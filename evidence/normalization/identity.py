"""Replaceable identity resolution port and configuration-backed adapter."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Protocol

from contracts.common import require_string


@dataclass(frozen=True, slots=True)
class IdentityResolution:
    canonical_id: str
    matched: bool
    original: str


class IdentityResolver(Protocol):
    def resolve_service(self, value: str) -> IdentityResolution: ...

    def resolve_resource(self, value: str) -> IdentityResolution: ...


def _key(value: str) -> str:
    return require_string(value, "identity").strip().casefold()


class ConfigurationIdentityResolver(IdentityResolver):
    """Resolves aliases from immutable mappings and preserves unknown inputs."""

    def __init__(
        self,
        service_aliases: Mapping[str, str] | None = None,
        resource_aliases: Mapping[str, str] | None = None,
    ) -> None:
        self._service_aliases = self._prepare(service_aliases or {})
        self._resource_aliases = self._prepare(resource_aliases or {})

    @staticmethod
    def _prepare(values: Mapping[str, str]) -> Mapping[str, str]:
        prepared: dict[str, str] = {}
        for alias, canonical in values.items():
            canonical_id = require_string(canonical, "canonical_identity").strip()
            prepared[_key(alias)] = canonical_id
            prepared.setdefault(_key(canonical_id), canonical_id)
        return MappingProxyType(prepared)

    @staticmethod
    def _resolve(value: str, aliases: Mapping[str, str]) -> IdentityResolution:
        original = require_string(value, "identity").strip()
        canonical = aliases.get(_key(original))
        if canonical is None:
            return IdentityResolution(canonical_id=original, matched=False, original=original)
        return IdentityResolution(canonical_id=canonical, matched=True, original=original)

    def resolve_service(self, value: str) -> IdentityResolution:
        return self._resolve(value, self._service_aliases)

    def resolve_resource(self, value: str) -> IdentityResolution:
        return self._resolve(value, self._resource_aliases)
