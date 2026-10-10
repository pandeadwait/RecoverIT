"""File-based log source adapter for live application log inspection.

Implements SourceAdapter to collect real application logs from local files on disk,
supporting structured JSON lines and standard formatted text logs. Strictly read-only.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from collectors.base import SourceAdapter
from contracts.collection.schemas import EvidenceQuery, RawRecord, SourceCapability, SourceResult
from contracts.enums import SourceStatus, SourceType
from contracts.incident.schemas import IncidentSeed

logger = logging.getLogger(__name__)

ISO_TIMESTAMP_PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)
LOG_LEVEL_PATTERN = re.compile(
    r"\b(CRITICAL|FATAL|ERROR|WARN(?:ING)?|INFO|DEBUG)\b", re.IGNORECASE
)
SERVICE_BRACKET_PATTERN = re.compile(r"\[([a-zA-Z0-9_\-]+)\]")


def _parse_log_timestamp(value: object, assumed_timezone: ZoneInfo) -> datetime | None:
    """Parse a log timestamp and return an aware UTC datetime.

    File logs commonly omit a timezone (for example, Python's standard
    ``YYYY-MM-DD HH:MM:SS,mmm`` format). The file adapter treats those local
    timestamps using the source's configured timezone. Explicit offsets are
    converted to UTC unchanged.
    """
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=assumed_timezone).astimezone(timezone.utc)
    return parsed.astimezone(timezone.utc)



class FileLogAdapter(SourceAdapter):
    """Real read-only source adapter for querying application logs from local files."""

    source_type: SourceType = SourceType.LOGS
    adapter_name: str = "file_log"

    def __init__(
        self,
        log_path: str | Path,
        service_name: str | None = None,
        only_errors: bool = False,
        timestamp_timezone: str = "UTC",
    ) -> None:
        self.log_path = Path(log_path).resolve()
        self.service_name = service_name
        self.only_errors = only_errors
        try:
            self.timestamp_timezone = ZoneInfo(timestamp_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"Unknown timestamp_timezone '{timestamp_timezone}'. Use an IANA timezone "
                "such as 'UTC' or 'Asia/Kolkata'."
            ) from exc

    def exists(self) -> bool:
        """Check whether the configured log file or directory exists."""
        return self.log_path.exists()

    def get_capability(self, incident: IncidentSeed | None = None) -> SourceCapability:
        """Return the capability descriptor for this log adapter."""
        is_avail = self.exists()
        return SourceCapability(
            source_type=SourceType.LOGS,
            available=is_avail,
            supported_query_fields=[
                "service",
                "limit",
                "severity",
                "level",
                "start_time",
                "end_time",
                "since",
                "until",
                "pattern",
                "query",
                "search",
                "correlation_id",
            ],
            maximum_window_seconds=86400,
            maximum_items=1000,
            adapter_name=self.adapter_name,
            unavailable_reason=None if is_avail else f"Log path '{self.log_path}' does not exist on disk.",
        )

    async def query(self, query: EvidenceQuery) -> SourceResult:
        """Execute query asynchronously against the target log files."""
        return await asyncio.to_thread(self._query_sync, query)

    def _query_sync(self, query: EvidenceQuery) -> SourceResult:
        started_at = datetime.now(timezone.utc)
        if not self.exists():
            completed_at = datetime.now(timezone.utc)
            return SourceResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.UNAVAILABLE,
                truncated=False,
                records=[],
                warnings=[f"Log path '{self.log_path}' does not exist."],
                started_at=started_at,
                completed_at=completed_at,
            )

        params = query.parameters or {}
        limit = int(params.get("limit") or 100)
        filter_service = params.get("service") or self.service_name
        default_service = filter_service or "application"


        files_to_read: list[Path] = []
        if self.log_path.is_dir():
            files_to_read = sorted(
                list(self.log_path.glob("*.log")) + list(self.log_path.glob("*.txt")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        else:
            files_to_read = [self.log_path]

        records: list[RawRecord] = []
        pattern_str = params.get("pattern") or params.get("query") or params.get("search")
        regex = None
        if pattern_str:
            try:
                regex = re.compile(str(pattern_str), re.IGNORECASE)
            except re.error:
                regex = None

        desired_level = params.get("severity") or params.get("level")
        if desired_level:
            if isinstance(desired_level, str):
                desired_level = {desired_level.lower()}
            else:
                desired_level = {str(x).lower() for x in desired_level}

        correlation_id = params.get("correlation_id")

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

        truncated = False

        for fpath in files_to_read:
            if truncated:
                break
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for line_num, line in enumerate(f, start=1):
                        clean_line = line.strip()
                        if not clean_line:
                            continue

                        parsed_record = self._parse_log_line(clean_line, fpath.name, line_num, default_service)
                        if parsed_record is None:
                            continue

                        payload = parsed_record.payload
                        msg = payload.get("message", "")
                        lvl = payload.get("level", "info").lower()

                        if filter_service:
                            line_service = payload.get("service")
                            if line_service and line_service != filter_service and filter_service not in clean_line:
                                continue


                        if self.only_errors and lvl not in {"error", "critical", "fatal"}:
                            continue

                        if desired_level and lvl not in desired_level:
                            continue


                        if correlation_id and correlation_id not in clean_line:
                            continue

                        if regex and not regex.search(clean_line):
                            continue
                        elif pattern_str and not regex and pattern_str.lower() not in clean_line.lower():
                            continue

                        event_time = parsed_record.event_time
                        if event_time is not None:
                            if start_time and event_time < start_time:
                                continue
                            if end_time and event_time > end_time:
                                continue

                        records.append(parsed_record)
                        if len(records) > limit:
                            records.pop()
                            truncated = True
                            break
            except Exception as exc:
                logger.warning("Error reading log file %s: %s", fpath, exc)

        completed_at = datetime.now(timezone.utc)
        status = SourceStatus.EMPTY if len(records) == 0 else SourceStatus.OK
        warnings: list[str] = []
        if truncated:
            warnings.append(f"Result set truncated to limit of {limit} log records.")

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

    def _parse_log_line(self, line: str, filename: str, line_num: int, default_service: str) -> RawRecord | None:
        rec_id = f"log-{filename}-{line_num}"
        now_utc = datetime.now(timezone.utc)

        # 1. Try structured JSON log
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    msg = data.get("message") or data.get("msg") or line
                    lvl = str(data.get("level") or data.get("severity") or "info").lower()
                    ts_val = data.get("timestamp") or data.get("time") or data.get("@timestamp")
                    parsed_time = None
                    if ts_val:
                        parsed_time = _parse_log_timestamp(ts_val, self.timestamp_timezone)

                    payload = {
                        "message": str(msg),
                        "level": lvl,
                        "service": str(data.get("service") or default_service),
                        "source_file": filename,
                        "line_number": line_num,
                        "raw": line,
                    }
                    for k, v in data.items():
                        if k not in payload and isinstance(v, (str, int, float, bool, list, dict)):
                            payload[k] = v

                    return RawRecord(
                        source_record_id=rec_id,
                        event_time=parsed_time,
                        observed_at=now_utc,
                        content_type="application/json",
                        payload=payload,
                    )
            except Exception:
                pass

        # 2. Plain text log
        time_match = ISO_TIMESTAMP_PATTERN.search(line)
        parsed_time = None
        if time_match:
            parsed_time = _parse_log_timestamp(time_match.group(1), self.timestamp_timezone)

        level_match = LOG_LEVEL_PATTERN.search(line)
        level_str = level_match.group(1).lower() if level_match else "info"

        bracket_match = SERVICE_BRACKET_PATTERN.search(line)
        line_service = bracket_match.group(1) if bracket_match else default_service

        return RawRecord(
            source_record_id=rec_id,
            event_time=parsed_time,
            observed_at=now_utc,
            content_type="text/plain",
            payload={
                "message": line,
                "raw": line,
                "level": level_str,
                "service": line_service,
                "source_file": filename,
                "line_number": line_num,
            },
        )

