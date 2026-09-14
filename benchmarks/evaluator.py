"""
Benchmark Ground-Truth Evaluator for RecoverIT.

Evaluates an InvestigationResult against hidden ground-truth metadata
derived from scenario fixtures without exposing that ground truth
to the runtime reasoning loop or prompt templates.

See CREDIBILITY_IMPROVEMENT_PLAN.md §Phase 9.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from collectors.fixtures.data_loader import (
    CANONICAL_SCENARIOS,
    load_scenario_json,
    resolve_scenario_name,
)
from recoverit.runner import InvestigationResult

logger = logging.getLogger(__name__)

# Category equivalency mapping to accommodate semantically equivalent root causes
CATEGORY_EQUIVALENCIES: dict[str, set[str]] = {
    "configuration_regression": {"configuration_regression", "deployment_failure", "code_defect"},
    "resource_exhaustion": {"resource_exhaustion", "database_outage", "infrastructure_outage", "infrastructure_failure"},
    "database_outage": {"database_outage", "resource_exhaustion", "infrastructure_outage", "infrastructure_failure"},
    "infrastructure_outage": {"infrastructure_outage", "infrastructure_failure", "database_outage", "resource_exhaustion"},
    "dependency_incompatibility": {"dependency_incompatibility", "code_defect", "deployment_failure"},
    "external_dependency_failure": {"external_dependency_failure", "infrastructure_failure"},
}

# Causal record aliases/equivalencies for scenario fixtures
CAUSAL_RECORD_ALIASES: dict[str, set[str]] = {
    "bad_db_config": {"cfg-chg-01", "dep-v241", "dep_v2.4.1", "abc12347890def"},
    "memory_exhaustion": {"commit-mem-01", "mem9876543210aa", "metric-mem-usage", "log-oom-001"},
    "dependency_incompatibility": {"commit-dep-01", "log-dep-001", "dep-auth-401"},
    "real_db_outage": {"log-outage-001", "log-outage-002", "metric-billing-500", "metric-db-disk-usage"},
    "coincidental_deployment": {"log-ext-001", "metric-ext-latency", "metric-checkout-500"},
}


@dataclass(frozen=True)
class BenchmarkMetadata:
    """Hidden ground truth for an incident scenario."""
    scenario_id: str
    expected_root_cause: str
    expected_category: str
    expected_causal_record: str
    acceptable_causal_records: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class BenchmarkEvaluationResult:
    """Outcome of evaluating an investigation against hidden ground truth."""
    scenario_id: str
    service: str
    expected_root_cause: str
    expected_category: str
    predicted_category: str
    category_match: bool
    expected_causal_record: str
    causal_record_cited: bool
    cited_records: list[str]
    leading_hypothesis_statement: str
    leading_confidence_label: str
    leading_score: float
    overall_success: bool
    details: str = ""

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "service": self.service,
            "expected_category": self.expected_category,
            "predicted_category": self.predicted_category,
            "category_match": self.category_match,
            "expected_causal_record": self.expected_causal_record,
            "causal_record_cited": self.causal_record_cited,
            "cited_records": self.cited_records,
            "overall_success": self.overall_success,
            "leading_score": self.leading_score,
            "leading_confidence": self.leading_confidence_label,
        }


class BenchmarkEvaluator:
    """Evaluates completed investigations against isolated benchmark ground truth."""

    @staticmethod
    def get_ground_truth(scenario_name: str) -> BenchmarkMetadata:
        """Extract ground-truth metadata from scenario fixture JSON without exposing records to agents."""
        resolved = resolve_scenario_name(scenario_name)
        data = load_scenario_json(scenario_name)
        gt = data.get("ground_truth", {})
        expected_cat = gt.get("expected_category", "unknown")
        expected_rec = gt.get("expected_causal_record", "")

        acceptable = set(CAUSAL_RECORD_ALIASES.get(resolved, set()))
        if expected_rec:
            acceptable.add(expected_rec)

        return BenchmarkMetadata(
            scenario_id=resolved,
            expected_root_cause=gt.get("expected_root_cause", ""),
            expected_category=expected_cat,
            expected_causal_record=expected_rec,
            acceptable_causal_records=acceptable,
        )

    @classmethod
    def evaluate(
        cls,
        result: InvestigationResult,
        scenario_name: str | None = None,
    ) -> BenchmarkEvaluationResult:
        """
        Evaluate a completed InvestigationResult against hidden ground truth.

        Evaluates:
        1. Category Match: Does the predicted root cause category match the expected category?
        2. Causal Record Citation: Does the leading hypothesis cite the expected causal record?
        3. Overall Success: Both category match and causal citation pass.
        """
        target_scenario = scenario_name or getattr(result, "scenario_name", None) or result.incident_id
        if target_scenario.startswith("inc-"):
            target_scenario = target_scenario[4:]

        metadata = cls.get_ground_truth(target_scenario)

        if not result.ranked_hypotheses:
            return BenchmarkEvaluationResult(
                scenario_id=metadata.scenario_id,
                service=result.service,
                expected_root_cause=metadata.expected_root_cause,
                expected_category=metadata.expected_category,
                predicted_category="none",
                category_match=False,
                expected_causal_record=metadata.expected_causal_record,
                causal_record_cited=False,
                cited_records=[],
                leading_hypothesis_statement="No hypotheses generated",
                leading_confidence_label="NONE",
                leading_score=0.0,
                overall_success=False,
                details="Investigation concluded with zero ranked hypotheses.",
            )

        leading = result.ranked_hypotheses[0]
        pred_cat = str(leading.get("root_cause_category", "unknown")).lower()
        exp_cat = metadata.expected_category.lower()

        # Category matching with normalizations and equivalencies
        def clean_cat(c: str) -> str:
            return c.lower().replace("-", "_").replace(" ", "_").strip()

        clean_pred = clean_cat(pred_cat)
        clean_exp = clean_cat(exp_cat)

        allowed_cats = CATEGORY_EQUIVALENCIES.get(clean_exp, {clean_exp})
        category_match = (clean_pred == clean_exp) or (clean_pred in allowed_cats)

        # Build lookup from evidence_id to source_record_id
        ev_id_to_source_rec: dict[str, str] = {}
        for ev in result.evidence_items:
            ev_id = ev.get("evidence_id")
            src_rec = ev.get("source_record_id")
            if ev_id and src_rec:
                ev_id_to_source_rec[ev_id] = src_rec

        # Resolve cited records from leading hypothesis
        cited_records: list[str] = []
        for c in leading.get("supporting_evidence", []):
            cid = c.get("evidence_id", "")
            if cid:
                cited_records.append(cid)
                if cid in ev_id_to_source_rec:
                    cited_records.append(ev_id_to_source_rec[cid])

        # Check causal record citation
        causal_record_cited = False
        target_acceptable = metadata.acceptable_causal_records
        for r in cited_records:
            if r in target_acceptable:
                causal_record_cited = True
                break
            if any(acc in r or r in acc for acc in target_acceptable if len(acc) >= 6):
                causal_record_cited = True
                break

        overall_success = category_match and causal_record_cited

        details_parts = []
        if category_match:
            details_parts.append("Category match confirmed.")
        else:
            details_parts.append(f"Category mismatch: expected '{metadata.expected_category}', got '{pred_cat}'.")

        if causal_record_cited:
            details_parts.append("Causal record cited in leading hypothesis.")
        else:
            details_parts.append(
                f"Expected causal record '{metadata.expected_causal_record}' not cited (cited: {cited_records})."
            )

        return BenchmarkEvaluationResult(
            scenario_id=metadata.scenario_id,
            service=result.service,
            expected_root_cause=metadata.expected_root_cause,
            expected_category=metadata.expected_category,
            predicted_category=pred_cat,
            category_match=category_match,
            expected_causal_record=metadata.expected_causal_record,
            causal_record_cited=causal_record_cited,
            cited_records=cited_records,
            leading_hypothesis_statement=leading.get("statement", ""),
            leading_confidence_label=leading.get("confidence_label", "UNKNOWN"),
            leading_score=float(leading.get("evidence_score", 0.0)),
            overall_success=overall_success,
            details=" ".join(details_parts),
        )

    @classmethod
    async def run_suite(
        cls,
        scenarios: list[str] | None = None,
        mode: str = "offline",
        provider: str = "auto",
        llm_model: str | None = None,
    ) -> list[BenchmarkEvaluationResult]:
        """Run investigations across a suite of scenarios and evaluate all outcomes."""
        from recoverit.runner import InvestigationRunner

        suite_scenarios = scenarios or list(CANONICAL_SCENARIOS)
        runner = InvestigationRunner()
        results: list[BenchmarkEvaluationResult] = []

        for scenario in suite_scenarios:
            inv_res = await runner.run_scenario(
                scenario_name=scenario,
                mode=mode,
                provider=provider,
                llm_model=llm_model,
            )
            eval_res = cls.evaluate(inv_res, scenario_name=scenario)
            results.append(eval_res)

        return results
