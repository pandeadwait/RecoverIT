"""Tests for the fixture-free LangGraph HTTP application boundary."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from contracts.errors.schemas import ProgressEvent
from recoverit.runner import InvestigationResult
from recoverit.web.live import create_live_app


NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


class RecordingExecutor:
    def __init__(self) -> None:
        self.run_incident_ids: list[str] = []
        self.resumed_incident_ids: list[str] = []

    async def run(self, incident, budget=None, progress_callback=None):
        self.run_incident_ids.append(incident.incident_id)
        if progress_callback is not None:
            progress_callback(
                ProgressEvent(
                    event_id="evt-live-1",
                    incident_id=incident.incident_id,
                    kind="status",
                    stage="initialize",
                    title="Investigation initialized",
                    created_at=NOW,
                )
            )
        return self._result(incident.incident_id, incident.service, incident.summary)

    async def resume(self, incident_id, progress_callback=None):
        self.resumed_incident_ids.append(incident_id)
        return self._result(incident_id, "resumed-service", "Resumed incident")

    @staticmethod
    def _result(incident_id: str, service: str, summary: str) -> InvestigationResult:
        return InvestigationResult(
            incident_id=incident_id,
            service=service,
            summary=summary,
            status="completed",
            stop_reason=None,
            execution_time_seconds=0.01,
            ranked_hypotheses=[],
            timeline_events=[],
            evidence_items=[],
            diff_excerpts={},
            log_excerpts=[],
            budget_usage={"rounds": 1, "queries": 0, "reasoning_calls": 0},
            provider_used="test-runtime",
        )


def incident_payload() -> dict[str, object]:
    return {
        "incident_id": "inc-unseen-service-42",
        "external_alert_id": "alert-42",
        "service": "inventory-reconciler",
        "environment": "staging",
        "severity": "critical",
        "detected_at": "2026-10-09T12:00:00Z",
        "received_at": "2026-10-09T12:00:05Z",
        "summary": "The reconciliation queue is delayed.",
        "labels": {"region": "ap-south-1"},
    }


def test_live_api_accepts_a_canonical_incident_without_scenario_fields() -> None:
    executor = RecordingExecutor()
    client = TestClient(create_live_app(executor))

    response = client.post("/api/investigations", json={"incident": incident_payload()})

    assert response.status_code == 200
    assert response.json()["incident_id"] == "inc-unseen-service-42"
    assert response.json()["service"] == "inventory-reconciler"
    assert executor.run_incident_ids == ["inc-unseen-service-42"]


def test_live_api_resumes_by_incident_id() -> None:
    executor = RecordingExecutor()
    client = TestClient(create_live_app(executor))

    response = client.post("/api/investigations/inc-checkpoint-7/resume")

    assert response.status_code == 200
    assert response.json()["incident_id"] == "inc-checkpoint-7"
    assert executor.resumed_incident_ids == ["inc-checkpoint-7"]


def test_live_api_streams_typed_progress_before_the_result() -> None:
    executor = RecordingExecutor()
    client = TestClient(create_live_app(executor))

    response = client.post(
        "/api/investigations/stream", json={"incident": incident_payload()}
    )

    assert response.status_code == 200
    assert "event: progress" in response.text
    assert '"stage": "initialize"' in response.text
    assert "event: result" in response.text
