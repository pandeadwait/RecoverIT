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
    RankedHypothesisSet,
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
from contracts.remediation.schemas import (
    RemediationPlan,
    RemediationRisk,
    RemediationStep,
)


class ScriptedReasoningProvider:
    """Scripted and programmatic reasoning test double for tests and benchmarks.

    Works for unseen incidents and unseen services without relying on scenario presets.
    Can be configured with scripted returns or can heuristically synthesize valid responses
    derived from incident seed, capability catalog, and current context.
    """

    def __init__(
        self,
        custom_assessment: MissingInformationAssessment | None = None,
        custom_plan: EvidenceQueryPlan | None = None,
        custom_hypotheses: HypothesisSet | None = None,
        custom_revised_hypotheses: HypothesisSet | None = None,
        custom_remediation: RemediationPlan | None = None,
        **kwargs: Any,
    ) -> None:
        self.custom_assessment = custom_assessment
        self.custom_plan = custom_plan
        self.custom_hypotheses = custom_hypotheses
        self.custom_revised_hypotheses = custom_revised_hypotheses
        self.custom_remediation = custom_remediation
        self._calls: list[dict[str, Any]] = []

    @property
    def call_count(self) -> int:
        return len(self._calls)

    @property
    def calls(self) -> list[dict[str, Any]]:
        return list(self._calls)

    def reset_calls(self) -> None:
        self._calls.clear()

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

        now = datetime.now(timezone.utc)
        evidence_ids = [e.evidence_id for e in context.evidence]
        known_facts = [
            KnownFact(
                statement=f"Incident {incident.incident_id} detected on {incident.service}: {incident.summary}",
                evidence_ids=evidence_ids[:2] if evidence_ids else [],
            )
        ]
        missing_items = []
        unavailable_items = []
        for src in source_capabilities.sources:
            st_val = src.source_type.value if hasattr(src.source_type, "value") else str(src.source_type)
            if src.available:
                missing_items.append(
                    MissingInformationItem(
                        information_id=f"gap_{st_val}",
                        question=f"What operational records exist in {st_val} for {incident.service}?",
                        reason=f"Investigating {incident.service} requires examining {st_val}.",
                        priority=InformationPriority.HIGH if st_val in ("logs", "changes", "deployments") else InformationPriority.MEDIUM,
                        candidate_sources=[src.source_type],
                        category=InformationGapCategory.DIRECT_CAUSAL_EVIDENCE if st_val in ("changes", "deployments", "configuration") else InformationGapCategory.SYMPTOM_CONFIRMATION,
                    )
                )
            else:
                unavailable_items.append(
                    MissingInformationItem(
                        information_id=f"unavail_{st_val}",
                        question=f"What {st_val} records exist?",
                        reason=f"Source {st_val} is unavailable.",
                        priority=InformationPriority.LOW,
                        candidate_sources=[src.source_type],
                    )
                )
        return MissingInformationAssessment(
            incident_id=incident.incident_id,
            assessment_id=f"assess_{incident.incident_id}_{self.call_count}",
            generated_at=now,
            known_facts=known_facts,
            missing_information=missing_items,
            unavailable_information=unavailable_items,
            recommended_stop=not missing_items,
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
        })
        if self.custom_plan is not None:
            return self.custom_plan

        catalog_map = {s.source_type: s for s in source_capabilities.sources if s.available}
        queries = []
        for idx, item in enumerate(missing_information.missing_information, start=1):
            if len(queries) >= budget.max_queries:
                break
            for st in item.candidate_sources:
                if st in catalog_map:
                    cap = catalog_map[st]
                    params = {}
                    if "service" in cap.supported_query_fields:
                        params["service"] = context.incident.service
                    if "limit" in cap.supported_query_fields:
                        params["limit"] = min(50, cap.maximum_items)
                    st_val = st.value if hasattr(st, "value") else str(st)
                    queries.append(
                        EvidenceQueryPlanQuery(
                            query_id=f"q_{st_val}_{idx}",
                            source_type=st,
                            question=item.question,
                            parameters=params,
                            related_information_ids=[item.information_id],
                            expected_information_value=InformationValueLevel.MEDIUM,
                        )
                    )
                    break

        stop_reason = None
        if not queries:
            stop_reason = (
                StopReason.SOURCES_UNAVAILABLE
                if missing_information.unavailable_information
                else StopReason.SUFFICIENT_EVIDENCE
            )

        return EvidenceQueryPlan(
            incident_id=missing_information.incident_id,
            plan_id=f"plan_{missing_information.incident_id}_{self.call_count}",
            round_number=1,
            queries=queries,
            stop_reason=stop_reason,
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
        ev_ids = [e.evidence_id for e in context.evidence]
        citations_1 = [
            EvidenceCitation(
                evidence_id=eid,
                reason="Relevant observation",
                role=EvidenceRole.CAUSE if idx == 0 else EvidenceRole.EFFECT,
            )
            for idx, eid in enumerate(ev_ids[:2])
        ]
        citations_2 = [
            EvidenceCitation(
                evidence_id=eid,
                reason="Corroborating symptom indicator",
                role=EvidenceRole.EFFECT,
            )
            for eid in ev_ids[:1]
        ]

        h1 = Hypothesis(
            hypothesis_id=f"hyp_{incident.incident_id}_1",
            incident_id=incident.incident_id,
            statement=f"Deployment or configuration update to {incident.service} triggered failure.",
            root_cause_category=RootCauseCategory.DEPLOYMENT_FAILURE,
            affected_component=incident.service,
            supporting_evidence=citations_1,
            status=HypothesisStatus.ACTIVE,
        )
        h2 = Hypothesis(
            hypothesis_id=f"hyp_{incident.incident_id}_2",
            incident_id=incident.incident_id,
            statement=f"External dependency or infrastructure degradation affected {incident.service}.",
            root_cause_category=RootCauseCategory.EXTERNAL_DEPENDENCY_FAILURE,
            affected_component="upstream",
            supporting_evidence=citations_2,
            status=HypothesisStatus.ACTIVE,
        )
        return HypothesisSet(
            incident_id=incident.incident_id,
            hypotheses=[h1, h2],
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
        })
        if self.custom_revised_hypotheses is not None:
            return self.custom_revised_hypotheses

        now = datetime.now(timezone.utc)
        ev_ids = {e.evidence_id for e in new_context.evidence}
        revised = []
        for idx, h in enumerate(previous_hypotheses.hypotheses):
            valid_supporting = [c for c in h.supporting_evidence if c.evidence_id in ev_ids]
            new_status = h.status
            if idx == 0 and valid_supporting:
                new_status = HypothesisStatus.ACTIVE
            elif idx > 0:
                new_status = HypothesisStatus.WEAKENED
            revised.append(
                h.model_copy(
                    update={
                        "revision": h.revision + 1,
                        "supporting_evidence": valid_supporting,
                        "status": new_status,
                    }
                )
            )
        return HypothesisSet(
            incident_id=previous_hypotheses.incident_id,
            hypotheses=revised,
            generated_at=now,
        )

    async def generate_remediation(
        self,
        ranked: RankedHypothesisSet,
        context: IncidentContextSnapshot,
    ) -> RemediationPlan:
        self._calls.append({
            "method": "generate_remediation",
            "incident_id": ranked.incident_id,
        })
        if self.custom_remediation is not None:
            return self.custom_remediation

        top_hyp = ranked.hypotheses[0] if ranked.hypotheses else None
        now = datetime.now(timezone.utc)
        if top_hyp is None:
            return RemediationPlan(
                plan_id=f"plan-scripted-{ranked.incident_id}",
                incident_id=ranked.incident_id,
                created_at=now,
                recommendation_available=False,
                safety_notice="No recommendation available: ranking has no hypotheses.",
                risk=RemediationRisk.BLOCKED,
                prerequisites=[],
                steps=[],
                escalation_guidance=["Escalate to primary on-call engineer."],
                unresolved_uncertainty=["No leading hypothesis available."],
            )

        cited_evidence_ids = (
            [c.evidence_id for c in top_hyp.supporting_evidence]
            if top_hyp.supporting_evidence
            else []
        )
        if not cited_evidence_ids and context.evidence:
            cited_evidence_ids = [context.evidence[0].evidence_id]
        if not cited_evidence_ids:
            cited_evidence_ids = ["ev_default"]

        rc_val = str(getattr(top_hyp.root_cause_category, "value", top_hyp.root_cause_category))

        return RemediationPlan(
            plan_id=f"plan-scripted-{ranked.incident_id}",
            incident_id=ranked.incident_id,
            created_at=now,
            recommendation_available=True,
            safety_notice="HUMAN APPROVAL MANDATORY. Review proposed operational guidance before executing any actions.",
            hypothesis_id=top_hyp.hypothesis_id,
            root_cause_category=top_hyp.root_cause_category,
            confidence=top_hyp.confidence_label,
            evidence_ids=cited_evidence_ids,
            risk=RemediationRisk.LOW,
            prerequisites=[
                f"Verify operational permissions for service '{top_hyp.affected_component}'.",
                "Ensure staging rollback validation was completed.",
            ],
            steps=[
                RemediationStep(
                    step_number=1,
                    title="Review service configuration in version control",
                    purpose=f"Mitigate identified {rc_val} condition on {top_hyp.affected_component}.",
                    instructions=[
                        f"Locate deployment or configuration changes for {top_hyp.affected_component}.",
                        "Verify health metrics after reviewing configuration.",
                    ],
                    expected_result="Healthy operational metrics restored.",
                    verification=["Check HTTP 5xx error rate drops below 0.1%."],
                    rollback_guidance=["Restore previous deployment artifact if degradation persists."],
                    requires_human_approval=True,
                )
            ],
            escalation_guidance=[
                f"Contact on-call team for service '{top_hyp.affected_component}'.",
            ],
            unresolved_uncertainty=[],
        )


class FakeReasoningProvider(ScriptedReasoningProvider):
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
        super().__init__(
            custom_assessment=custom_assessment,
            custom_plan=custom_plan,
            custom_hypotheses=custom_hypotheses,
            custom_revised_hypotheses=custom_revised_hypotheses,
        )
        self.preset = preset

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

