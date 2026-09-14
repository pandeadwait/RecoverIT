"""Unified Investigation Runner orchestrating the complete incident investigation flow."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import logging
from pathlib import Path
import time
from typing import Any, Callable
import uuid

from collectors.changes.git_adapter import LocalGitChangeAdapter
from collectors.fixtures import (
    canonical_scenario_id,
    create_scenario_adapters,
    list_available_scenarios,
    load_scenario_json,
)
from collectors.gateway.collection_service import DefaultCollectionService
from collectors.logs.file_adapter import FileLogAdapter
from contracts.collection.schemas import SourceCapabilityCatalog as Person3Catalog
from contracts.enums import SourceType
from contracts.hypothesis.schemas import RankedHypothesis, RankedHypothesisSet
from contracts.incident.schemas import IncidentSeed as Person3IncidentSeed
from contracts.incident.seed import IncidentSeed as Person1IncidentSeed
from contracts.investigation.schemas import InvestigationBudget
from ingestion.capabilities.registry import DefaultSourceRegistry
from integration_runtime import Person1CollectionAdapter, Person2ContextAdapter
from investigation.budgets.budget_tracker import BudgetTracker
from investigation.orchestration.orchestrator import (
    InvestigationOrchestrator,
    StoppingRuleEvaluator,
)
from reasoning.provider.clients import create_llm_client
from reasoning.provider.fake_provider import FakeReasoningProvider
from reasoning.provider.llm_provider import LLMReasoningProvider

logger = logging.getLogger(__name__)

SCENARIO_PRESET_MAP: dict[str, str] = {
    "incident_001": "deployment-regression",
    "incident_002": "resource-exhaustion",
    "incident_003": "dependency-incompatibility",
    "incident_004": "database-outage",
    "incident_005": "coincidental-deployment",
    "bad_db_config": "deployment-regression",
    "memory_exhaustion": "resource-exhaustion",
    "dependency_incompatibility": "dependency-incompatibility",
    "real_db_outage": "database-outage",
    "coincidental_deployment": "coincidental-deployment",
}


@dataclass
class InvestigationResult:
    """Consolidated outcome of an investigation run for presentation and reporting."""

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
        """Generate a complete SRE Post-Mortem Incident Triage Report in Markdown."""
        lines = [
            f"# Incident Triage Report: {self.incident_id}",
            "",
            f"**Target Service:** `{self.service}`  ",
            f"**Incident Summary:** {self.summary}  ",
            f"**Investigation Status:** `{self.status.upper()}`  ",
            f"**Reasoning Provider:** `{self.provider_used}`  ",
            f"**Analysis Latency:** {self.execution_time_seconds:.2f}s  ",
            "",
            "---",
            "",
            "## Ranked Root Cause Hypotheses",
            "",
        ]

        if not self.ranked_hypotheses:
            lines.append("> Warning: No definitive root-cause hypotheses met required evidence confidence.")
        else:
            for h in self.ranked_hypotheses:
                rank = h["rank"]
                score = h["evidence_score"]
                label = h["confidence_label"].upper()
                category = h["root_cause_category"].replace("_", " ").title()
                statement = h["statement"]
                component = h["affected_component"]

                lines.extend([
                    f"### #{rank} [{label}] {statement}",
                    f"- **Category:** {category}",
                    f"- **Component:** `{component}`",
                    f"- **Evidence Score:** {score:.1f} / 100",
                ])

                bd = h.get("score_breakdown", {})
                if bd:
                    symptom_conf = bd.get("symptom_score", 0.0)
                    causal_conf = bd.get("causal_score", 0.0)
                    lines.extend([
                        f"- **Symptom Confidence:** {symptom_conf:.1f}%",
                        f"- **Causal Confidence:** {causal_conf:.1f}%",
                    ])
                    if bd.get("capped_reason"):
                        lines.append(f"> ⚠ **Confidence Capped:** {bd['capped_reason']}")

                    lines.extend([
                        "",
                        "**Confidence Breakdown:**",
                        "",
                        "| Scoring Factor | Type | Points |",
                        "|---|---|---|",
                        f"| Independent sources | Positive | +{bd.get('independent_source_support', 0.0):.2f} |",
                        f"| Symptom coverage | Positive | +{bd.get('symptom_coverage', 0.0):.2f} |",
                        f"| Temporal consistency | Positive | +{bd.get('temporal_consistency', 0.0):.2f} |",
                        f"| Direct change evidence | Positive | +{bd.get('change_consistency', 0.0):.2f} |",
                        f"| Specificity | Positive | +{bd.get('specificity', 0.0):.2f} |",
                        f"| Prediction support | Positive | +{bd.get('prediction_support', 0.0):.2f} |",
                        f"| Contradictions | Penalty | -{bd.get('contradiction_penalty', 0.0):.2f} |",
                        f"| Missing causal evidence | Penalty | -{bd.get('missing_evidence_penalty', 0.0):.2f} |",
                        f"| **Final Evidence Score** | **Total** | **{score:.2f} / 100** |",
                        "",
                    ])

                supporting = h.get("supporting_evidence", [])
                if supporting:
                    lines.append("- **Supporting Evidence Citations:**")
                    for c in supporting:
                        lines.append(f"  - `{c['evidence_id']}`: {c.get('reason', 'Corroborating factor')}")

                contradicting = h.get("contradicting_evidence", [])
                if contradicting:
                    lines.append("- **Contradicting Evidence:**")
                    for c in contradicting:
                        lines.append(f"  - `{c['evidence_id']}`: {c.get('reason', 'Contradicting factor')}")

                lines.append("")

        lines.extend([
            "---",
            "",
            "## Incident Chronology & Reconstructed Timeline",
            "",
            "| Time (UTC) | Category | Event / Observation | Service |",
            "|---|---|---|---|",
        ])

        for ev in self.timeline_events:
            ts = ev.get("event_time") or "Unknown"
            cat = ev.get("category", "General").title()
            title = ev.get("title", "")
            svc = ev.get("service", "")
            lines.append(f"| {ts} | `{cat}` | {title} | `{svc}` |")

        lines.append("")

        if self.diff_excerpts:
            lines.extend([
                "---",
                "",
                "## Codebase Changes & Diff Excerpts",
                "",
            ])
            for commit_id, diff in self.diff_excerpts.items():
                lines.extend([
                    f"#### Commit: `{commit_id}`",
                    "```diff",
                    diff.strip(),
                    "```",
                    "",
                ])

        if self.completion_criteria:
            lines.extend([
                "",
                "---",
                "",
                "## Evidentiary Completion Status",
                "",
            ])
            for crit, ok in self.completion_criteria.items():
                mark = "[x]" if ok else "[ ]"
                name = crit.replace("_", " ").title()
                lines.append(f"- {mark} {name}")
            if self.unresolved_criteria:
                lines.append("")
                lines.append("**Unresolved Requirements:**")
                for item in self.unresolved_criteria:
                    lines.append(f"- {item}")

        lines.extend([
            "",
            "---",
            "",
            "## Investigation Audit & Resource Usage",
            "",
            f"- **Rounds Executed:** {self.budget_usage.get('rounds_completed', 1)}",
            f"- **Queries Planned & Executed:** {self.budget_usage.get('queries_executed', 0)}",
            f"- **Reasoning Calls:** {self.budget_usage.get('reasoning_calls_completed', 0)}",
            "",
            "*(Report automatically generated by RecoverIT Autonomous CI/CD Triager)*",
        ])

        return "\n".join(lines)


class InvestigationRunner:
    """Coordinates and executes an end-to-end investigation run."""

    async def run_scenario(
        self,
        scenario_name: str,
        mode: str = "auto",
        provider: str = "auto",
        llm_model: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> InvestigationResult:
        """Run an investigation against one of the pre-packaged scenario families."""
        scenarios = list_available_scenarios()
        if scenario_name not in scenarios:
            raise ValueError(f"Unknown scenario '{scenario_name}'. Available: {scenarios}")

        start_time = time.monotonic()
        data = load_scenario_json(scenario_name)
        canonical_id = canonical_scenario_id(scenario_name)

        seed = Person3IncidentSeed(
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

        registry = DefaultSourceRegistry()
        for source in create_scenario_adapters(scenario_name).values():
            registry.register_source(source)

        return await self._execute_investigation(
            seed=seed,
            registry=registry,
            scenario_name=scenario_name,
            mode=mode,
            provider=provider,
            llm_model=llm_model,
            start_time=start_time,
            progress_callback=progress_callback,
        )

    async def run_target(
        self,
        repo_path: str | Path,
        log_path: str | Path | None = None,
        service_name: str = "target-service",
        summary: str = "Operational degradation detected",
        mode: str = "auto",
        provider: str = "auto",
        llm_model: str | None = None,
        baseline_scenario: str = "bad_db_config",
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> InvestigationResult:
        """Run an investigation against a real local Git repository and log file on disk."""
        start_time = time.monotonic()
        incident_id = f"inc-{uuid.uuid4().hex[:8]}"

        seed = Person3IncidentSeed(
            incident_id=incident_id,
            external_alert_id=f"alert-{uuid.uuid4().hex[:6]}",
            service=service_name,
            environment="local",
            severity="critical",
            detected_at=datetime.now(timezone.utc),
            received_at=datetime.now(timezone.utc),
            summary=summary,
            labels={"mode": "target-inspection"},
        )

        registry = DefaultSourceRegistry()
        # Initialize baseline adapters for sources not provided on disk
        for source in create_scenario_adapters(baseline_scenario).values():
            registry.register_source(source)

        # Overlay real live Git change adapter
        git_adapter = LocalGitChangeAdapter(repo_path=repo_path, service_name=service_name)
        registry.register_source(git_adapter)

        # Overlay real live log adapter if log file provided
        if log_path:
            log_adapter = FileLogAdapter(log_path=log_path, service_name=service_name)
            registry.register_source(log_adapter)

        return await self._execute_investigation(
            seed=seed,
            registry=registry,
            scenario_name=baseline_scenario,
            mode=mode,
            provider=provider,
            llm_model=llm_model,
            start_time=start_time,
            progress_callback=progress_callback,
        )

    async def _execute_investigation(
        self,
        seed: Person3IncidentSeed,
        registry: DefaultSourceRegistry,
        scenario_name: str | None,
        mode: str,
        provider: str,
        llm_model: str | None,
        start_time: float,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> InvestigationResult:
        # 1. Discover capabilities
        p1_seed = Person1IncidentSeed.model_validate(seed.model_dump())
        p1_catalog = registry.capabilities(p1_seed)
        p3_catalog = Person3Catalog.model_validate(p1_catalog.model_dump())

        # 2. Setup boundary adapters
        col_svc = Person1CollectionAdapter(DefaultCollectionService(registry=registry))
        ctx_bld = Person2ContextAdapter(seed)

        # 3. Setup reasoning provider
        provider_name = "deterministic-preset"
        llm_client = None

        if mode in {"auto", "live"} and provider != "offline":
            llm_client = create_llm_client(provider_name=provider, model=llm_model)

        if llm_client is not None:
            if provider != "auto":
                effective_provider = provider
            elif llm_client.__class__.__name__ == "GeminiClient":
                effective_provider = "gemini"
            elif "11434" in getattr(llm_client, "base_url", ""):
                effective_provider = "ollama"
            else:
                effective_provider = "openai"
            provider_inst = LLMReasoningProvider(
                client=llm_client,
                provider_name=effective_provider,
                model=getattr(llm_client, "model", llm_model or "unknown"),
                max_retries=1 if effective_provider == "ollama" else 2,
            )
            provider_name = f"Live LLM ({llm_client.model})"
        else:
            preset = SCENARIO_PRESET_MAP.get(scenario_name or "", "deployment-regression")
            provider_inst = FakeReasoningProvider(preset=preset)
            provider_name = f"Deterministic Preset ({preset})"

        # 4. Orchestrate
        orchestrator = InvestigationOrchestrator(
            provider=provider_inst,
            collection_service=col_svc,
            context_builder=ctx_bld,
            stopping_evaluator=StoppingRuleEvaluator(),
            progress_callback=progress_callback,
            provider_name=provider_name,
        )

        effective_budget = InvestigationBudget(
            max_rounds=2,
            max_queries=8,
            max_reasoning_calls=8,
        )

        ranked_set: RankedHypothesisSet = await orchestrator.run(
            incident=seed,
            source_capabilities=p3_catalog,
            budget=effective_budget,
        )

        elapsed = time.monotonic() - start_time
        context = orchestrator.current_context

        # 5. Extract timeline events & diffs
        timeline_events = []
        if context and context.timeline:
            for item in context.timeline:
                timeline_events.append({
                    "event_id": item.timeline_event_id,
                    "event_time": item.event_time.isoformat() if item.event_time else None,
                    "category": str(item.category),
                    "title": item.title,
                    "service": item.service,
                    "evidence_ids": list(item.evidence_ids),
                })

        evidence_items = []
        if context and context.evidence:
            for ev in context.evidence:
                src_rec_id = None
                if hasattr(ev, "provenance") and ev.provenance:
                    src_rec_id = getattr(ev.provenance, "source_record_id", None)
                elif hasattr(ctx_bld, "coordinator") and hasattr(ctx_bld.coordinator, "evidence"):
                    try:
                        full_rec = ctx_bld.coordinator.evidence.get(ev.evidence_id)
                        if full_rec and hasattr(full_rec, "provenance") and full_rec.provenance:
                            src_rec_id = getattr(full_rec.provenance, "source_record_id", None)
                    except Exception:
                        pass

                evidence_items.append({
                    "evidence_id": ev.evidence_id,
                    "source_type": str(ev.source_type),
                    "evidence_type": str(ev.evidence_type),
                    "event_time": ev.event_time.isoformat() if ev.event_time else None,
                    "summary": ev.summary,
                    "source_record_id": src_rec_id,
                })

        # Extract diffs from raw records
        diff_excerpts = {}
        log_excerpts = []
        for batch in orchestrator.history_batches:
            for result in batch.results:
                st = str(result.source_type).lower()
                for rec in result.records:
                    if "diff_excerpt" in rec.payload and rec.payload["diff_excerpt"]:
                        cid = str(rec.payload.get("commit_sha") or rec.source_record_id)
                        diff_excerpts[cid] = rec.payload["diff_excerpt"]
                    if st in {"logs", "sourcetype.logs"}:
                        log_excerpts.append({
                            "id": rec.source_record_id,
                            "time": rec.event_time.isoformat() if rec.event_time else None,
                            "message": rec.payload.get("message"),
                            "level": rec.payload.get("level"),
                        })

        # Form ranked hypotheses list
        ranked_list = []
        for h in ranked_set.hypotheses:
            ranked_list.append({
                "rank": h.rank,
                "hypothesis_id": h.hypothesis_id,
                "statement": h.statement,
                "root_cause_category": str(h.root_cause_category),
                "affected_component": h.affected_component,
                "evidence_score": h.evidence_score,
                "confidence_label": str(h.confidence_label),
                "supporting_evidence": [
                    {"evidence_id": c.evidence_id, "reason": c.reason}
                    for c in h.supporting_evidence
                ],
                "contradicting_evidence": [
                    {"evidence_id": c.evidence_id, "reason": c.reason}
                    for c in h.contradicting_evidence
                ],
                "score_breakdown": h.score_breakdown.model_dump() if h.score_breakdown else {},
            })

        budget_usage = {}
        if orchestrator.budget_tracker:
            budget_usage = orchestrator.budget_tracker.get_budget_usage().model_dump(mode="json")

        criteria_status = {}
        unresolved_criteria = []
        if orchestrator.last_stopping_decision:
            criteria_status = orchestrator.last_stopping_decision.criteria_status
            unresolved_criteria = orchestrator.last_stopping_decision.unresolved_criteria

        return InvestigationResult(
            incident_id=seed.incident_id,
            service=seed.service,
            summary=seed.summary,
            status=str(ranked_set.status),
            stop_reason=str(ranked_set.stop_reason) if ranked_set.stop_reason else None,
            execution_time_seconds=elapsed,
            ranked_hypotheses=ranked_list,
            timeline_events=timeline_events,
            evidence_items=evidence_items,
            diff_excerpts=diff_excerpts,
            log_excerpts=log_excerpts,
            budget_usage=budget_usage,
            provider_used=provider_name,
            completion_criteria=criteria_status,
            unresolved_criteria=unresolved_criteria,
            scenario_name=scenario_name,
        )
