"""Fixture-free application runner for LangGraph investigations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Any

from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import InvestigationBudget
from recoverit.composition import ProgressCallback, RuntimeContainer


@dataclass
class InvestigationResult:
    """Stable presentation DTO returned by live and benchmark applications."""

    incident_id: str
    service: str
    summary: str
    status: str
    stop_reason: str | None
    execution_time_seconds: float
    ranked_hypotheses: list[dict[str, Any]]
    timeline_events: list[dict[str, Any]]
    evidence_items: list[dict[str, Any]]
    diff_excerpts: dict[str, str]
    log_excerpts: list[dict[str, Any]]
    budget_usage: dict[str, Any]
    provider_used: str
    completion_criteria: dict[str, bool] = field(default_factory=dict)
    unresolved_criteria: list[str] = field(default_factory=list)
    scenario_name: str | None = None

    def to_markdown_report(self) -> str:
        """Render a compact, source-neutral investigation report."""

        lines = [
            f"# Incident Triage Report: {self.incident_id}",
            "",
            f"**Target Service:** `{self.service}`  ",
            f"**Incident Summary:** {self.summary}  ",
            f"**Investigation Status:** `{self.status.upper()}`  ",
            f"**Reasoning Provider:** `{self.provider_used}`  ",
            "",
            "## Ranked Root Cause Hypotheses",
            "",
        ]
        if not self.ranked_hypotheses:
            lines.append("> No ranked root-cause hypothesis was supported by sufficient evidence.")
        for hypothesis in self.ranked_hypotheses:
            lines.extend(
                [
                    f"### #{hypothesis['rank']} {hypothesis['statement']}",
                    f"- Category: `{hypothesis['root_cause_category']}`",
                    f"- Component: `{hypothesis['affected_component']}`",
                    f"- Evidence score: {hypothesis['evidence_score']}",
                    "",
                ]
            )
            breakdown = hypothesis.get("score_breakdown", {})
            if breakdown:
                lines.extend(
                    [
                        f"- Symptom Confidence: {breakdown.get('symptom_score', 0.0):.1f}%",
                        f"- Causal Confidence: {breakdown.get('causal_score', 0.0):.1f}%",
                        "",
                        "Confidence Breakdown:",
                        "",
                        "| Scoring Factor | Points |",
                        "|---|---:|",
                        f"| Independent sources | +{breakdown.get('independent_source_support', 0.0):.2f} |",
                        f"| Symptom coverage | +{breakdown.get('symptom_coverage', 0.0):.2f} |",
                        f"| Temporal consistency | +{breakdown.get('temporal_consistency', 0.0):.2f} |",
                        f"| Direct change evidence | +{breakdown.get('change_consistency', 0.0):.2f} |",
                        f"| Specificity | +{breakdown.get('specificity', 0.0):.2f} |",
                        f"| Prediction support | +{breakdown.get('prediction_support', 0.0):.2f} |",
                        f"| Contradictions | -{breakdown.get('contradiction_penalty', 0.0):.2f} |",
                        f"| Missing causal evidence | -{breakdown.get('missing_evidence_penalty', 0.0):.2f} |",
                        f"| **Final Evidence Score** | **{hypothesis['evidence_score']:.2f} / 100** |",
                        "",
                    ]
                )
        if self.unresolved_criteria:
            lines.extend(["## Remaining uncertainty", ""])
            lines.extend(f"- {item}" for item in self.unresolved_criteria)
        return "\n".join(lines)


class InvestigationRunner:
    """Invoke the compiled production graph for canonical incidents only."""

    def __init__(self, runtime: RuntimeContainer) -> None:
        self._runtime = runtime

    async def run(
        self,
        incident: IncidentSeed,
        budget: InvestigationBudget | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> InvestigationResult:
        start_time = time.monotonic()
        capabilities = self._runtime.registry.capabilities(incident)
        config = {"configurable": {"thread_id": incident.incident_id}}
        with self._runtime.progress_dispatcher.bind(progress_callback):
            output = await self._runtime.graph.ainvoke(
                {
                    "incident": incident,
                    "source_capabilities": capabilities,
                    "budget": budget or InvestigationBudget(),
                },
                config=config,
            )
        return self._to_result(output, incident, time.monotonic() - start_time)

    async def resume(
        self,
        incident_id: str,
        progress_callback: ProgressCallback | None = None,
    ) -> InvestigationResult:
        config = {"configurable": {"thread_id": incident_id}}
        snapshot = await self._runtime.graph.aget_state(config)
        if not snapshot.values:
            raise ValueError(f"No checkpoint exists for incident '{incident_id}'.")
        with self._runtime.progress_dispatcher.bind(progress_callback):
            output = (
                snapshot.values
                if snapshot.values.get("ranked_result") is not None
                else await self._runtime.graph.ainvoke(None, config=config)
            )
        context_incident = output["context"].incident
        incident = IncidentSeed(
            incident_id=incident_id,
            external_alert_id=incident_id,
            service=context_incident.service,
            environment=context_incident.environment,
            severity=context_incident.severity,
            detected_at=context_incident.detected_at,
            received_at=output.get("started_at", output["context"].created_at),
            summary=context_incident.summary,
        )
        started_at = output.get("started_at")
        elapsed = (
            max(0.0, (datetime.now(timezone.utc) - started_at).total_seconds())
            if started_at is not None
            else 0.0
        )
        return self._to_result(output, incident, elapsed)

    def _to_result(
        self,
        output: dict[str, Any],
        incident: IncidentSeed,
        elapsed: float,
    ) -> InvestigationResult:
        ranked = output["ranked_result"]
        context = output["context"]
        decision = output.get("stop_decision")
        return InvestigationResult(
            incident_id=incident.incident_id,
            service=incident.service,
            summary=incident.summary,
            status=str(ranked.status),
            stop_reason=str(ranked.stop_reason) if ranked.stop_reason else None,
            execution_time_seconds=elapsed,
            ranked_hypotheses=[
                {
                    "rank": item.rank,
                    "hypothesis_id": item.hypothesis_id,
                    "statement": item.statement,
                    "root_cause_category": str(item.root_cause_category),
                    "affected_component": item.affected_component,
                    "evidence_score": item.evidence_score,
                    "confidence_label": str(item.confidence_label),
                    "supporting_evidence": [
                        {"evidence_id": citation.evidence_id, "reason": citation.reason}
                        for citation in item.supporting_evidence
                    ],
                    "contradicting_evidence": [
                        {"evidence_id": citation.evidence_id, "reason": citation.reason}
                        for citation in item.contradicting_evidence
                    ],
                    "score_breakdown": (
                        item.score_breakdown.model_dump() if item.score_breakdown else {}
                    ),
                }
                for item in ranked.hypotheses
            ],
            timeline_events=[
                {
                    "event_id": event.timeline_event_id,
                    "event_time": event.event_time.isoformat() if event.event_time else None,
                    "category": str(event.category),
                    "title": event.title,
                    "service": event.service,
                    "evidence_ids": list(event.evidence_ids),
                }
                for event in context.timeline
            ],
            evidence_items=[
                {
                    "evidence_id": evidence.evidence_id,
                    "source_type": str(evidence.source_type),
                    "evidence_type": str(evidence.evidence_type),
                    "event_time": evidence.event_time.isoformat() if evidence.event_time else None,
                    "summary": evidence.summary,
                    "source_record_id": None,
                }
                for evidence in context.evidence
            ],
            diff_excerpts={},
            log_excerpts=[],
            budget_usage=ranked.budget_usage.model_dump(mode="json"),
            provider_used=self._provider_label(),
            completion_criteria=decision.criteria_status if decision else {},
            unresolved_criteria=decision.unresolved_criteria if decision else [],
        )

    def _provider_label(self) -> str:
        """Expose the configured provider/model without exposing credentials."""

        settings = self._runtime.settings
        if settings.llm_model == "unconfigured":
            return settings.llm_provider
        return f"{settings.llm_provider}/{settings.llm_model}"


LangGraphInvestigationRunner = InvestigationRunner


__all__ = ["InvestigationResult", "InvestigationRunner", "LangGraphInvestigationRunner"]
