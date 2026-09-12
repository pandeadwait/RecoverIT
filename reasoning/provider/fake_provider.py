"""
Deterministic fake reasoning provider for testing and offline development.

Returns structured domain objects conforming to canonical contract schemas.
Supports multiple operational incident presets without requiring a live model.

See WORK_DIVISION.md §8.7 and ARCHITECTURE.md §8.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from contracts.collection.schemas import SourceCapabilityCatalog
from contracts.common import (
    HypothesisStatus,
    InformationPriority,
    InformationValueLevel,
    RootCauseCategory,
    SourceType,
)
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.hypothesis.schemas import (
    EvidenceCitation,
    Hypothesis,
    HypothesisSet,
)
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import (
    EvidenceQueryPlan,
    EvidenceQueryPlanQuery,
    InvestigationBudget,
    KnownFact,
    MissingInformationAssessment,
    MissingInformationItem,
)


class FakeReasoningProvider:
    """
    Deterministic ReasoningProvider test double.

    Presets supported:
    - 'deployment-regression': New code/config deployment caused operational regression.
    - 'database-outage': Database connection pool exhaustion / failure.
    - 'resource-exhaustion': Memory leak / container OOM kills.
    """

    def __init__(
        self,
        preset: str = "deployment-regression",
        custom_assessment: MissingInformationAssessment | None = None,
        custom_plan: EvidenceQueryPlan | None = None,
        custom_hypotheses: HypothesisSet | None = None,
        custom_revised_hypotheses: HypothesisSet | None = None,
    ) -> None:
        self.preset = preset
        self.custom_assessment = custom_assessment
        self.custom_plan = custom_plan
        self.custom_hypotheses = custom_hypotheses
        self.custom_revised_hypotheses = custom_revised_hypotheses

        self._calls: list[dict[str, Any]] = []

    @property
    def call_count(self) -> int:
        """Total number of reasoning methods invoked."""
        return len(self._calls)

    @property
    def calls(self) -> list[dict[str, Any]]:
        """Log of all reasoning calls."""
        return list(self._calls)

    def reset_calls(self) -> None:
        """Clear the call log."""
        self._calls.clear()

    # -----------------------------------------------------------------------
    # ReasoningProvider implementation
    # -----------------------------------------------------------------------

    async def assess_missing_information(
        self,
        incident: IncidentSeed,
        source_capabilities: SourceCapabilityCatalog,
        context: IncidentContextSnapshot,
        active_hypotheses: list[Hypothesis],
    ) -> MissingInformationAssessment:
        self._calls.append({
            "method": "assess_missing_information",
            "incident_id": incident.incident_id,
            "active_hypotheses_count": len(active_hypotheses),
        })

        if self.custom_assessment is not None:
            return self.custom_assessment

        evidence_ids = [e.evidence_id for e in context.evidence]

        if self.preset == "database-outage":
            known = [
                KnownFact(
                    statement=f"Service '{incident.service}' reported database errors.",
                    evidence_ids=evidence_ids[:1],
                )
            ]
            missing = [
                MissingInformationItem(
                    information_id="need_db_pool",
                    question="Are database connection pools exhausted?",
                    reason="High timeout count indicates connection pool starvation.",
                    priority=InformationPriority.HIGH,
                    candidate_sources=[SourceType.METRICS, SourceType.LOGS],
                    resolved=False,
                )
            ]
        elif self.preset == "resource-exhaustion":
            known = [
                KnownFact(
                    statement=f"Alert on '{incident.service}' indicates high error rate or restart.",
                    evidence_ids=evidence_ids[:1],
                )
            ]
            missing = [
                MissingInformationItem(
                    information_id="need_mem_metrics",
                    question="Did memory usage exceed container cgroup limits?",
                    reason="Investigate if container was killed by OOM killer.",
                    priority=InformationPriority.HIGH,
                    candidate_sources=[SourceType.METRICS, SourceType.HEALTH],
                    resolved=False,
                )
            ]
        else:  # default: deployment-regression
            known = [
                KnownFact(
                    statement=f"Alert '{incident.summary}' detected on service '{incident.service}'.",
                    evidence_ids=evidence_ids[:1],
                )
            ]
            missing = [
                MissingInformationItem(
                    information_id="need_deploy_history",
                    question="Was a deployment completed shortly before the error increase?",
                    reason="Distinguishes recent-change regression from an independent outage.",
                    priority=InformationPriority.HIGH,
                    candidate_sources=[SourceType.DEPLOYMENTS, SourceType.CHANGES],
                    resolved=False,
                )
            ]

        return MissingInformationAssessment(
            incident_id=incident.incident_id,
            assessment_id=f"mia_{incident.incident_id}_{len(self._calls)}",
            known_facts=known,
            missing_information=missing,
            unavailable_information=[],
            recommended_stop=False,
        )

    async def plan_queries(
        self,
        missing_information: MissingInformationAssessment,
        source_capabilities: SourceCapabilityCatalog,
        context: IncidentContextSnapshot,
        budget: InvestigationBudget,
    ) -> EvidenceQueryPlan:
        self._calls.append({
            "method": "plan_queries",
            "incident_id": missing_information.incident_id,
            "missing_info_count": len(missing_information.missing_information),
        })

        if self.custom_plan is not None:
            return self.custom_plan

        now_str = datetime.now(timezone.utc).isoformat()

        if self.preset == "database-outage":
            queries = [
                EvidenceQueryPlanQuery(
                    query_id="qry_db_pool_01",
                    source_type=SourceType.METRICS,
                    question="What is current connection pool utilization?",
                    parameters={
                        "metric": "db_connections_active",
                        "service": "payment-db",
                        "limit": 50,
                    },
                    related_information_ids=["need_db_pool"],
                    discriminates_hypothesis_ids=["hyp_01", "hyp_02"],
                    expected_information_value=InformationValueLevel.HIGH,
                )
            ]
        elif self.preset == "resource-exhaustion":
            queries = [
                EvidenceQueryPlanQuery(
                    query_id="qry_mem_01",
                    source_type=SourceType.METRICS,
                    question="What is the container memory usage trend?",
                    parameters={
                        "metric": "container_memory_working_set_bytes",
                        "limit": 100,
                    },
                    related_information_ids=["need_mem_metrics"],
                    discriminates_hypothesis_ids=["hyp_01", "hyp_02"],
                    expected_information_value=InformationValueLevel.HIGH,
                )
            ]
        else:  # deployment-regression
            queries = [
                EvidenceQueryPlanQuery(
                    query_id="qry_deploy_01",
                    source_type=SourceType.DEPLOYMENTS,
                    question="Which deployments completed shortly before the error increase?",
                    parameters={
                        "service": "payment-api",
                        "limit": 10,
                    },
                    related_information_ids=["need_deploy_history"],
                    discriminates_hypothesis_ids=["hyp_01", "hyp_02"],
                    expected_information_value=InformationValueLevel.HIGH,
                )
            ]

        return EvidenceQueryPlan(
            incident_id=missing_information.incident_id,
            plan_id=f"plan_{missing_information.incident_id}_{len(self._calls)}",
            round=1,
            queries=queries,
            stop_reason=None,
        )

    async def generate_hypotheses(
        self,
        incident: IncidentSeed,
        context: IncidentContextSnapshot,
        limits: InvestigationBudget,
    ) -> HypothesisSet:
        self._calls.append({
            "method": "generate_hypotheses",
            "incident_id": incident.incident_id,
        })

        if self.custom_hypotheses is not None:
            return self.custom_hypotheses

        now = datetime.now(timezone.utc)
        evidence_ids = [e.evidence_id for e in context.evidence]

        if self.preset == "database-outage":
            hypotheses = [
                Hypothesis(
                    hypothesis_id="hyp_01",
                    incident_id=incident.incident_id,
                    revision=1,
                    statement="Database connection pool exhausted due to unclosed connections.",
                    root_cause_category=RootCauseCategory.RESOURCE_EXHAUSTION,
                    affected_component=incident.service,
                    supporting_evidence=[
                        EvidenceCitation(evidence_id=eid, reason="Connection errors logged.")
                        for eid in evidence_ids[:1]
                    ],
                    contradicting_evidence=[],
                    missing_information_ids=["need_db_pool"],
                    testable_prediction="Active connections count equals maximum pool size.",
                    status=HypothesisStatus.ACTIVE,
                ),
                Hypothesis(
                    hypothesis_id="hyp_02",
                    incident_id=incident.incident_id,
                    revision=1,
                    statement="Database primary node experienced an unexpected hardware crash.",
                    root_cause_category=RootCauseCategory.DATABASE_OUTAGE,
                    affected_component="database-cluster",
                    supporting_evidence=[],
                    contradicting_evidence=[],
                    missing_information_ids=[],
                    testable_prediction="Database server uptime reset or failover occurred.",
                    status=HypothesisStatus.ACTIVE,
                ),
            ]
        elif self.preset == "resource-exhaustion":
            hypotheses = [
                Hypothesis(
                    hypothesis_id="hyp_01",
                    incident_id=incident.incident_id,
                    revision=1,
                    statement="Application memory leak triggered container OOM kill.",
                    root_cause_category=RootCauseCategory.RESOURCE_EXHAUSTION,
                    affected_component=incident.service,
                    supporting_evidence=[
                        EvidenceCitation(evidence_id=eid, reason="Memory usage spike observed.")
                        for eid in evidence_ids[:1]
                    ],
                    contradicting_evidence=[],
                    missing_information_ids=["need_mem_metrics"],
                    testable_prediction="Container exit code is 137 (OOMKilled).",
                    status=HypothesisStatus.ACTIVE,
                ),
                Hypothesis(
                    hypothesis_id="hyp_02",
                    incident_id=incident.incident_id,
                    revision=1,
                    statement="Sudden inbound traffic spike overwhelmed pod capacity.",
                    root_cause_category=RootCauseCategory.INFRASTRUCTURE_FAILURE,
                    affected_component=incident.service,
                    supporting_evidence=[],
                    contradicting_evidence=[],
                    missing_information_ids=[],
                    testable_prediction="Request rate metrics increased significantly before errors.",
                    status=HypothesisStatus.ACTIVE,
                ),
            ]
        else:  # deployment-regression
            hypotheses = [
                Hypothesis(
                    hypothesis_id="hyp_01",
                    incident_id=incident.incident_id,
                    revision=1,
                    statement="The recent deployment introduced an invalid database connection configuration.",
                    root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
                    affected_component=incident.service,
                    supporting_evidence=[
                        EvidenceCitation(evidence_id=eid, reason="500 errors began after deployment.")
                        for eid in evidence_ids[:1]
                    ],
                    contradicting_evidence=[],
                    missing_information_ids=["need_deploy_history"],
                    testable_prediction="Configuration diff shows modified database endpoint.",
                    status=HypothesisStatus.ACTIVE,
                ),
                Hypothesis(
                    hypothesis_id="hyp_02",
                    incident_id=incident.incident_id,
                    revision=1,
                    statement="Upstream payment gateway experienced a transient external outage.",
                    root_cause_category=RootCauseCategory.EXTERNAL_DEPENDENCY_FAILURE,
                    affected_component="payment-gateway-client",
                    supporting_evidence=[],
                    contradicting_evidence=[],
                    missing_information_ids=[],
                    testable_prediction="External gateway health checks show downtime.",
                    status=HypothesisStatus.ACTIVE,
                ),
            ]

        return HypothesisSet(
            incident_id=incident.incident_id,
            hypotheses=hypotheses,
            generated_at=now,
        )

    async def revise_hypotheses(
        self,
        previous_hypotheses: HypothesisSet,
        new_context: IncidentContextSnapshot,
    ) -> HypothesisSet:
        self._calls.append({
            "method": "revise_hypotheses",
            "incident_id": previous_hypotheses.incident_id,
            "prev_count": len(previous_hypotheses.hypotheses),
        })

        if self.custom_revised_hypotheses is not None:
            return self.custom_revised_hypotheses

        now = datetime.now(timezone.utc)
        evidence_ids = [e.evidence_id for e in new_context.evidence]

        revised: list[Hypothesis] = []
        for i, h in enumerate(previous_hypotheses.hypotheses):
            if i == 0:
                # Top hypothesis strengthened
                new_citations = list(h.supporting_evidence)
                for eid in evidence_ids:
                    if not any(c.evidence_id == eid for c in new_citations):
                        new_citations.append(
                            EvidenceCitation(
                                evidence_id=eid,
                                reason="Additional corroborating evidence found in new round.",
                            )
                        )
                revised.append(
                    Hypothesis(
                        hypothesis_id=h.hypothesis_id,
                        incident_id=h.incident_id,
                        revision=h.revision + 1,
                        statement=h.statement,
                        root_cause_category=h.root_cause_category,
                        affected_component=h.affected_component,
                        supporting_evidence=new_citations,
                        contradicting_evidence=h.contradicting_evidence,
                        missing_information_ids=[],
                        testable_prediction=h.testable_prediction,
                        status=HypothesisStatus.ACTIVE,
                    )
                )
            else:
                # Alternative hypothesis weakened or rejected
                revised.append(
                    Hypothesis(
                        hypothesis_id=h.hypothesis_id,
                        incident_id=h.incident_id,
                        revision=h.revision + 1,
                        statement=h.statement,
                        root_cause_category=h.root_cause_category,
                        affected_component=h.affected_component,
                        supporting_evidence=h.supporting_evidence,
                        contradicting_evidence=h.contradicting_evidence,
                        missing_information_ids=h.missing_information_ids,
                        testable_prediction=h.testable_prediction,
                        status=HypothesisStatus.WEAKENED,
                    )
                )

        return HypothesisSet(
            incident_id=previous_hypotheses.incident_id,
            hypotheses=revised,
            generated_at=now,
        )
