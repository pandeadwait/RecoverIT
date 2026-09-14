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
    EvidenceRole,
    HypothesisStatus,
    InformationGapCategory,
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
    - 'dependency-incompatibility': External library or upstream dependency version mismatch.
    - 'coincidental-deployment': Deployment coincided with incident, but cause is independent external outage.
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
            "active_hypotheses_count": len(active_hypotheses) if active_hypotheses is not None else 0,
        })

        if self.custom_assessment is not None:
            return self.custom_assessment

        evidence_ids = [e.evidence_id for e in context.evidence]

        ev_sources = {e.source_type for e in context.evidence}

        if self.preset == "database-outage":
            if SourceType.METRICS not in ev_sources:
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
                        category=InformationGapCategory.SYMPTOM_CONFIRMATION,
                    )
                ]
            else:
                known = [
                    KnownFact(
                        statement=f"Service '{incident.service}' database pool exhaustion corroborated by metrics.",
                        evidence_ids=evidence_ids,
                    )
                ]
                missing = [
                    MissingInformationItem(
                        information_id="need_db_pool",
                        question="Are database connection pools exhausted?",
                        reason="High timeout count indicates connection pool starvation.",
                        priority=InformationPriority.HIGH,
                        candidate_sources=[SourceType.METRICS, SourceType.LOGS],
                        resolved=True,
                        category=InformationGapCategory.SYMPTOM_CONFIRMATION,
                    ),
                    MissingInformationItem(
                        information_id="need_db_logs",
                        question="Are there database timeout and connection refused errors in logs?",
                        reason="Corroborate connection pool exhaustion with instance error logs.",
                        priority=InformationPriority.HIGH,
                        candidate_sources=[SourceType.LOGS],
                        resolved=SourceType.LOGS in ev_sources,
                        category=InformationGapCategory.SYMPTOM_CONFIRMATION,
                    ),
                ]
        elif self.preset == "resource-exhaustion":
            if SourceType.METRICS not in ev_sources:
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
                        category=InformationGapCategory.DIRECT_CAUSAL_EVIDENCE,
                    )
                ]
            else:
                known = [
                    KnownFact(
                        statement=f"Alert on '{incident.service}' memory spike corroborated by metrics.",
                        evidence_ids=evidence_ids,
                    )
                ]
                missing = [
                    MissingInformationItem(
                        information_id="need_mem_metrics",
                        question="Did memory usage exceed container cgroup limits?",
                        reason="Investigate if container was killed by OOM killer.",
                        priority=InformationPriority.HIGH,
                        candidate_sources=[SourceType.METRICS, SourceType.HEALTH],
                        resolved=True,
                        category=InformationGapCategory.DIRECT_CAUSAL_EVIDENCE,
                    ),
                    MissingInformationItem(
                        information_id="need_oom_logs",
                        question="Are container OOMKilled or termination events logged?",
                        reason="Corroborate memory metric spikes with container failure logs.",
                        priority=InformationPriority.HIGH,
                        candidate_sources=[SourceType.LOGS],
                        resolved=SourceType.LOGS in ev_sources,
                        category=InformationGapCategory.SYMPTOM_CONFIRMATION,
                    ),
                ]
        elif self.preset == "dependency-incompatibility":
            if not any(s in ev_sources for s in (SourceType.CHANGES, SourceType.PIPELINES)):
                known = [
                    KnownFact(
                        statement=f"Service '{incident.service}' failed with dependency import or symbol resolution errors.",
                        evidence_ids=evidence_ids[:1],
                    )
                ]
                missing = [
                    MissingInformationItem(
                        information_id="need_dep_version",
                        question="Which dependency or library version was updated recently?",
                        reason="Identify incompatible transitive library upgrade.",
                        priority=InformationPriority.HIGH,
                        candidate_sources=[SourceType.PIPELINES, SourceType.CHANGES],
                        resolved=False,
                        category=InformationGapCategory.DIRECT_CAUSAL_EVIDENCE,
                    )
                ]
            else:
                known = [
                    KnownFact(
                        statement=f"Dependency upgrade confirmed in lockfile changes.",
                        evidence_ids=evidence_ids,
                    )
                ]
                missing = [
                    MissingInformationItem(
                        information_id="need_dep_version",
                        question="Which dependency or library version was updated recently?",
                        reason="Identify incompatible transitive library upgrade.",
                        priority=InformationPriority.HIGH,
                        candidate_sources=[SourceType.PIPELINES, SourceType.CHANGES],
                        resolved=True,
                        category=InformationGapCategory.DIRECT_CAUSAL_EVIDENCE,
                    ),
                    MissingInformationItem(
                        information_id="need_runtime_errors",
                        question="Are there module import or symbol resolution errors in service logs?",
                        reason="Corroborate dependency update with runtime failure logs.",
                        priority=InformationPriority.HIGH,
                        candidate_sources=[SourceType.LOGS],
                        resolved=SourceType.LOGS in ev_sources,
                        category=InformationGapCategory.SYMPTOM_CONFIRMATION,
                    ),
                ]
        elif self.preset == "coincidental-deployment":
            if not any(s in ev_sources for s in (SourceType.HEALTH, SourceType.LOGS)):
                known = [
                    KnownFact(
                        statement=f"Deployment on '{incident.service}' coincided with external payment provider outage.",
                        evidence_ids=evidence_ids[:1],
                    )
                ]
                missing = [
                    MissingInformationItem(
                        information_id="need_ext_health",
                        question="Is the external dependency or upstream provider down independently?",
                        reason="Verify whether the coincident deployment is causal or merely temporal.",
                        priority=InformationPriority.HIGH,
                        candidate_sources=[SourceType.HEALTH, SourceType.LOGS],
                        resolved=False,
                        category=InformationGapCategory.CONTRADICTING_EVIDENCE,
                    )
                ]
            else:
                known = [
                    KnownFact(
                        statement=f"External gateway health degradation confirmed.",
                        evidence_ids=evidence_ids,
                    )
                ]
                missing = [
                    MissingInformationItem(
                        information_id="need_ext_health",
                        question="Is the external dependency or upstream provider down independently?",
                        reason="Verify whether the coincident deployment is causal or merely temporal.",
                        priority=InformationPriority.HIGH,
                        candidate_sources=[SourceType.HEALTH, SourceType.LOGS],
                        resolved=True,
                        category=InformationGapCategory.CONTRADICTING_EVIDENCE,
                    ),
                    MissingInformationItem(
                        information_id="need_deploy_diff",
                        question="Did the coincidental deployment modify payment components?",
                        reason="Verify if deployment was causal or purely coincidental.",
                        priority=InformationPriority.HIGH,
                        candidate_sources=[SourceType.DEPLOYMENTS],
                        resolved=any(s in ev_sources for s in (SourceType.DEPLOYMENTS, SourceType.CHANGES)),
                        category=InformationGapCategory.DIRECT_CAUSAL_EVIDENCE,
                    ),
                ]
        else:  # default: deployment-regression
            if not any(s in ev_sources for s in (SourceType.DEPLOYMENTS, SourceType.CHANGES)):
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
                        category=InformationGapCategory.DIRECT_CAUSAL_EVIDENCE,
                    )
                ]
            else:
                known = [
                    KnownFact(
                        statement=f"Recent deployment verified on '{incident.service}'.",
                        evidence_ids=evidence_ids,
                    )
                ]
                missing = [
                    MissingInformationItem(
                        information_id="need_deploy_history",
                        question="Was a deployment completed shortly before the error increase?",
                        reason="Distinguishes recent-change regression from an independent outage.",
                        priority=InformationPriority.HIGH,
                        candidate_sources=[SourceType.DEPLOYMENTS, SourceType.CHANGES],
                        resolved=True,
                        category=InformationGapCategory.DIRECT_CAUSAL_EVIDENCE,
                    ),
                    MissingInformationItem(
                        information_id="need_error_logs",
                        question="Are there application error logs corresponding to this deployment?",
                        reason="Corroborate deployment change with runtime error symptoms.",
                        priority=InformationPriority.HIGH,
                        candidate_sources=[SourceType.LOGS],
                        resolved=SourceType.LOGS in ev_sources,
                        category=InformationGapCategory.SYMPTOM_CONFIRMATION,
                    ),
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
        incident_service = (
            context.incident.service
            if (context and context.incident and context.incident.service)
            else "payment-api"
        )

        ev_sources = {e.source_type for e in context.evidence}

        metric_cap = next(
            (s for s in source_capabilities.sources if s.source_type == SourceType.METRICS),
            None,
        )
        metric_key = (
            "metric"
            if (metric_cap and "metric" in metric_cap.supported_query_fields and "metric_name" not in metric_cap.supported_query_fields)
            else "metric_name"
        )

        if self.preset == "database-outage":
            if SourceType.METRICS not in ev_sources:
                queries = [
                    EvidenceQueryPlanQuery(
                        query_id="qry_db_pool_01",
                        source_type=SourceType.METRICS,
                        question="What is current connection pool or database error metric?",
                        parameters={
                            metric_key: "http_500_rate",
                            "service": incident_service,
                            "limit": 50,
                        },
                        related_information_ids=["need_db_pool"],
                        discriminates_hypothesis_ids=["hyp_01", "hyp_02"],
                        expected_information_value=InformationValueLevel.HIGH,
                    )
                ]
            else:
                queries = [
                    EvidenceQueryPlanQuery(
                        query_id="qry_db_log_01",
                        source_type=SourceType.LOGS,
                        question="Are there database timeout and connection refused errors in logs?",
                        parameters={
                            "service": incident_service,
                            "severity": ["error", "warning", "critical"],
                            "limit": 20,
                        },
                        related_information_ids=["need_db_logs"],
                        discriminates_hypothesis_ids=["hyp_01", "hyp_02"],
                        expected_information_value=InformationValueLevel.HIGH,
                    )
                ]
        elif self.preset == "resource-exhaustion":
            if SourceType.METRICS not in ev_sources:
                queries = [
                    EvidenceQueryPlanQuery(
                        query_id="qry_mem_01",
                        source_type=SourceType.METRICS,
                        question="What is the container memory usage trend?",
                        parameters={
                            metric_key: "container_memory_usage_bytes",
                            "service": incident_service,
                            "limit": 100,
                        },
                        related_information_ids=["need_mem_metrics"],
                        discriminates_hypothesis_ids=["hyp_01", "hyp_02"],
                        expected_information_value=InformationValueLevel.HIGH,
                    )
                ]
            else:
                queries = [
                    EvidenceQueryPlanQuery(
                        query_id="qry_mem_log_01",
                        source_type=SourceType.LOGS,
                        question="Are there container OOMKilled or termination events in logs?",
                        parameters={
                            "service": incident_service,
                            "severity": ["error", "warning", "critical"],
                            "limit": 20,
                        },
                        related_information_ids=["need_oom_logs"],
                        discriminates_hypothesis_ids=["hyp_01", "hyp_02"],
                        expected_information_value=InformationValueLevel.HIGH,
                    )
                ]
        elif self.preset == "dependency-incompatibility":
            if not any(s in ev_sources for s in (SourceType.CHANGES, SourceType.PIPELINES)):
                queries = [
                    EvidenceQueryPlanQuery(
                        query_id="qry_dep_01",
                        source_type=SourceType.CHANGES,
                        question="What dependencies were modified in the build or package lockfiles?",
                        parameters={
                            "service": incident_service,
                            "limit": 10,
                        },
                        related_information_ids=["need_dep_version"],
                        discriminates_hypothesis_ids=["hyp_01", "hyp_02"],
                        expected_information_value=InformationValueLevel.HIGH,
                    )
                ]
            else:
                queries = [
                    EvidenceQueryPlanQuery(
                        query_id="qry_dep_log_01",
                        source_type=SourceType.LOGS,
                        question="Are there module import or symbol resolution errors in service logs?",
                        parameters={
                            "service": incident_service,
                            "severity": ["error", "warning", "critical"],
                            "limit": 20,
                        },
                        related_information_ids=["need_runtime_errors"],
                        discriminates_hypothesis_ids=["hyp_01", "hyp_02"],
                        expected_information_value=InformationValueLevel.HIGH,
                    )
                ]
        elif self.preset == "coincidental-deployment":
            if not any(s in ev_sources for s in (SourceType.HEALTH, SourceType.LOGS)):
                has_health = any(
                    s.source_type == SourceType.HEALTH and s.available
                    for s in source_capabilities.sources
                )
                if has_health:
                    queries = [
                        EvidenceQueryPlanQuery(
                            query_id="qry_ext_health_01",
                            source_type=SourceType.HEALTH,
                            question="What is the availability status of the external payment gateway?",
                            parameters={
                                "service": "payment-gateway",
                            },
                            related_information_ids=["need_ext_health"],
                            discriminates_hypothesis_ids=["hyp_01", "hyp_02"],
                            expected_information_value=InformationValueLevel.HIGH,
                        )
                    ]
                else:
                    queries = [
                        EvidenceQueryPlanQuery(
                            query_id="qry_ext_log_01",
                            source_type=SourceType.LOGS,
                            question="Are there external payment gateway errors or timeouts?",
                            parameters={
                                "service": incident_service,
                                "severity": ["error", "warning", "critical"],
                                "limit": 10,
                            },
                            related_information_ids=["need_ext_health"],
                            discriminates_hypothesis_ids=["hyp_01", "hyp_02"],
                            expected_information_value=InformationValueLevel.HIGH,
                        )
                    ]
            else:
                queries = [
                    EvidenceQueryPlanQuery(
                        query_id="qry_deploy_01",
                        source_type=SourceType.DEPLOYMENTS,
                        question="Which deployments completed shortly before the error increase?",
                        parameters={
                            "service": incident_service,
                            "limit": 10,
                        },
                        related_information_ids=["need_deploy_diff"],
                        discriminates_hypothesis_ids=["hyp_01", "hyp_02"],
                        expected_information_value=InformationValueLevel.HIGH,
                    )
                ]
        else:  # deployment-regression
            if not any(s in ev_sources for s in (SourceType.DEPLOYMENTS, SourceType.CHANGES)):
                queries = [
                    EvidenceQueryPlanQuery(
                        query_id="qry_deploy_01",
                        source_type=SourceType.DEPLOYMENTS,
                        question="Which deployments completed shortly before the error increase?",
                        parameters={
                            "service": incident_service,
                            "limit": 10,
                        },
                        related_information_ids=["need_deploy_history"],
                        discriminates_hypothesis_ids=["hyp_01", "hyp_02"],
                        expected_information_value=InformationValueLevel.HIGH,
                    )
                ]
            else:
                queries = [
                    EvidenceQueryPlanQuery(
                        query_id="qry_log_01",
                        source_type=SourceType.LOGS,
                        question="Are there application error logs corresponding to this deployment?",
                        parameters={
                            "service": incident_service,
                            "severity": ["error", "warning", "critical"],
                            "limit": 20,
                        },
                        related_information_ids=["need_error_logs"],
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
                        EvidenceCitation(
                            evidence_id=eid,
                            reason="Connection pool exhaustion metrics detected.",
                            role=EvidenceRole.CAUSE,
                        )
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
                        EvidenceCitation(
                            evidence_id=eid,
                            reason="Memory usage spike observed.",
                            role=EvidenceRole.CAUSE,
                        )
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
        elif self.preset == "dependency-incompatibility":
            hypotheses = [
                Hypothesis(
                    hypothesis_id="hyp_01",
                    incident_id=incident.incident_id,
                    revision=1,
                    statement="Incompatible transitive dependency update broke application runtime.",
                    root_cause_category=RootCauseCategory.DEPENDENCY_INCOMPATIBILITY,
                    affected_component=incident.service,
                    supporting_evidence=[
                        EvidenceCitation(
                            evidence_id=eid,
                            reason="Incompatible dependency introduced in lockfile change.",
                            role=EvidenceRole.CAUSE,
                        )
                        for eid in evidence_ids[:1]
                    ],
                    contradicting_evidence=[],
                    missing_information_ids=["need_dep_version"],
                    testable_prediction="Dependency lockfile diff shows upgraded library version.",
                    status=HypothesisStatus.ACTIVE,
                ),
                Hypothesis(
                    hypothesis_id="hyp_02",
                    incident_id=incident.incident_id,
                    revision=1,
                    statement="Hardware node failure affected container host.",
                    root_cause_category=RootCauseCategory.INFRASTRUCTURE_FAILURE,
                    affected_component="k8s-worker-node",
                    supporting_evidence=[],
                    contradicting_evidence=[],
                    missing_information_ids=[],
                    testable_prediction="Kubernetes node events show NodeNotReady.",
                    status=HypothesisStatus.ACTIVE,
                ),
            ]
        elif self.preset == "coincidental-deployment":
            hypotheses = [
                Hypothesis(
                    hypothesis_id="hyp_01",
                    incident_id=incident.incident_id,
                    revision=1,
                    statement="External third-party payment gateway suffered an independent service outage.",
                    root_cause_category=RootCauseCategory.EXTERNAL_DEPENDENCY_FAILURE,
                    affected_component="payment-gateway",
                    supporting_evidence=[
                        EvidenceCitation(
                            evidence_id=eid,
                            reason="External gateway timeouts reported.",
                            role=EvidenceRole.CAUSE,
                        )
                        for eid in evidence_ids[:1]
                    ],
                    contradicting_evidence=[],
                    missing_information_ids=["need_ext_health"],
                    testable_prediction="External status page confirms global gateway degradation.",
                    status=HypothesisStatus.ACTIVE,
                ),
                Hypothesis(
                    hypothesis_id="hyp_02",
                    incident_id=incident.incident_id,
                    revision=1,
                    statement="Coincidental deployment introduced a regression in payment routing.",
                    root_cause_category=RootCauseCategory.DEPLOYMENT_FAILURE,
                    affected_component=incident.service,
                    supporting_evidence=[],
                    contradicting_evidence=[],
                    missing_information_ids=[],
                    testable_prediction="Rollback to previous deployment resolves errors.",
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
                        EvidenceCitation(
                            evidence_id=eid,
                            reason="500 errors began after deployment.",
                            role=EvidenceRole.CAUSE,
                        )
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
        ev_map = {e.evidence_id: e for e in new_context.evidence}

        revised: list[Hypothesis] = []
        for i, h in enumerate(previous_hypotheses.hypotheses):
            if i == 0:
                # Top hypothesis strengthened
                new_citations = list(h.supporting_evidence)
                existing_ids = {c.evidence_id for c in new_citations}
                for eid, ev in ev_map.items():
                    if eid not in existing_ids:
                        if ev.source_type in (SourceType.LOGS, SourceType.METRICS):
                            role = EvidenceRole.EFFECT
                            reason = "Corroborating runtime symptom observed in logs/metrics."
                        elif ev.source_type in (SourceType.DEPLOYMENTS, SourceType.CHANGES, SourceType.CONFIGURATION):
                            role = EvidenceRole.CAUSE
                            reason = "Corroborating change record confirmed."
                        elif ev.source_type == SourceType.HEALTH:
                            role = EvidenceRole.CAUSE
                            reason = "Upstream provider degradation confirmed."
                        else:
                            role = EvidenceRole.CORRELATION
                            reason = "Corroborating evidence found in new round."
                        new_citations.append(
                            EvidenceCitation(
                                evidence_id=eid,
                                reason=reason,
                                role=role,
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
