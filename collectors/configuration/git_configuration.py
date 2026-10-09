"""Git configuration adapter for live versioned configuration inspection.

Implements SourceAdapter to query configuration file modifications and diffs
from Git history with automatic secret redaction. Strictly read-only.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
import re
import subprocess
from typing import Any

from collectors.base import SourceAdapter
from contracts.collection.schemas import EvidenceQuery, RawRecord, SourceCapability, SourceResult
from contracts.enums import SourceStatus, SourceType
from contracts.incident.schemas import IncidentSeed

logger = logging.getLogger(__name__)

COMMIT_DELIMITER = "---RECOVERIT_CONFIG_COMMIT---"

# Regex patterns for detecting and redacting sensitive values in configuration diffs
SECRET_PATTERNS = [
    re.compile(r"""(?i)(["']?(?:password|passwd|secret|api_key|token|auth_token|access_key|private_key)["']?\s*[:=]\s*["']?)([^"' \n\r\t,]+)(["']?)"""),
    re.compile(r"""(?i)(Bearer\s+)([A-Za-z0-9_\-\.]{10,})"""),
    re.compile(r"""(?i)(ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})"""),
]

DEFAULT_CONFIG_GLOBS = [
    "config/",
    "conf/",
    "*.json",
    "*.yaml",
    "*.yml",
    "*.env.example",
    "*.toml",
    "*.ini",
]


class GitConfigurationAdapter(SourceAdapter):
    """Real read-only source adapter for versioned configuration files with secret redaction."""

    source_type: SourceType = SourceType.CONFIGURATION
    adapter_name: str = "git_configuration"

    def __init__(
        self,
        repo_path: str | Path = ".",
        config_paths: list[str] | None = None,
        max_diff_lines: int = 100,
    ) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.config_paths = config_paths or list(DEFAULT_CONFIG_GLOBS)
        self.max_diff_lines = max_diff_lines

    def is_git_repository(self) -> bool:
        """Check whether repo_path is an existing Git repository."""
        return (self.repo_path / ".git").exists() or (self.repo_path / "HEAD").exists()

    def get_capability(self, incident: IncidentSeed | None = None) -> SourceCapability:
        """Return the capability descriptor for this configuration adapter."""
        is_avail = self.is_git_repository()
        return SourceCapability(
            source_type=SourceType.CONFIGURATION,
            available=is_avail,
            supported_query_fields=[
                "key",
                "service",
                "path",
                "paths",
                "start_time",
                "end_time",
                "since",
                "until",
                "limit",
                "max_commits",
                "branch",
            ],
            maximum_window_seconds=86400 * 30,
            maximum_items=100,
            adapter_name=self.adapter_name,
            unavailable_reason=None if is_avail else f"Path '{self.repo_path}' is not a valid Git repository.",
        )

    async def query(self, query: EvidenceQuery) -> SourceResult:
        """Query configuration changes asynchronously from Git history."""
        return await asyncio.to_thread(self._query_sync, query)

    def _query_sync(self, query: EvidenceQuery) -> SourceResult:
        started_at = datetime.now(timezone.utc)
        if not self.is_git_repository():
            completed_at = datetime.now(timezone.utc)
            return SourceResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.UNAVAILABLE,
                truncated=False,
                records=[],
                warnings=[f"Path '{self.repo_path}' is not a valid Git repository."],
                started_at=started_at,
                completed_at=completed_at,
            )

        params = query.parameters or {}
        limit = int(params.get("max_commits") or params.get("limit") or 10)
        target_service = params.get("service") or "application"
        target_key = params.get("key")

        # Determine path filters
        path_filters: list[str] = []
        raw_paths = params.get("path") or params.get("paths")
        if raw_paths:
            if isinstance(raw_paths, str):
                path_filters.append(raw_paths)
            elif isinstance(raw_paths, (list, tuple)):
                path_filters.extend(str(p) for p in raw_paths)
        else:
            path_filters = list(self.config_paths)

        cmd = [
            "git",
            "log",
            f"--max-count={limit + 1}",
            f"--format={COMMIT_DELIMITER}%n%H%n%an%n%ae%n%aI%n%s",
            "--name-only",
        ]

        start_time = params.get("start_time") or params.get("since")
        if start_time:
            cmd.append(f"--since={start_time}")

        end_time = params.get("end_time") or params.get("until")
        if end_time:
            cmd.append(f"--until={end_time}")

        branch = params.get("branch")
        if branch:
            cmd.append(str(branch))

        cmd.append("--")
        cmd.extend(path_filters)

        try:
            res = subprocess.run(
                cmd,
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                check=False,
                timeout=15.0,
            )
        except Exception as exc:
            completed_at = datetime.now(timezone.utc)
            return SourceResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.ERROR,
                truncated=False,
                records=[],
                warnings=[f"Git log execution failed: {type(exc).__name__}: {str(exc)}"],
                started_at=started_at,
                completed_at=completed_at,
            )

        if res.returncode != 0:
            completed_at = datetime.now(timezone.utc)
            return SourceResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.ERROR,
                truncated=False,
                records=[],
                warnings=[res.stderr.strip() or f"git log exited with code {res.returncode}"],
                started_at=started_at,
                completed_at=completed_at,
            )

        commit_blocks = [b.strip() for b in res.stdout.split(COMMIT_DELIMITER) if b.strip()]
        truncated = len(commit_blocks) > limit
        if truncated:
            commit_blocks = commit_blocks[:limit]

        records: list[RawRecord] = []

        for block in commit_blocks:
            lines = block.splitlines()
            if len(lines) < 4:
                continue

            commit_sha = lines[0].strip()
            author_name = lines[1].strip()
            author_email = lines[2].strip()
            commit_time_str = lines[3].strip()
            subject = lines[4].strip() if len(lines) > 4 else ""

            config_files = [line.strip() for line in lines[5:] if line.strip()]

            event_time = None
            try:
                event_time = datetime.fromisoformat(commit_time_str.replace("Z", "+00:00"))
            except Exception:
                pass

            diff_excerpt = self._fetch_redacted_diff(commit_sha, path_filters)

            # If key filter requested, check if key is present in diff or commit message
            if target_key and target_key.lower() not in diff_excerpt.lower() and target_key.lower() not in subject.lower():
                continue

            rec_id = f"config-{commit_sha[:12]}-{Path(config_files[0]).name if config_files else 'generic'}"

            payload = {
                "commit_sha": commit_sha,
                "author": author_name,
                "author_email": author_email,
                "committed_at": commit_time_str,
                "message": subject,
                "files_changed": config_files,
                "diff_excerpt": diff_excerpt,
                "diff": diff_excerpt,
                "service": target_service,
            }

            records.append(
                RawRecord(
                    source_record_id=rec_id,
                    event_time=event_time,
                    observed_at=datetime.now(timezone.utc),
                    content_type="application/json",
                    payload=payload,
                )
            )

        completed_at = datetime.now(timezone.utc)
        status = SourceStatus.EMPTY if len(records) == 0 else SourceStatus.OK
        warnings: list[str] = []
        if truncated:
            warnings.append(f"Result set truncated to limit of {limit} configuration changes.")

        return SourceResult(
            query_id=query.query_id,
            source_type=self.source_type,
            source_adapter=self.adapter_name,
            source_status=status,
            truncated=truncated,
            records=records,
            warnings=warnings,
            started_at=started_at,
            completed_at=completed_at,
        )

    def _fetch_redacted_diff(self, commit_sha: str, path_filters: list[str]) -> str:
        """Fetch and redact diff excerpt for a commit affecting configuration."""
        cmd = ["git", "show", "--format=", "-p", commit_sha, "--"]
        cmd.extend(path_filters)
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                check=False,
                timeout=5.0,
            )
            if proc.returncode == 0:
                raw_diff = proc.stdout
                redacted = self.redact_secrets(raw_diff)
                diff_lines = redacted.splitlines()
                if len(diff_lines) > self.max_diff_lines:
                    return "\n".join(diff_lines[: self.max_diff_lines]) + "\n... [diff truncated]"
                return redacted
        except Exception:
            pass
        return ""

    @staticmethod
    def redact_secrets(text: str) -> str:
        """Redact known secret patterns from configuration text or diffs."""
        out = text
        for pat in SECRET_PATTERNS:
            if pat.groups == 3:
                out = pat.sub(r"\1[REDACTED]\3", out)
            elif pat.groups == 2:
                out = pat.sub(r"\1[REDACTED]", out)
            else:
                out = pat.sub("[REDACTED]", out)
        return out
