"""
Contract tests for all Person 3 schemas.

Verifies:
- All schemas are constructible with valid data.
- All schemas round-trip through JSON serialization.
- Required fields are enforced.
- Enumerations are complete and match contract definitions.
- schema_version is present on every contract.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

# -- Enumerations --
from contracts.common import (
    ConfidenceLabel,
    EvidenceType,
    HypothesisStatus,
    InformationPriority,
    InformationValueLevel,
    InvestigationStatus,
    Reliability,
    RelationshipCreator,
    RelationshipType,
    RootCauseCategory,
    Severity,
    SourceCoverageStatus,
    SourceStatus,
    SourceType,
    StopReason,
    TimelineCategory,
)

# -- Error contracts --
from contracts.errors.schemas import StructuredError

# -- Incident contracts --
from contracts.incident.schemas import IncidentAlert, IncidentSeed

# -- Collection contracts --
from contracts.collection.schemas import (
    RawEvidenceBatch,
    RawRecord,
    SourceCapability,
    SourceCapabilityCatalog,
    SourceResult,
)

# -- Evidence contracts --
from contracts.evidence.schemas import (
    EvidenceProvenance,
    EvidenceQuality,
    EvidenceRecord,
    EvidenceSummaryProjection,
    IncidentContextSnapshot,
    IncidentSummary,
    TemporalRelationship,
    TimelineEvent,
    TimelineEventProjection,
)

# -- Investigation contracts --
from contracts.investigation.schemas import (
    EvidenceQueryPlan,
    EvidenceQueryPlanQuery,
    InvestigationBudget,
    KnownFact,
    MissingInformationAssessment,
    MissingInformationItem,
)

# -- Hypothesis contracts --
from contracts.hypothesis.schemas import (
    BudgetUsage,
    EvidenceCitation,
    Hypothesis,
    HypothesisSet,
    RankedHypothesis,
    RankedHypothesisSet,
    ScoreBreakdown,
)


# =====================================================================
# Helpers
# =====================================================================

NOW = datetime.now(timezone.utc)


def _round_trip(model_instance):
    """Serialize to JSON string, deserialize back, and assert equality."""
    json_str = model_instance.model_dump_json()
    parsed = json.loads(json_str)
    # Verify it's valid JSON and can be re-parsed
    reconstructed = type(model_instance).model_validate(parsed)
    assert reconstructed == model_instance
    return reconstructed


# =====================================================================
# Enum Completeness
# =====================================================================


class TestEnumerations:
    """Verify all enumerations have the expected members."""

    def test_severity_values(self):
        assert set(Severity) == {Severity.INFO, Severity.WARNING, Severity.CRITICAL}

    def test_source_type_values(self):
        expected = {"logs", "metrics", "changes", "deployments",
                    "pipelines", "configuration", "health", "operator"}
        assert {st.value for st in SourceType} == expected

    def test_hypothesis_status_values(self):
        expected = {"active", "weakened", "rejected", "selected"}
        assert {hs.value for hs in HypothesisStatus} == expected

    def test_confidence_label_values(self):
        expected = {"low", "medium", "high"}
        assert {cl.value for cl in ConfidenceLabel} == expected

    def test_root_cause_category_values(self):
        # At least the primary categories from the work division doc
        assert RootCauseCategory.CONFIGURATION_REGRESSION.value == "configuration_regression"
        assert RootCauseCategory.RESOURCE_EXHAUSTION.value == "resource_exhaustion"
        assert RootCauseCategory.DATABASE_OUTAGE.value == "database_outage"
        assert len(RootCauseCategory) >= 5

    def test_stop_reason_values(self):
        expected = {"sufficient_evidence", "budget_exhausted",
                    "insufficient_evidence", "sources_unavailable",
                    "repeated_invalid_output"}
        assert {sr.value for sr in StopReason} == expected

    def test_investigation_status_values(self):
        expected = {"completed", "inconclusive"}
        assert {s.value for s in InvestigationStatus} == expected

    def test_timeline_category_values(self):
        expected = {"change", "deployment", "symptom", "alert",
                    "action", "verification"}
        assert {tc.value for tc in TimelineCategory} == expected

    def test_relationship_type_values(self):
        expected = {"PRECEDES", "COINCIDES_WITH", "SUPPORTS", "CONTRADICTS",
                    "DEPLOYED_FROM", "AFFECTS", "OBSERVED_ON", "PREDICTS"}
        assert {rt.value for rt in RelationshipType} == expected


# =====================================================================
# Error Contracts
# =====================================================================


class TestStructuredError:
    def test_construction_and_round_trip(self):
        err = StructuredError(
            code="SOURCE_UNAVAILABLE",
            message="Metrics source timed out.",
            retryable=True,
            source="metrics",
            details={"query_id": "qry_123"},
        )
        assert err.schema_version == "1.0"
        _round_trip(err)

    def test_required_fields(self):
        with pytest.raises(Exception):
            StructuredError()  # missing code and message


# =====================================================================
# Incident Contracts
# =====================================================================


class TestIncidentAlert:
    def test_construction_and_round_trip(self):
        alert = IncidentAlert(
            external_alert_id="alert-001",
            service="payment-api",
            environment="simulation",
            severity=Severity.CRITICAL,
            detected_at=NOW,
            message="HTTP 500 rate exceeded threshold",
            labels={"region": "local"},
        )
        assert alert.schema_version == "1.0"
        result = _round_trip(alert)
        assert result.service == "payment-api"

    def test_required_fields(self):
        with pytest.raises(Exception):
            IncidentAlert(
                external_alert_id="alert-001",
                # missing service, environment, severity, etc.
            )


class TestIncidentSeed:
    def test_construction_and_round_trip(self):
        seed = IncidentSeed(
            incident_id="inc_001",
            external_alert_id="alert-001",
            service="payment-api",
            environment="simulation",
            severity=Severity.CRITICAL,
            detected_at=NOW,
            received_at=NOW,
            summary="HTTP 500 rate exceeded threshold",
            labels={"region": "local"},
        )
        assert seed.schema_version == "1.0"
        _round_trip(seed)


# =====================================================================
# Collection Contracts
# =====================================================================


class TestSourceCapabilityCatalog:
    def test_construction_and_round_trip(self):
        catalog = SourceCapabilityCatalog(
            incident_id="inc_001",
            generated_at=NOW,
            sources=[
                SourceCapability(
                    source_type=SourceType.LOGS,
                    available=True,
                    supported_query_fields=[
                        "service", "start_time", "end_time", "severity",
                        "pattern", "limit",
                    ],
                    maximum_window_seconds=86400,
                    maximum_items=1000,
                ),
                SourceCapability(
                    source_type=SourceType.METRICS,
                    available=True,
                    supported_query_fields=[
                        "service", "metric_name", "start_time", "end_time",
                        "aggregation",
                    ],
                    maximum_window_seconds=604800,
                    maximum_items=500,
                ),
                SourceCapability(
                    source_type=SourceType.CONFIGURATION,
                    available=True,
                    supported_query_fields=[
                        "service", "start_time", "end_time", "keys",
                    ],
                    maximum_window_seconds=604800,
                    maximum_items=200,
                ),
            ],
        )
        assert catalog.schema_version == "1.0"
        result = _round_trip(catalog)
        assert len(result.sources) == 3


class TestRawEvidenceBatch:
    def test_construction_and_round_trip(self):
        batch = RawEvidenceBatch(
            incident_id="inc_001",
            plan_id="plan_002",
            batch_id="batch_009",
            collected_at=NOW,
            results=[
                SourceResult(
                    query_id="qry_101",
                    source_type=SourceType.LOGS,
                    source_adapter="configured-log-adapter",
                    source_status=SourceStatus.OK,
                    truncated=False,
                    records=[
                        RawRecord(
                            source_record_id="log-998",
                            event_time=NOW,
                            observed_at=NOW,
                            content_type="application_log",
                            payload={
                                "level": "error",
                                "message": "Database connection timeout",
                                "service": "payment-api",
                            },
                        ),
                    ],
                    warnings=[],
                ),
            ],
            errors=[],
        )
        assert batch.schema_version == "1.0"
        result = _round_trip(batch)
        assert len(result.results) == 1
        assert result.results[0].records[0].payload["level"] == "error"


# =====================================================================
# Evidence Contracts
# =====================================================================


class TestEvidenceRecord:
    def test_construction_and_round_trip(self):
        record = EvidenceRecord(
            evidence_id="ev_201",
            incident_id="inc_001",
            source_type=SourceType.LOGS,
            evidence_type=EvidenceType.ERROR_EVENT,
            service="payment-api",
            event_time=NOW,
            observed_at=NOW,
            collected_at=NOW,
            summary="Payment API logged a database connection timeout.",
            attributes={"level": "error", "error_signature": "database_connection_timeout"},
            provenance=EvidenceProvenance(
                batch_id="batch_009",
                query_id="qry_101",
                source_record_id="log-998",
                source_adapter="configured-log-adapter",
                raw_payload_hash="sha256:abc123",
            ),
            quality=EvidenceQuality(
                reliability=Reliability.HIGH,
                freshness_seconds=224,
                truncated_source=False,
                redactions_applied=False,
            ),
        )
        assert record.schema_version == "1.0"
        _round_trip(record)


class TestIncidentContextSnapshot:
    def test_construction_and_round_trip(self):
        snapshot = IncidentContextSnapshot(
            snapshot_id="ctx_501",
            incident_id="inc_001",
            revision=3,
            created_at=NOW,
            incident=IncidentSummary(
                service="payment-api",
                environment="simulation",
                severity=Severity.CRITICAL,
                detected_at=NOW,
                summary="HTTP 500 rate exceeded threshold",
            ),
            evidence=[
                EvidenceSummaryProjection(
                    evidence_id="ev_201",
                    source_type=SourceType.LOGS,
                    evidence_type=EvidenceType.ERROR_EVENT,
                    event_time=NOW,
                    summary="Payment API logged a database connection timeout.",
                    quality=EvidenceQuality(
                        reliability=Reliability.HIGH,
                        freshness_seconds=224,
                    ),
                ),
            ],
            timeline=[
                TimelineEventProjection(
                    timeline_event_id="tle_301",
                    event_time=NOW,
                    category=TimelineCategory.SYMPTOM,
                    title="Database connection timeouts began",
                    evidence_ids=["ev_201"],
                ),
            ],
            relationships=[],
            source_coverage={
                "logs": SourceCoverageStatus.AVAILABLE,
                "metrics": SourceCoverageStatus.NOT_QUERIED,
                "changes": SourceCoverageStatus.NOT_QUERIED,
                "deployments": SourceCoverageStatus.NOT_QUERIED,
                "pipelines": SourceCoverageStatus.NOT_QUERIED,
                "configuration": SourceCoverageStatus.NOT_QUERIED,
            },
            warnings=[],
        )
        assert snapshot.schema_version == "1.0"
        result = _round_trip(snapshot)
        assert result.revision == 3
        assert len(result.evidence) == 1


# =====================================================================
# Investigation Contracts
# =====================================================================


class TestInvestigationBudget:
    def test_defaults(self):
        budget = InvestigationBudget()
        assert budget.max_rounds == 6
        assert budget.max_queries == 12
        assert budget.max_elapsed_seconds == 900
        assert budget.max_reasoning_calls == 10
        assert budget.minimum_hypotheses == 2
        assert budget.maximum_hypotheses == 5
        _round_trip(budget)

    def test_custom_values(self):
        budget = InvestigationBudget(
            max_rounds=3,
            max_queries=6,
            max_elapsed_seconds=300,
            max_reasoning_calls=5,
            max_input_units=50000,
            max_output_units=10000,
            minimum_hypotheses=2,
            maximum_hypotheses=4,
        )
        result = _round_trip(budget)
        assert result.max_rounds == 3


class TestMissingInformationAssessment:
    def test_construction_and_round_trip(self):
        assessment = MissingInformationAssessment(
            incident_id="inc_001",
            assessment_id="mia_601",
            known_facts=[
                KnownFact(
                    statement="HTTP 500 errors exceeded the alert threshold.",
                    evidence_ids=["ev_100"],
                ),
            ],
            missing_information=[
                MissingInformationItem(
                    information_id="need_01",
                    question="Was a deployment completed shortly before the error increase?",
                    reason="This distinguishes a recent-change regression from an independent outage.",
                    priority=InformationPriority.HIGH,
                    candidate_sources=[SourceType.DEPLOYMENTS, SourceType.CHANGES],
                    resolved=False,
                ),
            ],
            unavailable_information=[],
            recommended_stop=False,
        )
        assert assessment.schema_version == "1.0"
        _round_trip(assessment)


class TestEvidenceQueryPlan:
    def test_construction_and_round_trip(self):
        plan = EvidenceQueryPlan(
            incident_id="inc_001",
            plan_id="plan_002",
            round=2,
            queries=[
                EvidenceQueryPlanQuery(
                    query_id="qry_101",
                    source_type=SourceType.LOGS,
                    question="Which errors appeared immediately after deployment?",
                    parameters={
                        "service": "payment-api",
                        "start_time": "2026-09-12T10:20:00Z",
                        "end_time": "2026-09-12T10:35:00Z",
                        "severity": ["error", "critical"],
                        "limit": 200,
                    },
                    related_information_ids=["need_02"],
                    discriminates_hypothesis_ids=["hyp_01", "hyp_02"],
                    expected_information_value=InformationValueLevel.HIGH,
                ),
            ],
            stop_reason=None,
        )
        assert plan.schema_version == "1.0"
        result = _round_trip(plan)
        assert len(result.queries) == 1
        assert result.queries[0].query_id == "qry_101"

    def test_empty_queries_requires_stop_reason(self):
        """A plan with no queries should have a stop_reason (business rule)."""
        plan = EvidenceQueryPlan(
            incident_id="inc_001",
            plan_id="plan_003",
            round=3,
            queries=[],
            stop_reason=StopReason.SUFFICIENT_EVIDENCE,
        )
        assert plan.stop_reason is not None
        _round_trip(plan)


# =====================================================================
# Hypothesis Contracts
# =====================================================================


class TestHypothesis:
    def test_construction_and_round_trip(self):
        hyp = Hypothesis(
            hypothesis_id="hyp_01",
            incident_id="inc_001",
            revision=2,
            statement="The deployment introduced an invalid database connection configuration.",
            root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
            affected_component="payment-api",
            supporting_evidence=[
                EvidenceCitation(
                    evidence_id="ev_201",
                    reason="Connection timeouts began after the new deployment.",
                ),
            ],
            contradicting_evidence=[
                EvidenceCitation(
                    evidence_id="ev_205",
                    reason="One timeout occurred before the deployment.",
                ),
            ],
            missing_information_ids=["need_04"],
            testable_prediction="The configuration diff should contain a database endpoint change.",
            status=HypothesisStatus.ACTIVE,
        )
        assert hyp.schema_version == "1.0"
        _round_trip(hyp)


class TestRankedHypothesisSet:
    def test_completed_round_trip(self):
        ranked = RankedHypothesisSet(
            incident_id="inc_001",
            context_snapshot_id="ctx_501",
            ranking_id="rank_701",
            created_at=NOW,
            status=InvestigationStatus.COMPLETED,
            hypotheses=[
                RankedHypothesis(
                    rank=1,
                    hypothesis_id="hyp_01",
                    statement="The deployment introduced an invalid database connection configuration.",
                    root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
                    affected_component="payment-api",
                    evidence_score=84.0,
                    confidence_label=ConfidenceLabel.HIGH,
                    supporting_evidence=[
                        EvidenceCitation(
                            evidence_id="ev_201",
                            reason="Database timeouts began after deployment.",
                        ),
                        EvidenceCitation(
                            evidence_id="ev_203",
                            reason="The configuration change modified the database endpoint.",
                        ),
                    ],
                    contradicting_evidence=[],
                    unresolved_questions=[],
                    score_breakdown=ScoreBreakdown(
                        independent_source_support=22.0,
                        symptom_coverage=18.0,
                        temporal_consistency=14.0,
                        change_consistency=14.0,
                        specificity=8.0,
                        prediction_support=8.0,
                        contradiction_penalty=0.0,
                        missing_evidence_penalty=0.0,
                    ),
                ),
            ],
            remaining_uncertainty=[
                "The exact runtime value of the database endpoint was not independently observed.",
            ],
            budget_usage=BudgetUsage(
                rounds=3,
                queries=7,
                reasoning_calls=5,
            ),
        )
        assert ranked.schema_version == "1.0"
        result = _round_trip(ranked)
        assert result.status == "completed"
        assert result.hypotheses[0].evidence_score == 84.0
        assert result.hypotheses[0].rank == 1

    def test_inconclusive_round_trip(self):
        ranked = RankedHypothesisSet(
            incident_id="inc_001",
            context_snapshot_id="ctx_501",
            ranking_id="rank_702",
            created_at=NOW,
            status=InvestigationStatus.INCONCLUSIVE,
            hypotheses=[],
            remaining_uncertainty=[
                "Logs and deployment history were unavailable.",
            ],
            stop_reason=StopReason.INSUFFICIENT_EVIDENCE,
            budget_usage=BudgetUsage(
                rounds=2,
                queries=4,
                reasoning_calls=3,
            ),
        )
        assert ranked.status == "inconclusive"
        assert ranked.stop_reason == "insufficient_evidence"
        _round_trip(ranked)

    def test_schema_version_present(self):
        """Every contract must have schema_version."""
        ranked = RankedHypothesisSet(
            incident_id="inc_001",
            context_snapshot_id="ctx_501",
            ranking_id="rank_701",
            created_at=NOW,
            status=InvestigationStatus.COMPLETED,
            hypotheses=[],
            remaining_uncertainty=[],
            budget_usage=BudgetUsage(),
        )
        data = json.loads(ranked.model_dump_json())
        assert "schema_version" in data
        assert data["schema_version"] == "1.0"
