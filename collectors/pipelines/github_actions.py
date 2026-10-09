"""GitHub Actions pipeline adapter for live CI/CD pipeline run inspection.

Implements SourceAdapter to query workflow runs and statuses via the GitHub REST API.
Strictly read-only.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from typing import Any

import httpx

from collectors.base import SourceAdapter
from contracts.collection.schemas import EvidenceQuery, RawRecord, SourceCapability, SourceResult
from contracts.enums import SourceStatus, SourceType
from contracts.incident.schemas import IncidentSeed

logger = logging.getLogger(__name__)


class GitHubActionsPipelineAdapter(SourceAdapter):
    """Real read-only source adapter for querying GitHub Actions workflow runs."""

    source_type: SourceType = SourceType.PIPELINES
    adapter_name: str = "github_actions"

    def __init__(
        self,
        repository: str | None = None,
        github_token: str | None = None,
        api_url: str = "https://api.github.com",
        timeout_seconds: float = 10.0,
    ) -> None:
        self.repository = repository or os.environ.get("GITHUB_REPOSITORY")
        self.github_token = github_token or os.environ.get("GITHUB_TOKEN")
        self.api_url = (api_url or os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
        self.timeout_seconds = timeout_seconds

    def is_configured(self) -> bool:
        """Check whether repository is configured."""
        return bool(self.repository)

    def get_capability(self, incident: IncidentSeed | None = None) -> SourceCapability:
        """Return the capability descriptor for this GitHub Actions adapter."""
        is_avail = self.is_configured()
        return SourceCapability(
            source_type=SourceType.PIPELINES,
            available=is_avail,
            supported_query_fields=[
                "repository",
                "branch",
                "commit_sha",
                "sha",
                "status",
                "conclusion",
                "start_time",
                "end_time",
                "since",
                "until",
                "limit",
                "workflow_name",
                "pipeline",
                "service",
            ],
            maximum_window_seconds=86400 * 30,
            maximum_items=100,
            adapter_name=self.adapter_name,
            unavailable_reason=None if is_avail else "GitHub repository is not configured (set GITHUB_REPOSITORY).",
        )

    async def query(self, query: EvidenceQuery) -> SourceResult:
        """Query GitHub Actions workflow runs asynchronously via HTTP API."""
        started_at = datetime.now(timezone.utc)
        if not self.is_configured():
            completed_at = datetime.now(timezone.utc)
            return SourceResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.UNAVAILABLE,
                truncated=False,
                records=[],
                warnings=["GitHub Actions repository is not configured."],
                started_at=started_at,
                completed_at=completed_at,
            )

        params = query.parameters or {}
        limit = int(params.get("limit") or 20)
        target_repo = params.get("repository") or self.repository

        url = f"{self.api_url}/repos/{target_repo}/actions/runs"

        req_params: dict[str, Any] = {"per_page": min(limit + 5, 100)}
        branch = params.get("branch")
        if branch:
            req_params["branch"] = str(branch)

        status_filter = params.get("status")
        if status_filter:
            req_params["status"] = str(status_filter)

        sha_filter = params.get("commit_sha") or params.get("sha")
        pipeline_name = params.get("workflow_name") or params.get("pipeline")

        start_time = None
        start_raw = params.get("start_time") or params.get("since")
        if start_raw:
            try:
                start_time = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
            except Exception:
                pass

        end_time = None
        end_raw = params.get("end_time") or params.get("until")
        if end_raw:
            try:
                end_time = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
            except Exception:
                pass

        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"

        client = getattr(self, "_client", None)
        close_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout_seconds)
            close_client = True

        try:
            if True:
                resp = await client.get(url, params=req_params, headers=headers)

                if resp.status_code != 200:
                    completed_at = datetime.now(timezone.utc)
                    return SourceResult(
                        query_id=query.query_id,
                        source_type=self.source_type,
                        source_adapter=self.adapter_name,
                        source_status=SourceStatus.ERROR,
                        truncated=False,
                        records=[],
                        warnings=[f"GitHub API HTTP {resp.status_code}: {resp.text[:200]}"],
                        started_at=started_at,
                        completed_at=completed_at,
                    )

                body = resp.json()
                raw_runs = body.get("workflow_runs", [])

                records: list[RawRecord] = []
                truncated = False

                for run in raw_runs:
                    if truncated:
                        break

                    name = run.get("name") or "Workflow"
                    if pipeline_name and pipeline_name.lower() not in name.lower():
                        continue

                    target_service = params.get("service")
                    if target_service:
                        svc_lower = str(target_service).lower()
                        if (
                            svc_lower not in name.lower()
                            and svc_lower not in str(run.get("head_branch", "")).lower()
                            and svc_lower not in str(run.get("path", "")).lower()
                            and svc_lower not in target_repo.lower()
                        ):
                            continue

                    pattern = params.get("pattern")
                    if pattern:
                        pat_lower = str(pattern).lower()
                        if pat_lower not in name.lower() and pat_lower not in str(run).lower():
                            continue

                    req_path = params.get("path")
                    if req_path:
                        path_lower = str(req_path).lower()
                        if path_lower not in str(run.get("path", "")).lower():
                            continue

                    head_sha = run.get("head_sha", "")
                    if sha_filter and not head_sha.startswith(str(sha_filter)):
                        continue

                    conclusion = run.get("conclusion")
                    req_conclusion = params.get("conclusion")
                    if req_conclusion and conclusion != req_conclusion:
                        continue

                    created_str = run.get("created_at") or run.get("run_started_at")
                    event_time = None
                    if created_str:
                        try:
                            event_time = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                        except Exception:
                            pass

                    if event_time is not None:
                        if start_time and event_time < start_time:
                            continue
                        if end_time and event_time > end_time:
                            continue

                    run_id = str(run.get("id"))
                    rec_id = f"pipeline-run-{run_id}"

                    jobs_info: list[dict[str, Any]] = []
                    if conclusion == "failure":
                        try:
                            jobs_url = f"{self.api_url}/repos/{target_repo}/actions/runs/{run_id}/jobs"
                            jobs_resp = await client.get(jobs_url, headers=headers)
                            if jobs_resp.status_code == 200:
                                jobs_data = jobs_resp.json()
                                for j in jobs_data.get("jobs", []):
                                    failed_steps = [
                                        s.get("name")
                                        for s in j.get("steps", [])
                                        if s.get("conclusion") == "failure"
                                    ]
                                    jobs_info.append({
                                        "id": j.get("id"),
                                        "name": j.get("name"),
                                        "conclusion": j.get("conclusion"),
                                        "failed_steps": failed_steps,
                                    })
                        except Exception:
                            pass

                    payload = {
                        "run_id": run.get("id"),
                        "workflow_name": name,
                        "status": run.get("status"),
                        "conclusion": conclusion,
                        "head_sha": head_sha,
                        "head_branch": run.get("head_branch"),
                        "event": run.get("event"),
                        "html_url": run.get("html_url"),
                        "run_attempt": run.get("run_attempt"),
                        "created_at": created_str,
                        "repository": target_repo,
                        "jobs": jobs_info,
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

                    if len(records) > limit:
                        records.pop()
                        truncated = True
                        break

                completed_at = datetime.now(timezone.utc)
                status = SourceStatus.EMPTY if len(records) == 0 else SourceStatus.OK
                warnings: list[str] = []
                if truncated:
                    warnings.append(f"Result set truncated to limit of {limit} pipeline runs.")

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

        except Exception as exc:
            completed_at = datetime.now(timezone.utc)
            logger.warning("GitHub Actions query failed: %s", exc)
            return SourceResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.ERROR,
                truncated=False,
                records=[],
                warnings=[f"GitHub Actions request failed: {type(exc).__name__}: {str(exc)}"],
                started_at=started_at,
                completed_at=completed_at,
            )
        finally:
            if close_client:
                await client.aclose()

