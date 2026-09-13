"""File-based log source adapter for live application log inspection.

Implements LogSource to collect real application logs from local files on disk,
supporting both structured JSON lines and standard formatted text logs.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
from typing import Any

from collectors.interfaces import LogSource, SourceQuery, SourceResult
from contracts.collection.batch import QueryResult, RawRecord
from contracts.collection.capabilities import SourceCapability
from contracts.enums import SourceStatus, SourceType
from contracts.incident.seed import IncidentSeed

logger = logging.getLogger(__name__)

# Common timestamp patterns
ISO_TIMESTAMP_PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)
LOG_LEVEL_PATTERN = re.compile(
    r"\b(CRITICAL|FATAL|ERROR|WARN(?:ING)?|INFO|DEBUG)\b", re.IGNORECASE
)


class FileLogAdapter(LogSource):
    """Source adapter for querying application logs from a local file or directory."""

    source_type: SourceType = SourceType.LOGS
    adapter_name: str = "file-log-adapter"

    def __init__(
        self,
        log_path: str | Path,
        service_name: str | None = None,
        only_errors: bool = True,
    ) -> None:
        self.log_path = Path(log_path).resolve()
        self.service_name = service_name
        self.only_errors = only_errors

    def exists(self) -> bool:
        """Check whether the configured log path exists on disk."""
        return self.log_path.exists()

    def get_capability(self, incident: IncidentSeed | None = None) -> SourceCapability:
        """Return the capability descriptor for this log adapter."""
        return SourceCapability(
            source_type=SourceType.LOGS,
            available=self.exists(),
            supported_query_fields=[
                "service",
                "limit",
                "severity",
                "level",
                "start_time",
                "end_time",
                "query",
                "filter",
                "search",
                "keyword",
            ],
            maximum_window_seconds=86400,
            maximum_items=500,
        )

    def query(self, query: SourceQuery) -> SourceResult:
        """Read and parse log records from the target log file."""
        if not self.exists():
            return QueryResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.ERROR,
                records=[],
                warnings=[f"Log path '{self.log_path}' does not exist."],
            )

        params = query.parameters or {}
        limit = int(params.get("limit") or 100)
        target_service = params.get("service") or self.service_name or "application"

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
        rec_index = 1

        for file_path in files_to_read:
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    for line_num, raw_line in enumerate(f, start=1):
                        line = raw_line.strip()
                        if not line:
                            continue

                        record = self._parse_line(
                            line, file_path.name, line_num, target_service, rec_index
                        )
                        if record is not None:
                            records.append(record)
                            rec_index += 1
                            if len(records) >= limit:
                                break
            except Exception as exc:
                logger.error("Error reading log file %s: %s", file_path, exc)

            if len(records) >= limit:
                break

        return QueryResult(
            query_id=query.query_id,
            source_type=self.source_type,
            source_adapter=self.adapter_name,
            source_status=SourceStatus.OK,
            truncated=len(records) >= limit,
            records=records,
            warnings=[],
        )

    def _parse_line(
        self,
        line: str,
        filename: str,
        line_num: int,
        target_service: str,
        rec_index: int,
    ) -> RawRecord | None:
        """Parse a single log line into a canonical RawRecord."""
        # 1. Check if JSON log format
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                level = str(data.get("level") or data.get("severity") or "info").lower()
                if self.only_errors and level not in {"error", "critical", "fatal"}:
                    return None

                msg = str(data.get("message") or data.get("msg") or line)
                ts_raw = data.get("timestamp") or data.get("time") or data.get("event_time")
                event_time = self._parse_timestamp(str(ts_raw)) if ts_raw else datetime.now(timezone.utc)

                return RawRecord(
                    source_record_id=f"log-{filename}-{line_num}",
                    event_time=event_time,
                    observed_at=datetime.now(timezone.utc),
                    content_type="application_log",
                    payload={
                        "level": level,
                        "service": str(data.get("service") or target_service),
                        "message": msg,
                        "error_signature": data.get("error_signature") or self._extract_signature(msg),
                        "raw_line": line,
                    },
                )
            except Exception:
                pass

        # 2. Standard Text Log Parsing
        level_match = LOG_LEVEL_PATTERN.search(line)
        level = level_match.group(1).lower() if level_match else "info"
        if level in {"warning", "warn"}:
            level = "warn"

        if self.only_errors and level not in {"error", "critical", "fatal"}:
            return None

        # Parse timestamp if present
        ts_match = ISO_TIMESTAMP_PATTERN.search(line)
        event_time = (
            self._parse_timestamp(ts_match.group(1))
            if ts_match
            else datetime.now(timezone.utc)
        )

        return RawRecord(
            source_record_id=f"log-{filename}-{line_num}",
            event_time=event_time,
            observed_at=datetime.now(timezone.utc),
            content_type="application_log",
            payload={
                "level": level,
                "service": target_service,
                "message": line,
                "error_signature": self._extract_signature(line),
                "file": filename,
                "line": line_num,
            },
        )

    def _parse_timestamp(self, ts_str: str) -> datetime:
        """Safely parse various datetime strings into UTC."""
        try:
            cleaned = ts_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)

    def _extract_signature(self, text: str) -> str:
        """Derive a short error signature from a log message."""
        for word in text.split():
            if "Error" in word or "Exception" in word or "Timeout" in word:
                return re.sub(r"[^a-zA-Z0-9_]", "", word)
        return "log_error"
