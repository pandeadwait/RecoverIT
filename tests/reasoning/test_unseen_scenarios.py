"""Tests verifying reasoning works for completely unseen incidents and services."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from contracts.collection.schemas import (
    RawEvidenceBatch,
    RawRecord,
    SourceCapability,
    SourceCapabilityCatalog,
    SourceResult,
)
from contracts.common import (
    ConfidenceLabel,
    EvidenceRole,
    EvidenceType,
    Reliability,
    RootCauseCategory,
    SourceCoverageStatus,
    SourceStatus,
    SourceType,
    StopAction,
    StopReason,
)
from contracts.evidence.schemas import (
    EvidenceQuality,
    EvidenceSummaryProjection,
    IncidentContextSnapshot,
    IncidentSummary,
)
from contracts.hypothesis.schemas import (
    EvidenceCitation,
    Hypothesis,
    HypothesisSet,
    RankedHypothesisSet,
)
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import (
    BudgetUsage,
    InvestigationBudget,
    MissingInformationAssessment,
)
from evidence.context.builder import DefaultContextBuilder
from investigation.missing_information.assessor import MissingInformationAssessor
from investigation.query_planning.planner import EvidenceQueryPlanner
from reasoning.hypotheses.service import DefaultHypothesisService
from reasoning.ranking.ranking_engine import RankingEngine
from reasoning.stopping import DefaultStoppingService
from tests.support.scripted_reasoning_provider import ScriptedReasoningProvider


NOW = datetime(2026, 7, 20, 14, 0, 0, tzinfo=timezone.utc)


def _make_unseen_incident() -> IncidentSeed:
    """An incident from an arbitrary unseen domain: IoT satellite ground station."""
    return IncidentSeed(
        incident_id="sat-downlink-8891",
        external_alert_id="pager-unseen-99",
        service="orbital-telemetry-demux",
        environment="antarctica-ground-station-1",
        severity="critical",
        detected_at=NOW,
        received_at=NOW,
        summary="S-band telemetry demux buffer overrun with packet loss spike",
    )


def _make_unseen_capabilities(incident_id: str) -> SourceCapabilityCatalog:
    return SourceCapabilityCatalog(
        incident_id=incident_id,
        generated_at=NOW,
        sources=[
            SourceCapability(
                source_type=SourceType.LOGS,
                available=True,
                supported_query_fields=["service", "level", "start_time", "end_time", "query"],
                maximum_window_seconds=3600,
                maximum_items=100,
                adapter_name="telemetry-log-store",
            ),
            SourceCapability(
                source_type=SourceType.METRICS,
                available=True,
                supported_query_fields=["metric_name", "start_time", "end_time"],
                maximum_window_seconds=3600,
                maximum_items=50,
                adapter_name="telemetry-tsdb",
            ),
            SourceCapability(
                source_type=SourceType.DEPLOYMENTS,
                available=True,
                supported_query_fields=["service", "environment"],
                maximum_window_seconds=86400,
                maximum_items=10,
                adapter_name="ota-firmware-manager",
            ),
        ],
    )


class TestUnseenIncidentReasoning:
    """Validates the Person 3 pipeline handles unseen domains and services."""

    @pytest.mark.asyncio
    async def test_end_to_end_reasoning_unseen_pipeline(self) -> None:
        incident = _make_unseen_incident()
        capabilities = _make_unseen_capabilities(incident.incident_id)
        budget = InvestigationBudget(max_rounds=3, max_queries=10)
        budget_usage = BudgetUsage()

        # 1. Build initial context via DefaultContextBuilder
        context_builder = DefaultContextBuilder()
        init_context = IncidentContextSnapshot(
            snapshot_id=f"ctx-{incident.incident_id}-0",
            incident_id=incident.incident_id,
            revision=0,
            created_at=NOW,
            incident=IncidentSummary(
                service=incident.service,
                environment=incident.environment,
                severity=incident.severity,
                detected_at=incident.detected_at,
                summary=incident.summary,
            ),
            source_coverage={st.value: SourceCoverageStatus.NOT_QUERIED for st in SourceType},
        )

        batch1 = RawEvidenceBatch(
            batch_id="b-1",
            incident_id=incident.incident_id,
            plan_id="p-0",
            collected_at=NOW,
            results=[
                SourceResult(
                    query_id="q-init-log",
                    source_type=SourceType.LOGS,
                    source_adapter="telemetry-log-store",
                    source_status=SourceStatus.OK,
                    started_at=NOW,
                    completed_at=NOW,
                    records=[
                        RawRecord(
                            source_record_id="rec-log-1",
                            content_type="application/json",
                            payload={"message": "Buffer overflow in frame decoder, ring queue capacity exceeded"},
                            event_time=NOW,
                        )
                    ],
                )
            ],
            errors=[],
        )

        context = await context_builder.build(incident, batch1, init_context)
        assert context.revision == 1
        assert len(context.evidence) == 1
        assert "frame decoder" in context.evidence[0].summary

        # 2. Generate initial hypotheses on unseen incident
        hyp_service = DefaultHypothesisService()
        hyp_set = await hyp_service.generate(
            incident=incident,
            context=context,
            budget=budget,
        )

        assert isinstance(hyp_set, HypothesisSet)
        assert len(hyp_set.hypotheses) >= 1
        for h in hyp_set.hypotheses:
            assert h.incident_id == incident.incident_id
            assert len(h.affected_component) > 0
            assert len(h.statement) > 0

        # 3. Assess missing information
        assessor = MissingInformationAssessor()
        assessment = await assessor.assess(
            incident=incident,
            capabilities=capabilities,
            context=context,
            hypotheses=hyp_set,
        )
        assert isinstance(assessment, MissingInformationAssessment)
        assert assessment.incident_id == incident.incident_id

        # 4. Plan evidence queries
        planner = EvidenceQueryPlanner()
        plan = await planner.plan(
            assessment=assessment,
            capabilities=capabilities,
            context=context,
            hypotheses=hyp_set,
            budget=budget,
            budget_usage=budget_usage,
            round_number=1,
            query_history=[],
        )
        assert plan.incident_id == incident.incident_id
        assert plan.round_number == 1
        assert len(plan.queries) > 0
        for q in plan.queries:
            # Query source types must be available in capabilities
            cap_source_types = {s.source_type for s in capabilities.sources if s.available}
            assert q.source_type in cap_source_types

        # 5. Revise hypotheses with corroborating evidence
        batch2 = RawEvidenceBatch(
            batch_id="b-2",
            incident_id=incident.incident_id,
            plan_id=plan.plan_id,
            collected_at=NOW,
            results=[
                SourceResult(
                    query_id="q-rev-dep",
                    source_type=SourceType.DEPLOYMENTS,
                    source_adapter="ota-firmware-manager",
                    source_status=SourceStatus.OK,
                    started_at=NOW,
                    completed_at=NOW,
                    records=[
                        RawRecord(
                            source_record_id="rec-dep-1",
                            content_type="application/json",
                            payload={"title": "Firmware upgrade v4.2 applied to demux subsystem"},
                            event_time=NOW,
                        )
                    ],
                )
            ],
            errors=[],
        )
        context = await context_builder.build(incident, batch2, context)
        assert context.revision == 2

        revised_hyp_set = await hyp_service.revise(
            incident=incident,
            previous_hypotheses=hyp_set,
            context=context,
            assessment=assessment,
        )
        assert isinstance(revised_hyp_set, HypothesisSet)
        assert revised_hyp_set.revision == 2

        # 6. Evaluate stopping decision
        stopping_service = DefaultStoppingService(
            min_supporting_sources_for_adequate=2,
            min_evidence_score_for_confidence=30.0,
        )
        decision = stopping_service.evaluate(
            context=context,
            hypotheses=revised_hyp_set,
            assessment=assessment,
            query_plan=plan,
            budget=budget,
            budget_usage=budget_usage,
            round_number=1,
        )
        assert decision.action in (StopAction.CONTINUE, StopAction.RANK, StopAction.INCONCLUSIVE)

        # 7. Deterministic ranking
        ranking_service = RankingEngine()
        ranked = ranking_service.rank(
            hypotheses=revised_hyp_set,
            context=context,
            budget_usage=budget_usage,
            status=decision.action.value if hasattr(decision.action, "value") else str(decision.action),
            stop_reason=decision.stop_reason,
            remaining_uncertainty=decision.unresolved_criteria,
        )

        assert isinstance(ranked, RankedHypothesisSet)
        assert ranked.incident_id == incident.incident_id
        assert ranked.context_snapshot_id == context.snapshot_id
        assert len(ranked.hypotheses) > 0
        # Ranks are sequential starting at 1
        for idx, rh in enumerate(ranked.hypotheses, start=1):
            assert rh.rank == idx
            assert 0.0 <= rh.evidence_score <= 100.0
            assert rh.confidence_label in tuple(ConfidenceLabel)

    def test_production_reasoning_has_no_hardcoded_scenarios(self) -> None:
        """Verify that production code has no scenario_xxx or incident_00x hardcoding."""
        import inspect
        import reasoning.hypotheses.service as hs_mod
        import reasoning.ranking.ranking_engine as rank_mod
        import reasoning.stopping as stop_mod
        import investigation.missing_information.assessor as miss_mod
        import investigation.query_planning.planner as plan_mod

        modules = [hs_mod, rank_mod, stop_mod, miss_mod, plan_mod]
        forbidden_substrings = ["incident_001", "incident_002", "scenario_01", "scenario_1", "benchmark_label"]

        for mod in modules:
            src = inspect.getsource(mod).lower()
            for forbidden in forbidden_substrings:
                assert forbidden not in src, f"Found hardcoded test scenario {forbidden} in {mod.__name__}"
