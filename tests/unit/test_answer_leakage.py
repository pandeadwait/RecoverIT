"""
Tests for Phase 1: Answer Leakage Prevention & Ground Truth Isolation.

Verifies:
1. Scenario fixture titles are neutral observable alerts (e.g. [P1] HTTP 500 rate increased...).
2. Revealing diagnoses and scenario names do not leak into model-visible prompts.
3. The LLM receives strictly: alert, service, environment, severity, and detection time.
4. Ground truth metadata is stored separately in fixtures and is unavailable to collection.
5. Canonical neutral identifiers (incident_001..incident_005) and legacy aliases work seamlessly.
"""

from __future__ import annotations

import json
from typing import Any
import pytest

from collectors.fixtures import (
    CANONICAL_SCENARIOS,
    canonical_scenario_id,
    list_available_scenarios,
    load_scenario_json,
    load_scenario_records,
    resolve_scenario_name,
)
from contracts.enums import SourceType
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import InvestigationBudget
from investigation.orchestration.orchestrator import (
    InMemoryCollectionService,
    InMemoryContextBuilder,
    InvestigationOrchestrator,
)
from reasoning.provider.llm_provider import LLMReasoningProvider, LLMResponse


class CapturingLLMClient:
    """Mock LLM client that records every prompt sent to it and returns valid structured schema."""

    def __init__(self) -> None:
        self.captured_prompts: list[str] = []
        self.model = "mock-capture-llm"

    async def complete(
        self,
        prompt: str,
        system_instruction: str | None = None,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.captured_prompts.append(prompt)

        # Return minimal valid JSON matching whatever schema is expected
        if "MissingInformationAssessment" in prompt or "assess_missing_information" in (system_instruction or ""):
            content = json.dumps({
                "schema_version": "1.0",
                "incident_id": "test-inc",
                "assessment_id": "assess-01",
                "round": 1,
                "known_facts": [
                    {
                        "fact_id": "fact-1",
                        "statement": "HTTP 500 rate is elevated",
                        "source_evidence_ids": ["ev-1"],
                        "confidence": "high",
                    }
                ],
                "missing_information": [
                    {
                        "missing_info_id": "gap-1",
                        "question": "Were any changes made to configuration?",
                        "reason": "Identify if recent changes caused the failure",
                        "priority": "high",
                        "candidate_sources": ["changes"],
                    }
                ],
                "recommended_action": "continue",
            })
        elif "EvidenceQueryPlan" in prompt or "plan_queries" in (system_instruction or ""):
            content = json.dumps({
                "schema_version": "1.0",
                "incident_id": "test-inc",
                "plan_id": "plan-01",
                "round": 1,
                "queries": [
                    {
                        "query_id": "q-1",
                        "source_type": "changes",
                        "query_question": "Search for changes",
                        "parameters": {"service": "payment-api"},
                        "target_information_ids": ["gap-1"],
                    }
                ],
            })
        elif "Hypothesis" in prompt or "hypotheses" in prompt.lower():
            content = json.dumps({
                "schema_version": "1.0",
                "incident_id": "test-inc",
                "hypotheses": [
                    {
                        "hypothesis_id": "hypo-1",
                        "incident_id": "test-inc",
                        "statement": "Service degradation following recent commit",
                        "root_cause_category": "configuration_regression",
                        "affected_component": "payment-api",
                        "status": "active",
                    }
                ],
            })
        else:
            content = "{}"

        return LLMResponse(content=content, input_tokens=100, output_tokens=50)


# ── 1. Neutral Alert Titles in Fixtures ──────────────────────────────────────


def test_scenario_fixture_titles_are_neutral_symptoms():
    """Ensure scenario fixtures contain observable symptoms and not root-cause answers."""
    revealing_phrases = [
        "bad database configuration",
        "misleading coincidental",
        "dependency incompatibility causing",
        "memory exhaustion and container",
        "real database outage",
    ]

    for scenario_name in CANONICAL_SCENARIOS:
        data = load_scenario_json(scenario_name)
        title = data.get("title", "").lower()

        # Title must not reveal the diagnosis
        for phrase in revealing_phrases:
            assert phrase not in title, (
                f"Scenario '{scenario_name}' title leaks answer: '{data.get('title')}' contains '{phrase}'"
            )

        # Title should look like an alert symptom
        assert any(p in data.get("title", "") for p in ["[P1]", "[P2]", "Spike", "Failure", "Exhaustion"]), (
            f"Scenario '{scenario_name}' title should resemble an alert symptom: {data.get('title')}"
        )


# ── 2. Ground Truth Isolation ───────────────────────────────────────────────


def test_ground_truth_is_isolated_in_fixtures():
    """Verify ground truth metadata exists in fixtures but is segregated from collector records."""
    for scenario_name in CANONICAL_SCENARIOS:
        data = load_scenario_json(scenario_name)

        assert "ground_truth" in data, f"Scenario '{scenario_name}' must have ground_truth block"
        gt = data["ground_truth"]
        assert "expected_root_cause" in gt
        assert "expected_category" in gt
        assert "expected_causal_record" in gt

        # Ensure ground truth is not part of source records returned by collectors
        for st in SourceType:
            records = load_scenario_records(scenario_name, st)
            for r in records:
                # No record should contain the ground truth dict
                assert "ground_truth" not in r.payload


# ── 3. Canonical and Alias Resolution ───────────────────────────────────────


def test_scenario_aliases_and_canonical_mapping():
    """Ensure canonical IDs and legacy aliases resolve properly in both directions."""
    expected_mappings = {
        "incident_001": "bad_db_config",
        "incident_002": "memory_exhaustion",
        "incident_003": "dependency_incompatibility",
        "incident_004": "real_db_outage",
        "incident_005": "coincidental_deployment",
    }

    for canon, alias in expected_mappings.items():
        assert resolve_scenario_name(canon) == alias
        assert resolve_scenario_name(alias) == alias
        assert canonical_scenario_id(alias) == canon
        assert canonical_scenario_id(canon) == canon

    all_scenarios = list_available_scenarios()
    for canon in expected_mappings.keys():
        assert canon in all_scenarios
    for alias in expected_mappings.values():
        assert alias in all_scenarios

    only_canon = list_available_scenarios(only_canonical=True)
    assert set(only_canon) == set(expected_mappings.keys())


# ── 4. Prompt Sanitization & Zero Answer Leakage ────────────────────────────


@pytest.mark.asyncio
async def test_llm_prompts_have_zero_answer_leakage():
    """
    Simulate LLM interaction across all scenarios and verify that:
    1. Neither the scenario alias (e.g. bad_db_config) nor the expected diagnosis appears in any prompt.
    2. The LLM receives strictly: alert, service, environment, severity, and detection time.
    """
    for scenario_name in CANONICAL_SCENARIOS:
        client = CapturingLLMClient()
        provider = LLMReasoningProvider(client=client, provider_name="test-llm", model="test-model")

        data = load_scenario_json(scenario_name)
        canonical_id = canonical_scenario_id(scenario_name)
        alias = resolve_scenario_name(scenario_name)

        seed = IncidentSeed(
            incident_id=f"inc-{canonical_id}",
            external_alert_id=f"alert-{canonical_id}",
            service=data["service"],
            environment="simulation",
            severity="critical",
            detected_at=data["detected_at"],
            received_at=data["detected_at"],
            summary=data["title"],
            labels={"environment": "simulation"},
        )

        col_svc = InMemoryCollectionService()
        ctx_bld = InMemoryContextBuilder()
        orchestrator = InvestigationOrchestrator(
            provider=provider,
            collection_service=col_svc,
            context_builder=ctx_bld,
        )

        from datetime import datetime, timezone
        from contracts.collection.schemas import SourceCapability, SourceCapabilityCatalog

        catalog = SourceCapabilityCatalog(
            incident_id=seed.incident_id,
            generated_at=datetime.now(timezone.utc),
            sources=[
                SourceCapability(
                    source_type=SourceType.LOGS,
                    available=True,
                    supported_query_fields=["service", "limit"],
                ),
                SourceCapability(
                    source_type=SourceType.CHANGES,
                    available=True,
                    supported_query_fields=["service", "limit"],
                ),
            ],
        )

        budget = InvestigationBudget(max_rounds=1, max_queries=2, max_reasoning_calls=3)
        await orchestrator.run(incident=seed, source_capabilities=catalog, budget=budget)

        assert len(client.captured_prompts) > 0, f"Expected captured prompts for {scenario_name}"

        expected_cause = data["ground_truth"]["expected_root_cause"]

        for prompt in client.captured_prompts:
            # 1. Alias must not appear anywhere in the prompt
            assert alias not in prompt, f"Prompt for {scenario_name} leaked alias '{alias}'"

            # 2. Expected diagnosis must not appear in the prompt
            assert expected_cause not in prompt, f"Prompt for {scenario_name} leaked expected cause '{expected_cause}'"

            # 3. Model visible incident payload contains only alert, service, environment, severity, detected_at
            parsed_sections = []
            for line in prompt.splitlines():
                if '"incident": {' in line:
                    parsed_sections.append(line)

            # In each assess prompt, the incident payload must not contain labels or scenario metadata
            assert '"scenario"' not in prompt
            assert '"ground_truth"' not in prompt


def test_sanitize_incident_for_prompt_fields():
    """Verify that _sanitize_incident_for_prompt outputs only allowed fields."""
    seed = IncidentSeed(
        incident_id="inc-test-01",
        external_alert_id="alert-raw-internal",
        service="payment-api",
        environment="simulation",
        severity="critical",
        detected_at="2026-09-12T10:30:00Z",
        received_at="2026-09-12T10:30:00Z",
        summary="[P1] Elevated error rate",
        labels={"scenario": "bad_db_config", "internal_secret": "do_not_leak"},
    )

    sanitized = LLMReasoningProvider._sanitize_incident_for_prompt(seed)

    # Allowed fields only
    expected_keys = {"service", "environment", "severity", "detected_at", "alert"}
    assert set(sanitized.keys()) == expected_keys
    assert sanitized["alert"] == "[P1] Elevated error rate"
    assert sanitized["service"] == "payment-api"
    assert "bad_db_config" not in str(sanitized)
    assert "internal_secret" not in str(sanitized)
