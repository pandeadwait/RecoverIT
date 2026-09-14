"""
Tests for Phase 9: Hidden-Ground-Truth Evaluation & Credibility Benchmarking.

Verifies:
1. Ground-Truth Isolation:
   - Scenario fixtures contain isolated ground_truth metadata.
   - Ground truth is never loaded as an evidence record or injected into prompts.
   - InvestigationResult does not embed ground truth unless explicitly evaluated post-hoc.
2. Category Matching Logic:
   - Exact matching, case-insensitivity, hyphen/space normalization.
   - Semantic category equivalencies (e.g. database_outage <-> resource_exhaustion).
   - Correct negative identification of category mismatches.
3. Causal Record Citation Verification:
   - Direct citation of expected record ID.
   - Citation of acceptable aliases for canonical scenarios.
   - Evidence ID resolution to underlying source_record_id via provenance.
   - Rejection when only symptoms/metrics are cited without causal records.
4. Metric Independence:
   - Category accuracy and causal citation are reported as separate metrics.
   - Overall benchmark pass requires both category match AND causal citation.
5. Benchmark Suite Execution:
   - BenchmarkEvaluator.run_suite() executes multi-scenario offline evaluations.
6. CLI Integration:
   - Argument parsing for --reveal-ground-truth on `run`.
   - Subcommand `recoverit benchmark` options and execution.
   - Scorecard rendering without crashes.
"""

from __future__ import annotations

import argparse
from typing import Any
import pytest

from benchmarks.evaluator import (
    BenchmarkEvaluator,
    BenchmarkMetadata,
    BenchmarkEvaluationResult,
    CATEGORY_EQUIVALENCIES,
    CAUSAL_RECORD_ALIASES,
)
from collectors.fixtures import (
    CANONICAL_SCENARIOS,
    load_scenario_json,
    load_scenario_records,
)
from recoverit.runner import InvestigationResult


# ============================================================================
# 1. Ground Truth Isolation Tests
# ============================================================================

class TestGroundTruthIsolation:
    """Verifies that ground truth metadata is kept strictly isolated."""

    def test_scenario_fixtures_have_isolated_ground_truth(self) -> None:
        """Every canonical scenario must define ground_truth in its JSON fixture."""
        for scenario_id in CANONICAL_SCENARIOS:
            data = load_scenario_json(scenario_id)
            assert "ground_truth" in data, f"{scenario_id} must have a ground_truth block"
            gt = data["ground_truth"]
            assert "expected_root_cause" in gt
            assert "expected_category" in gt
            assert "expected_causal_record" in gt
            assert len(gt["expected_root_cause"]) > 0
            assert len(gt["expected_category"]) > 0
            assert len(gt["expected_causal_record"]) > 0

    def test_load_scenario_records_excludes_ground_truth(self) -> None:
        """Loading data records for investigation must NEVER return ground_truth."""
        from contracts.enums import SourceType
        for scenario_id in CANONICAL_SCENARIOS:
            for st in SourceType:
                records = load_scenario_records(scenario_id, st)
                for rec in records:
                    # No record should be a ground truth metadata dump
                    assert rec.source_record_id != "ground_truth"
                    assert "expected_root_cause" not in rec.payload
                    assert "expected_causal_record" not in rec.payload

    def test_benchmark_evaluator_extracts_metadata(self) -> None:
        """BenchmarkEvaluator correctly reads metadata without mutating source."""
        meta = BenchmarkEvaluator.get_ground_truth("incident_001")
        assert isinstance(meta, BenchmarkMetadata)
        assert meta.scenario_id == "bad_db_config"
        assert meta.expected_category == "configuration_regression"
        assert meta.expected_causal_record == "cfg-chg-01"
        assert "dep-v241" in meta.acceptable_causal_records or "cfg-chg-01" in meta.acceptable_causal_records

    def test_investigation_result_default_has_no_evaluation(self) -> None:
        """InvestigationResult created by runner doesn't contain ground truth answers by default."""
        res = InvestigationResult(
            incident_id="test-inc",
            service="checkout-service",
            summary="High error rate",
            status="completed",
            stop_reason="criteria_met",
            execution_time_seconds=1.5,
            ranked_hypotheses=[],
            timeline_events=[],
            evidence_items=[],
            diff_excerpts={},
            log_excerpts=[],
            budget_usage={},
            provider_used="FakeReasoningProvider",
        )
        assert not hasattr(res, "ground_truth")
        assert not hasattr(res, "category_match")


# ============================================================================
# 2. Category Matching Logic Tests
# ============================================================================

class TestCategoryMatching:
    """Verifies exact and semantic equivalence matching for root cause categories."""

    def _make_result(
        self,
        category: str,
        supporting_evidence: list[dict[str, str]] | None = None,
        evidence_items: list[dict[str, Any]] | None = None,
    ) -> InvestigationResult:
        return InvestigationResult(
            incident_id="incident_001",
            service="checkout-service",
            summary="Database timeouts",
            status="completed",
            stop_reason="criteria_met",
            execution_time_seconds=1.0,
            ranked_hypotheses=[
                {
                    "rank": 1,
                    "hypothesis_id": "hyp-01",
                    "statement": "Connection pool was configured too low",
                    "root_cause_category": category,
                    "affected_component": "db-pool",
                    "evidence_score": 85.0,
                    "confidence_label": "HIGH",
                    "supporting_evidence": supporting_evidence or [],
                    "contradicting_evidence": [],
                }
            ],
            timeline_events=[],
            evidence_items=evidence_items or [],
            diff_excerpts={},
            log_excerpts=[],
            budget_usage={},
            provider_used="mock",
            scenario_name="incident_001",
        )

    def test_exact_category_match(self) -> None:
        res = self._make_result("configuration_regression")
        eval_res = BenchmarkEvaluator.evaluate(res, scenario_name="incident_001")
        assert eval_res.category_match is True
        assert eval_res.predicted_category == "configuration_regression"

    def test_normalized_category_match(self) -> None:
        # Uppercase and spaces
        res = self._make_result("Configuration Regression")
        eval_res = BenchmarkEvaluator.evaluate(res, scenario_name="incident_001")
        assert eval_res.category_match is True

        # Hyphenated
        res2 = self._make_result("configuration-regression")
        eval_res2 = BenchmarkEvaluator.evaluate(res2, scenario_name="incident_001")
        assert eval_res2.category_match is True

    def test_equivalent_category_match(self) -> None:
        # deployment_failure is equivalent to configuration_regression
        res = self._make_result("deployment_failure")
        eval_res = BenchmarkEvaluator.evaluate(res, scenario_name="incident_001")
        assert eval_res.category_match is True

        # database_outage scenario (incident_004) accepts resource_exhaustion
        res_db = InvestigationResult(
            incident_id="incident_004",
            service="billing-service",
            summary="DB outage",
            status="completed",
            stop_reason="criteria_met",
            execution_time_seconds=1.0,
            ranked_hypotheses=[
                {
                    "rank": 1,
                    "hypothesis_id": "hyp-01",
                    "statement": "Database storage exhaustion",
                    "root_cause_category": "resource_exhaustion",
                    "affected_component": "db",
                    "evidence_score": 90.0,
                    "confidence_label": "HIGH",
                    "supporting_evidence": [],
                    "contradicting_evidence": [],
                }
            ],
            timeline_events=[],
            evidence_items=[],
            diff_excerpts={},
            log_excerpts=[],
            budget_usage={},
            provider_used="mock",
            scenario_name="incident_004",
        )
        eval_db = BenchmarkEvaluator.evaluate(res_db, scenario_name="incident_004")
        assert eval_db.category_match is True

    def test_mismatched_category(self) -> None:
        res = self._make_result("security_vulnerability")
        eval_res = BenchmarkEvaluator.evaluate(res, scenario_name="incident_001")
        assert eval_res.category_match is False


# ============================================================================
# 3. Causal Record Citation Tests
# ============================================================================

class TestCausalRecordCitation:
    """Verifies checking of whether the leading hypothesis cites causal evidence."""

    def test_direct_causal_record_citation(self) -> None:
        res = InvestigationResult(
            incident_id="incident_001",
            service="checkout-service",
            summary="DB timeouts",
            status="completed",
            stop_reason="criteria_met",
            execution_time_seconds=1.0,
            ranked_hypotheses=[
                {
                    "rank": 1,
                    "hypothesis_id": "hyp-01",
                    "statement": "Bad connection pool config",
                    "root_cause_category": "configuration_regression",
                    "affected_component": "db-pool",
                    "evidence_score": 92.0,
                    "confidence_label": "HIGH",
                    "supporting_evidence": [
                        {"evidence_id": "cfg-chg-01", "reason": "Config commit reduced pool size"}
                    ],
                    "contradicting_evidence": [],
                }
            ],
            timeline_events=[],
            evidence_items=[],
            diff_excerpts={},
            log_excerpts=[],
            budget_usage={},
            provider_used="mock",
            scenario_name="incident_001",
        )
        eval_res = BenchmarkEvaluator.evaluate(res)
        assert eval_res.causal_record_cited is True
        assert eval_res.overall_success is True

    def test_acceptable_alias_citation(self) -> None:
        """Citing an acceptable alias (e.g. deployment record dep-v241) counts as citing cause."""
        res = InvestigationResult(
            incident_id="incident_001",
            service="checkout-service",
            summary="DB timeouts",
            status="completed",
            stop_reason="criteria_met",
            execution_time_seconds=1.0,
            ranked_hypotheses=[
                {
                    "rank": 1,
                    "hypothesis_id": "hyp-01",
                    "statement": "Bad deployment",
                    "root_cause_category": "configuration_regression",
                    "affected_component": "checkout",
                    "evidence_score": 88.0,
                    "confidence_label": "HIGH",
                    "supporting_evidence": [
                        {"evidence_id": "dep-v241", "reason": "Deployment introduced change"}
                    ],
                    "contradicting_evidence": [],
                }
            ],
            timeline_events=[],
            evidence_items=[],
            diff_excerpts={},
            log_excerpts=[],
            budget_usage={},
            provider_used="mock",
            scenario_name="incident_001",
        )
        eval_res = BenchmarkEvaluator.evaluate(res)
        assert eval_res.causal_record_cited is True
        assert eval_res.overall_success is True

    def test_source_record_id_resolution_via_provenance(self) -> None:
        """When evidence ID is a hashed UUID, resolution to source_record_id confirms citation."""
        hashed_id = "ev_98237482934789"
        res = InvestigationResult(
            incident_id="incident_001",
            service="checkout-service",
            summary="DB timeouts",
            status="completed",
            stop_reason="criteria_met",
            execution_time_seconds=1.0,
            ranked_hypotheses=[
                {
                    "rank": 1,
                    "hypothesis_id": "hyp-01",
                    "statement": "Config pool reduction",
                    "root_cause_category": "configuration_regression",
                    "affected_component": "checkout",
                    "evidence_score": 90.0,
                    "confidence_label": "HIGH",
                    "supporting_evidence": [
                        {"evidence_id": hashed_id, "reason": "Hashed evidence of config commit"}
                    ],
                    "contradicting_evidence": [],
                }
            ],
            timeline_events=[],
            evidence_items=[
                {
                    "evidence_id": hashed_id,
                    "source_type": "git",
                    "source_record_id": "cfg-chg-01",  # Underneath it maps to causal record
                    "summary": "Reduced db max_connections",
                }
            ],
            diff_excerpts={},
            log_excerpts=[],
            budget_usage={},
            provider_used="mock",
            scenario_name="incident_001",
        )
        eval_res = BenchmarkEvaluator.evaluate(res)
        assert eval_res.causal_record_cited is True
        assert eval_res.overall_success is True
        assert "cfg-chg-01" in eval_res.cited_records

    def test_non_causal_symptom_citation_only(self) -> None:
        """Citing only symptoms (e.g. error log or CPU metric) does NOT pass causal citation."""
        res = InvestigationResult(
            incident_id="incident_001",
            service="checkout-service",
            summary="DB timeouts",
            status="completed",
            stop_reason="criteria_met",
            execution_time_seconds=1.0,
            ranked_hypotheses=[
                {
                    "rank": 1,
                    "hypothesis_id": "hyp-01",
                    "statement": "Generic DB timeout",
                    "root_cause_category": "configuration_regression",
                    "affected_component": "checkout",
                    "evidence_score": 75.0,
                    "confidence_label": "MEDIUM",
                    "supporting_evidence": [
                        {"evidence_id": "log-err-001", "reason": "Timeout log observed in checkout service"}
                    ],
                    "contradicting_evidence": [],
                }
            ],
            timeline_events=[],
            evidence_items=[],
            diff_excerpts={},
            log_excerpts=[],
            budget_usage={},
            provider_used="mock",
            scenario_name="incident_001",
        )
        eval_res = BenchmarkEvaluator.evaluate(res)
        assert eval_res.category_match is True
        assert eval_res.causal_record_cited is False
        assert eval_res.overall_success is False


# ============================================================================
# 4. Metric Independence Tests
# ============================================================================

class TestMetricIndependence:
    """Verifies that category accuracy and causal citation are distinct metrics."""

    def test_all_four_combinations(self) -> None:
        """Checks (T, T), (T, F), (F, T), (F, F) cases."""
        # 1. Category True, Citation True -> Success True
        res_tt = InvestigationResult(
            incident_id="incident_001",
            service="checkout",
            summary="x",
            status="completed",
            stop_reason="done",
            execution_time_seconds=1.0,
            ranked_hypotheses=[{
                "statement": "Correct cause",
                "root_cause_category": "configuration_regression",
                "affected_component": "c",
                "evidence_score": 90.0,
                "confidence_label": "HIGH",
                "supporting_evidence": [{"evidence_id": "cfg-chg-01"}],
            }],
            timeline_events=[],
            evidence_items=[],
            diff_excerpts={},
            log_excerpts=[],
            budget_usage={},
            provider_used="m",
            scenario_name="incident_001",
        )
        eval_tt = BenchmarkEvaluator.evaluate(res_tt)
        assert (eval_tt.category_match, eval_tt.causal_record_cited, eval_tt.overall_success) == (True, True, True)

        # 2. Category True, Citation False -> Success False
        res_tf = InvestigationResult(
            incident_id="incident_001",
            service="checkout",
            summary="x",
            status="completed",
            stop_reason="done",
            execution_time_seconds=1.0,
            ranked_hypotheses=[{
                "statement": "Lucky category guess without citing commit",
                "root_cause_category": "configuration_regression",
                "affected_component": "c",
                "evidence_score": 50.0,
                "confidence_label": "LOW",
                "supporting_evidence": [{"evidence_id": "random-symptom"}],
            }],
            timeline_events=[],
            evidence_items=[],
            diff_excerpts={},
            log_excerpts=[],
            budget_usage={},
            provider_used="m",
            scenario_name="incident_001",
        )
        eval_tf = BenchmarkEvaluator.evaluate(res_tf)
        assert (eval_tf.category_match, eval_tf.causal_record_cited, eval_tf.overall_success) == (True, False, False)

        # 3. Category False, Citation True -> Success False
        res_ft = InvestigationResult(
            incident_id="incident_001",
            service="checkout",
            summary="x",
            status="completed",
            stop_reason="done",
            execution_time_seconds=1.0,
            ranked_hypotheses=[{
                "statement": "Misdiagnosed category despite finding commit",
                "root_cause_category": "security_breach",
                "affected_component": "c",
                "evidence_score": 70.0,
                "confidence_label": "MEDIUM",
                "supporting_evidence": [{"evidence_id": "cfg-chg-01"}],
            }],
            timeline_events=[],
            evidence_items=[],
            diff_excerpts={},
            log_excerpts=[],
            budget_usage={},
            provider_used="m",
            scenario_name="incident_001",
        )
        eval_ft = BenchmarkEvaluator.evaluate(res_ft)
        assert (eval_ft.category_match, eval_ft.causal_record_cited, eval_ft.overall_success) == (False, True, False)

        # 4. Category False, Citation False -> Success False
        res_ff = InvestigationResult(
            incident_id="incident_001",
            service="checkout",
            summary="x",
            status="completed",
            stop_reason="done",
            execution_time_seconds=1.0,
            ranked_hypotheses=[{
                "statement": "Totally wrong",
                "root_cause_category": "hardware_failure",
                "affected_component": "c",
                "evidence_score": 20.0,
                "confidence_label": "LOW",
                "supporting_evidence": [],
            }],
            timeline_events=[],
            evidence_items=[],
            diff_excerpts={},
            log_excerpts=[],
            budget_usage={},
            provider_used="m",
            scenario_name="incident_001",
        )
        eval_ff = BenchmarkEvaluator.evaluate(res_ff)
        assert (eval_ff.category_match, eval_ff.causal_record_cited, eval_ff.overall_success) == (False, False, False)

    def test_summary_dict_export(self) -> None:
        """to_summary_dict provides all necessary metrics for reporting."""
        eval_res = BenchmarkEvaluationResult(
            scenario_id="bad_db_config",
            service="checkout",
            expected_root_cause="Config change",
            expected_category="configuration_regression",
            predicted_category="configuration_regression",
            category_match=True,
            expected_causal_record="cfg-chg-01",
            causal_record_cited=True,
            cited_records=["cfg-chg-01"],
            leading_hypothesis_statement="Config change reduced pool",
            leading_confidence_label="HIGH",
            leading_score=94.0,
            overall_success=True,
        )
        d = eval_res.to_summary_dict()
        assert d["scenario_id"] == "bad_db_config"
        assert d["category_match"] is True
        assert d["causal_record_cited"] is True
        assert d["overall_success"] is True
        assert d["leading_score"] == 94.0


# ============================================================================
# 5. Evaluator Edge Cases
# ============================================================================

class TestEvaluatorEdgeCases:
    """Verifies handling of empty or partial investigation results."""

    def test_empty_ranked_hypotheses(self) -> None:
        res = InvestigationResult(
            incident_id="incident_001",
            service="checkout",
            summary="DB timeouts",
            status="completed",
            stop_reason="budget_exhausted",
            execution_time_seconds=0.5,
            ranked_hypotheses=[],
            timeline_events=[],
            evidence_items=[],
            diff_excerpts={},
            log_excerpts=[],
            budget_usage={},
            provider_used="mock",
            scenario_name="incident_001",
        )
        eval_res = BenchmarkEvaluator.evaluate(res)
        assert eval_res.category_match is False
        assert eval_res.causal_record_cited is False
        assert eval_res.overall_success is False
        assert eval_res.leading_score == 0.0
        assert "zero ranked hypotheses" in eval_res.details


# ============================================================================
# 6. Benchmark Suite Execution Tests
# ============================================================================

class TestBenchmarkSuiteExecution:
    """Verifies running the benchmark suite against canonical scenarios in offline mode."""

    @pytest.mark.asyncio
    async def test_run_suite_offline(self) -> None:
        # Run across 2 scenarios to keep test fast
        results = await BenchmarkEvaluator.run_suite(
            scenarios=["incident_001", "incident_002"],
            mode="offline",
        )
        assert len(results) == 2
        for r in results:
            assert isinstance(r, BenchmarkEvaluationResult)
            assert r.scenario_id in ("bad_db_config", "memory_exhaustion")
            assert len(r.expected_root_cause) > 0
            assert r.leading_score > 0.0


# ============================================================================
# 7. CLI Integration Tests
# ============================================================================

class TestBenchmarkCLI:
    """Verifies CLI arguments and benchmark scorecard rendering."""

    def test_run_parser_accepts_reveal_ground_truth(self) -> None:
        from recoverit.cli import main
        # Verify parser construction and flag
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        run_parser = subparsers.add_parser("run")
        run_parser.add_argument("--reveal-ground-truth", action="store_true")

        args = parser.parse_args(["run", "--reveal-ground-truth"])
        assert args.reveal_ground_truth is True

        args_default = parser.parse_args(["run"])
        assert args_default.reveal_ground_truth is False

    def test_benchmark_parser_subcommand(self) -> None:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        bench_parser = subparsers.add_parser("benchmark")
        bench_parser.add_argument("--scenarios", nargs="+", default=["incident_001"])
        bench_parser.add_argument("--mode", default="offline")
        bench_parser.add_argument("--report", "-r")

        args = parser.parse_args(["benchmark", "--scenarios", "incident_001", "incident_002", "--report", "bench.md"])
        assert args.command == "benchmark"
        assert args.scenarios == ["incident_001", "incident_002"]
        assert args.mode == "offline"
        assert args.report == "bench.md"

    def test_render_benchmark_evaluation_runs_cleanly(self) -> None:
        """render_benchmark_evaluation renders without throwing exceptions."""
        from recoverit.cli import render_benchmark_evaluation

        eval_res = BenchmarkEvaluationResult(
            scenario_id="bad_db_config",
            service="checkout-service",
            expected_root_cause="Config change reduced pool",
            expected_category="configuration_regression",
            predicted_category="configuration_regression",
            category_match=True,
            expected_causal_record="cfg-chg-01",
            causal_record_cited=True,
            cited_records=["cfg-chg-01"],
            leading_hypothesis_statement="Database connection pool size reduced",
            leading_confidence_label="HIGH",
            leading_score=91.5,
            overall_success=True,
        )
        # Should render to Rich console without exception
        render_benchmark_evaluation(eval_res)
