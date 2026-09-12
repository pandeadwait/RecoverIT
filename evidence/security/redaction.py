"""Recursive field and free-text redaction with quarantine decisions."""

from __future__ import annotations

from base64 import b64decode
from binascii import Error as Base64Error
from dataclasses import dataclass
import re
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import unquote

from contracts.common import thaw_json
from evidence.security.models import RedactionOutcome, RedactionResult


class RedactionPolicy(Protocol):
    def redact(self, value: object) -> RedactionResult: ...


_SENSITIVE_FIELD = re.compile(
    r"(?:^|[_-])(?:password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key|"
    r"client[_-]?secret|access[_-]?key|database[_-]?(?:url|uri)|db[_-]?(?:url|uri)|"
    r"connection[_-]?string)(?:$|[_-])",
    re.IGNORECASE,
)
_SECRET_ENV_FIELD = re.compile(
    r"^(?:AWS_SECRET_ACCESS_KEY|AZURE_CLIENT_SECRET|GOOGLE_APPLICATION_CREDENTIALS|"
    r"DATABASE_URL|DB_PASSWORD|.*(?:_SECRET|_TOKEN|_PASSWORD|_API_KEY))$",
    re.IGNORECASE,
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_DATABASE_URL = re.compile(
    r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|mssql)://[^\s'\"<>]+"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_TOKEN_VALUE = re.compile(
    r"\b[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b"
)
_AWS_ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pwd|api[_-]?key|client[_-]?secret|access[_-]?token|"
    r"refresh[_-]?token|aws[_-]?secret[_-]?access[_-]?key)\s*[:=]\s*([^\s,;]+)"
)


@dataclass(frozen=True, slots=True)
class PatternRedactionPolicy(RedactionPolicy):
    personal_identifiers: tuple[str, ...] = ()
    sensitive_field_names: tuple[str, ...] = ()
    quarantine_private_keys: bool = True
    inspect_encoded_values: bool = True

    def redact(self, value: object) -> RedactionResult:
        return Redactor(self).redact(value)


class Redactor:
    """Reference recursive redactor; applications may replace the policy port."""

    def __init__(self, policy: PatternRedactionPolicy | None = None) -> None:
        self._policy = policy or PatternRedactionPolicy()
        self._personal_patterns = tuple(
            re.compile(re.escape(value), re.IGNORECASE)
            for value in self._policy.personal_identifiers
            if value
        )
        self._configured_fields = {
            value.strip().casefold()
            for value in self._policy.sensitive_field_names
            if value.strip()
        }

    def redact(self, value: object) -> RedactionResult:
        sanitized, paths, private_key_found = self._walk(value, "$")
        if private_key_found and self._policy.quarantine_private_keys:
            outcome = RedactionOutcome.QUARANTINE
            reason = "private_key_material"
        elif paths:
            outcome = RedactionOutcome.REDACT
            reason = None
        else:
            outcome = RedactionOutcome.PASS
            reason = None
        return RedactionResult(
            outcome=outcome,
            value=sanitized,
            redacted_paths=tuple(sorted(set(paths))),
            reason_code=reason,
        )

    def _walk(self, value: object, path: str) -> tuple[Any, list[str], bool]:
        value = thaw_json(value)  # type: ignore[arg-type]
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            paths: list[str] = []
            private_key_found = False
            for index, (raw_key, nested) in enumerate(value.items()):
                key = str(raw_key)
                sanitized_key, key_changed, key_private = self._redact_text(key)
                if sanitized_key in result:
                    sanitized_key = f"[REDACTED_KEY_{index}]"
                    key_changed = True
                nested_path = f"{path}.{sanitized_key}"
                if self._is_sensitive_field(key):
                    result[sanitized_key] = "[REDACTED]"
                    paths.append(nested_path)
                    _, _, nested_private = self._walk(nested, nested_path)
                    private_key_found = private_key_found or nested_private
                else:
                    sanitized, nested_paths, nested_private = self._walk(nested, nested_path)
                    result[sanitized_key] = sanitized
                    paths.extend(nested_paths)
                    private_key_found = private_key_found or nested_private
                if key_changed:
                    paths.append(f"{path}.[REDACTED_KEY]")
                private_key_found = private_key_found or key_private
            return result, paths, private_key_found
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            result_items: list[Any] = []
            paths: list[str] = []
            private_key_found = False
            for index, nested in enumerate(value):
                sanitized, nested_paths, nested_private = self._walk(nested, f"{path}[{index}]")
                result_items.append(sanitized)
                paths.extend(nested_paths)
                private_key_found = private_key_found or nested_private
            return result_items, paths, private_key_found
        if isinstance(value, str):
            sanitized, changed, private_key_found = self._redact_text(value)
            return sanitized, ([path] if changed else []), private_key_found
        return value, [], False

    def _is_sensitive_field(self, key: str) -> bool:
        normalized = key.strip().casefold()
        return bool(
            normalized in self._configured_fields
            or _SENSITIVE_FIELD.search(normalized)
            or _SECRET_ENV_FIELD.fullmatch(key.strip())
        )

    def _redact_text(self, value: str) -> tuple[str, bool, bool]:
        private_key_found = bool(_PRIVATE_KEY.search(value))
        result = _PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", value)
        result = _DATABASE_URL.sub("[REDACTED_DATABASE_URL]", result)
        result = _BEARER.sub("Bearer [REDACTED]", result)
        result = _TOKEN_VALUE.sub("[REDACTED_TOKEN]", result)
        result = _AWS_ACCESS_KEY.sub("[REDACTED_AWS_ACCESS_KEY]", result)
        result = _ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", result)
        for pattern in self._personal_patterns:
            result = pattern.sub("[REDACTED_PERSONAL_IDENTIFIER]", result)

        if self._policy.inspect_encoded_values and result == value:
            decoded_url = unquote(value)
            if decoded_url != value and self._contains_sensitive(decoded_url):
                return (
                    "[REDACTED_ENCODED_VALUE]",
                    True,
                    private_key_found or bool(_PRIVATE_KEY.search(decoded_url)),
                )
            decoded_base64 = self._decode_base64(value)
            if decoded_base64 is not None and self._contains_sensitive(decoded_base64):
                return (
                    "[REDACTED_ENCODED_VALUE]",
                    True,
                    private_key_found or bool(_PRIVATE_KEY.search(decoded_base64)),
                )
        return result, result != value, private_key_found

    def _contains_sensitive(self, value: str) -> bool:
        return bool(
            _PRIVATE_KEY.search(value)
            or _DATABASE_URL.search(value)
            or _BEARER.search(value)
            or _TOKEN_VALUE.search(value)
            or _AWS_ACCESS_KEY.search(value)
            or _ASSIGNMENT.search(value)
            or any(pattern.search(value) for pattern in self._personal_patterns)
        )

    @staticmethod
    def _decode_base64(value: str) -> str | None:
        compact = value.strip()
        if len(compact) < 12 or len(compact) % 4 != 0:
            return None
        try:
            return b64decode(compact, validate=True).decode("utf-8")
        except (Base64Error, UnicodeDecodeError, ValueError):
            return None
