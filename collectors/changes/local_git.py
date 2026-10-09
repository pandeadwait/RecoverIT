"""Local Git repository change adapter for live codebase inspection.

Implements SourceAdapter to collect real git commits, metadata, changed files,
and diff excerpts from a local Git repository on disk. Strictly read-only.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
import subprocess
from typing import Any

from collectors.base import SourceAdapter
from contracts.collection.schemas import EvidenceQuery, RawRecord, SourceCapability, SourceResult
from contracts.enums import SourceStatus, SourceType
from contracts.incident.schemas import IncidentSeed

logger = logging.getLogger(__name__)

COMMIT_DELIMITER = "---RECOVERIT_COMMIT_BOUNDARY---"


class LocalGitChangeAdapter(SourceAdapter):
    """Real read-only source adapter for querying Git commits from a local repository."""

    source_type: SourceType = SourceType.CHANGES
    adapter_name: str = "local_git"

    def __init__(
        self,
        repo_path: str | Path = ".",
        service_name: str | None = None,
        max_diff_lines: int = 100,
    ) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.service_name = service_name
        self.max_diff_lines = max_diff_lines

    def is_git_repository(self) -> bool:
        """Check whether repo_path is an existing Git repository."""
        return (self.repo_path / ".git").exists() or (self.repo_path / "HEAD").exists()

    def get_capability(self, incident: IncidentSeed | None = None) -> SourceCapability:
        """Return the capability descriptor for this local Git adapter."""
        is_avail = self.is_git_repository()
        return SourceCapability(
            source_type=SourceType.CHANGES,
            available=is_avail,
            supported_query_fields=[
                "service",
                "repository",
                "limit",
                "max_commits",
                "since",
                "until",
                "start_time",
                "end_time",
                "path",
                "paths",
                "files",
                "branch",
                "author",
            ],
            maximum_window_seconds=86400 * 30,
            maximum_items=100,
            adapter_name=self.adapter_name,
            unavailable_reason=None if is_avail else f"Path '{self.repo_path}' is not a valid Git repository.",
        )

    async def query(self, query: EvidenceQuery) -> SourceResult:
        """Execute query asynchronously against the local Git repository."""
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
        target_service = params.get("service") or self.service_name or "application"

        cmd = [
            "git",
            "log",
            f"--max-count={limit + 1}",
            f"--format={COMMIT_DELIMITER}%n%H%n%an%n%ae%n%aI%n%s%n%b",
            "--name-only",
        ]

        branch = params.get("branch")
        if branch:
            cmd.append(str(branch))

        author = params.get("author")
        if author:
            cmd.append(f"--author={author}")

        start_time = params.get("start_time") or params.get("since")
        if start_time:
            cmd.append(f"--since={start_time}")

        end_time = params.get("end_time") or params.get("until")
        if end_time:
            cmd.append(f"--until={end_time}")

        paths_filter: list[str] = []
        raw_paths = params.get("path") or params.get("paths") or params.get("files")
        if raw_paths:
            if isinstance(raw_paths, str):
                paths_filter.append(raw_paths)
            elif isinstance(raw_paths, (list, tuple)):
                paths_filter.extend(str(p) for p in raw_paths)

        if paths_filter:
            cmd.append("--")
            cmd.extend(paths_filter)

        try:
            result = subprocess.run(
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

        if result.returncode != 0:
            completed_at = datetime.now(timezone.utc)
            err_msg = result.stderr.strip() or f"git log exited with code {result.returncode}"
            return SourceResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.ERROR,
                truncated=False,
                records=[],
                warnings=[err_msg],
                started_at=started_at,
                completed_at=completed_at,
            )

        records: list[RawRecord] = []
        output = result.stdout
        commit_blocks = [b.strip() for b in output.split(COMMIT_DELIMITER) if b.strip()]

        truncated = len(commit_blocks) > limit
        if truncated:
            commit_blocks = commit_blocks[:limit]

        for block in commit_blocks:
            lines = block.splitlines()
            if len(lines) < 4:
                continue

            commit_sha = lines[0].strip()
            author_name = lines[1].strip()
            author_email = lines[2].strip()
            commit_time_str = lines[3].strip()
            subject = lines[4].strip() if len(lines) > 4 else ""

            body_lines: list[str] = []
            files_changed: list[str] = []
            parsing_files = False

            for line in lines[5:]:
                clean_line = line.strip()
                if not clean_line:
                    parsing_files = True
                    continue
                if parsing_files:
                    files_changed.append(clean_line)
                else:
                    body_lines.append(line)

            event_time = None
            try:
                event_time = datetime.fromisoformat(commit_time_str.replace("Z", "+00:00"))
            except Exception:
                pass

            diff_excerpt = self._fetch_diff_excerpt(commit_sha)

            payload: dict[str, Any] = {
                "commit_sha": commit_sha,
                "commit_hash": commit_sha,
                "author": author_name,
                "author_email": author_email,
                "committed_at": commit_time_str,
                "message": subject,
                "description": "\n".join(body_lines).strip(),
                "files_changed": files_changed,
                "changed_files": files_changed,
                "diff_excerpt": diff_excerpt,
                "service": target_service,
            }

            records.append(
                RawRecord(
                    source_record_id=f"commit-{commit_sha}",
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
            warnings.append(f"Result set truncated to limit of {limit} commits.")

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

    def _fetch_diff_excerpt(self, commit_sha: str) -> str:
        """Fetch a bounded diff excerpt for a specific commit."""
        try:
            diff_proc = subprocess.run(
                ["git", "show", "--format=", "--stat", "-p", commit_sha],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                check=False,
                timeout=5.0,
            )
            if diff_proc.returncode == 0:
                diff_lines = diff_proc.stdout.splitlines()
                if len(diff_lines) > self.max_diff_lines:
                    diff_excerpt = "\n".join(diff_lines[: self.max_diff_lines]) + "\n... [diff truncated]"
                else:
                    diff_excerpt = diff_proc.stdout
                return diff_excerpt
        except Exception:
            pass
        return ""
