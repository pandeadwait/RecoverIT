"""Local Git repository change adapter for live codebase inspection.

Implements ChangeSource to collect real git commits, metadata, and diffs
from a local git repository on disk.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import subprocess
from typing import Any

from collectors.interfaces import ChangeSource, SourceQuery, SourceResult
from contracts.collection.batch import QueryResult, RawRecord
from contracts.collection.capabilities import SourceCapability
from contracts.enums import SourceStatus, SourceType
from contracts.incident.seed import IncidentSeed

logger = logging.getLogger(__name__)

COMMIT_DELIMITER = "---RECOVERIT_COMMIT_BOUNDARY---"


class LocalGitChangeAdapter(ChangeSource):
    """Source adapter for querying git commit history from a local Git directory."""

    source_type: SourceType = SourceType.CHANGES
    adapter_name: str = "local-git-change-adapter"

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
        """Check whether the configured repo_path is a valid Git repository."""
        return (self.repo_path / ".git").exists() or (self.repo_path / "HEAD").exists()

    def ensure_git_initialized(self) -> bool:
        """Initialize and commit files if repo_path contains source code but no git repository."""
        if self.is_git_repository():
            return True
        try:
            subprocess.run(["git", "init"], cwd=str(self.repo_path), capture_output=True, check=True)
            subprocess.run(["git", "config", "user.email", "ci-bot@recoverit.local"], cwd=str(self.repo_path), capture_output=True)
            subprocess.run(["git", "config", "user.name", "RecoverIT CI Bot"], cwd=str(self.repo_path), capture_output=True)
            subprocess.run(["git", "add", "."], cwd=str(self.repo_path), capture_output=True)
            subprocess.run(["git", "commit", "-m", "Initial commit: service core logic and config"], cwd=str(self.repo_path), capture_output=True)
            return self.is_git_repository()
        except Exception:
            return False

    def get_capability(self, incident: IncidentSeed | None = None) -> SourceCapability:
        """Return the capability descriptor for this local Git adapter."""
        available = self.is_git_repository() or self.ensure_git_initialized()
        return SourceCapability(
            source_type=SourceType.CHANGES,
            available=available,
            supported_query_fields=[
                "service",
                "limit",
                "max_commits",
                "since",
                "path",
                "start_time",
                "end_time",
                "query",
                "filter",
                "branch",
                "author",
            ],
            maximum_window_seconds=86400 * 30,
            maximum_items=100,
        )

    def query(self, query: SourceQuery) -> SourceResult:
        """Execute query by running git log on the local repository."""
        if not self.is_git_repository():
            return QueryResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.ERROR,
                records=[],
                warnings=[f"Path '{self.repo_path}' is not a valid Git repository."],
            )

        params = query.parameters or {}
        limit = int(params.get("max_commits") or params.get("limit") or 10)
        target_service = params.get("service") or self.service_name or "application"

        records: list[RawRecord] = []
        warnings: list[str] = []

        try:
            # Format: commit hash, author name <email>, commit date ISO, subject, body
            format_str = f"{COMMIT_DELIMITER}%n%H%n%an <%ae>%n%aI%n%s%n%b"
            cmd = [
                "git",
                "log",
                f"-n{limit}",
                f"--format={format_str}",
                "--stat",
            ]

            path_filter = params.get("path")
            if path_filter:
                cmd.extend(["--", str(path_filter)])

            result = subprocess.run(
                cmd,
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                errors="replace",
            )

            if result.returncode != 0:
                warnings.append(f"git log returned error: {result.stderr.strip()}")
                return QueryResult(
                    query_id=query.query_id,
                    source_type=self.source_type,
                    source_adapter=self.adapter_name,
                    source_status=SourceStatus.ERROR,
                    records=[],
                    warnings=warnings,
                )

            # Also fetch patch diff for recent commits
            commits_raw = result.stdout.split(COMMIT_DELIMITER)
            for raw_chunk in commits_raw:
                chunk = raw_chunk.strip()
                if not chunk:
                    continue

                lines = chunk.splitlines()
                if len(lines) < 4:
                    continue

                commit_sha = lines[0].strip()
                author = lines[1].strip()
                date_str = lines[2].strip()
                subject = lines[3].strip()

                # Parse remaining body and stat
                body_lines = []
                stat_lines = []
                in_stat = False
                for line in lines[4:]:
                    if "|" in line and ("+" in line or "-" in line):
                        in_stat = True
                    if in_stat:
                        stat_lines.append(line.strip())
                    else:
                        body_lines.append(line.strip())

                commit_msg = subject
                if body_lines:
                    full_body = " ".join(b for b in body_lines if b)
                    if full_body:
                        commit_msg = f"{subject} — {full_body}"

                files_changed = [
                    s.split("|")[0].strip()
                    for s in stat_lines
                    if "|" in s and s.split("|")[0].strip()
                ]

                # Fetch individual diff excerpt for this commit
                diff_excerpt = self._get_diff_excerpt(commit_sha)

                # Parse event_time with UTC timezone fallback
                event_time: datetime | None = None
                try:
                    event_time = datetime.fromisoformat(date_str).astimezone(timezone.utc)
                except Exception:
                    event_time = datetime.now(timezone.utc)

                record = RawRecord(
                    source_record_id=f"commit-{commit_sha[:12]}",
                    event_time=event_time,
                    observed_at=datetime.now(timezone.utc),
                    content_type="git_commit",
                    payload={
                        "commit_sha": commit_sha,
                        "author": author,
                        "message": commit_msg,
                        "files_changed": files_changed,
                        "diff_excerpt": diff_excerpt,
                        "service": target_service,
                    },
                )
                records.append(record)

        except Exception as exc:
            logger.error("Failed executing git query on %s: %s", self.repo_path, exc)
            return QueryResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.ERROR,
                records=[],
                warnings=[f"Exception running git log: {exc}"],
            )

        return QueryResult(
            query_id=query.query_id,
            source_type=self.source_type,
            source_adapter=self.adapter_name,
            source_status=SourceStatus.OK,
            truncated=False,
            records=records,
            warnings=warnings,
        )

    def _get_diff_excerpt(self, commit_sha: str) -> str:
        """Fetch unified diff for a given commit SHA, capped at max_diff_lines."""
        try:
            diff_res = subprocess.run(
                ["git", "show", "--format=", "--unified=3", commit_sha],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                errors="replace",
            )
            if diff_res.returncode == 0 and diff_res.stdout:
                diff_lines = diff_res.stdout.splitlines()[: self.max_diff_lines]
                return "\n".join(diff_lines)
        except Exception:
            pass
        return ""
