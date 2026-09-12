"""Shared deterministic evidence filtering and ordering behavior."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
import json

from contracts.evidence import EvidenceFilter, EvidenceRecord
from evidence.repositories.ports import EvidenceOrder, RepositoryValidationError


def matches(record: EvidenceRecord, criteria: EvidenceFilter) -> bool:
    if record.incident_id != criteria.incident_id:
        return False
    if criteria.evidence_ids and record.evidence_id not in criteria.evidence_ids:
        return False
    if criteria.source_types and record.source_type not in criteria.source_types:
        return False
    if criteria.evidence_types and record.evidence_type not in criteria.evidence_types:
        return False
    if criteria.service is not None and record.service != criteria.service:
        return False
    if record.event_time is None:
        return criteria.include_unknown_event_time
    if criteria.start_time is not None and record.event_time < criteria.start_time:
        return False
    if criteria.end_time is not None and record.event_time > criteria.end_time:
        return False
    return True


def sort_records(
    records: list[EvidenceRecord], order: EvidenceOrder
) -> list[EvidenceRecord]:
    # Unknown times remain last in either direction. IDs make ties deterministic.
    known = [record for record in records if record.event_time is not None]
    unknown = [record for record in records if record.event_time is None]
    known.sort(key=lambda record: (record.event_time, record.evidence_id))
    if order is EvidenceOrder.EVENT_TIME_DESC:
        # Stable two-pass sorting keeps IDs ascending inside equal timestamps.
        known.sort(key=lambda record: record.event_time, reverse=True)
    unknown.sort(key=lambda record: record.evidence_id)
    return known + unknown


def encode_cursor(order: EvidenceOrder, offset: int) -> str:
    payload = json.dumps(
        {"order": order.value, "offset": offset},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None, order: EvidenceOrder) -> int:
    if cursor is None:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(urlsafe_b64decode(padded.encode("ascii")))
        if value.get("order") != order.value:
            raise ValueError
        offset = value.get("offset")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError
        return offset
    except (
        AttributeError,
        Base64Error,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise RepositoryValidationError("invalid evidence page cursor") from error
