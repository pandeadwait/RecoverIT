"""
LLM Reasoning Provider adapter implementation.

Implements the ReasoningProvider protocol for live LLM APIs.
Features:
- Provider-neutral client protocol supporting OpenAI, Gemini, Anthropic, or mock clients
- Structured output enforcement via JSON schemas and Pydantic validation
- Prompt versioning (tracked in metadata)
- Token and cost tracking mapped to InvestigationBudget units
- Retry with bounded exponential backoff on transient errors
- Schema-repair: one attempt to fix invalid structured output, then fail safely
- Execution records (provider, model, prompt_version, latency, tokens, cost)
- Strict credential privacy: API keys and secrets never logged or attached to records
- Isolation: provider-specific objects never escape the adapter boundary

See WORK_DIVISION.md §8.7, ARCHITECTURE.md §8, and implementation_plan.md Phase 9.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ValidationError

from contracts.collection.schemas import SourceCapabilityCatalog
from contracts.common import (
    ConfidenceLabel,
    HypothesisStatus,
    InformationPriority,
    InformationValueLevel,
    InvestigationState,
    Reliability,
    RootCauseCategory,
    Severity,
    SourceCoverageStatus,
    SourceType,
    StopReason,
)
from contracts.errors.schemas import (
    REASONING_PROVIDER_ERROR,
    SCHEMA_VALIDATION_FAILED,
    StructuredError,
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
from reasoning.provider.interface import ReasoningProvider

logger = logging.getLogger(__name__)

# Prompt versions
PROMPT_VERSION_ASSESS = "assess_missing_info:v1.0"
PROMPT_VERSION_PLAN = "plan_queries:v1.0"
PROMPT_VERSION_GENERATE = "generate_hypotheses:v1.0"
PROMPT_VERSION_REVISE = "revise_hypotheses:v1.0"
PROMPT_VERSION_SCHEMA_REPAIR = "schema_repair:v1.0"

# Schema version
SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Execution Metadata & Call Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMCallRecord:
    """Audit record of an LLM call without leaking credentials or proprietary formats."""

    provider: str
    model: str
    prompt_version: str
    schema_version: str
    method: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    latency_seconds: float
    retry_count: int
    schema_repaired: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class LLMProviderError(Exception):
    """Raised when an LLM call fails unrecoverably."""

    def __init__(self, error: StructuredError) -> None:
        super().__init__(error.message)
        self.error = error


# ---------------------------------------------------------------------------
# LLM Client Protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMResponse:
    """Raw response returned by an LLM client adapter."""

    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    raw_metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMClient(Protocol):
    """Protocol for underlying raw LLM clients (OpenAI, Gemini, Anthropic, or mock)."""

    async def complete(
        self,
        prompt: str,
        system_instruction: str | None = None,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Execute a completion and return content string with token usage."""
        ...


# ---------------------------------------------------------------------------
# Pricing Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelPricing:
    """Pricing per 1,000,000 tokens in USD."""

    input_cost_per_million: float = 0.15
    output_cost_per_million: float = 0.60

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            (input_tokens / 1_000_000.0) * self.input_cost_per_million
            + (output_tokens / 1_000_000.0) * self.output_cost_per_million
        )


# ---------------------------------------------------------------------------
# LLM Reasoning Provider
# ---------------------------------------------------------------------------

T = TypeVar("T", bound=BaseModel)


class LLMReasoningProvider:
    """
    ReasoningProvider implementation that connects to an LLM via LLMClient.

    Guarantees:
    - Fulfills the ReasoningProvider protocol.
    - Uses JSON schema and Pydantic parsing for guaranteed contract conformity.
    - Prompt versioning recorded in every call record.
    - Token tracking (input_tokens, output_tokens) mapped to InvestigationBudget units.
    - Retry with bounded exponential backoff on transient errors.
    - Exactly one schema-repair attempt on malformed model responses before failing safely.
    - Call records contain latency, token count, and estimated cost.
    - Credentials are never stored or logged in records or evidence.
    - Raw provider-specific objects never escape this class.
    """

    def __init__(
        self,
        client: LLMClient,
        provider_name: str = "openai",
        model: str = "gpt-4o-mini",
        pricing: ModelPricing | None = None,
        max_retries: int = 2,
        initial_backoff_seconds: float = 0.05,
        backoff_multiplier: float = 2.0,
        api_key: str | None = None,
    ) -> None:
        self._client = client
        self._provider_name = provider_name
        self._model = model
        self._pricing = pricing or ModelPricing()
        self._max_retries = max_retries
        self._initial_backoff = initial_backoff_seconds
        self._backoff_multiplier = backoff_multiplier
        # Store api_key securely in a private variable; never log or serialize it
        self._api_key = api_key

        self._call_records: list[LLMCallRecord] = []
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._total_cost_usd: float = 0.0

    # -----------------------------------------------------------------------
    # Inspection & Token Accounting
    # -----------------------------------------------------------------------

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model(self) -> str:
        return self._model

    @property
    def call_records(self) -> list[LLMCallRecord]:
        return list(self._call_records)

    @property
    def total_input_tokens(self) -> int:
        return self._total_input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self._total_output_tokens

    @property
    def total_cost_usd(self) -> float:
        return self._total_cost_usd

    def get_last_call_record(self) -> LLMCallRecord | None:
        return self._call_records[-1] if self._call_records else None

    # -----------------------------------------------------------------------
    # ReasoningProvider Protocol Implementation
    # -----------------------------------------------------------------------

    async def assess_missing_information(
        self,
        incident: IncidentSeed,
        source_capabilities: SourceCapabilityCatalog,
        context: IncidentContextSnapshot,
        active_hypotheses: list[Hypothesis] | None = None,
    ) -> MissingInformationAssessment:
        """
        Assess current investigative progress and identify remaining gaps.
        """
        prompt = self._build_assess_prompt(
            incident=incident,
            source_capabilities=source_capabilities,
            context=context,
            active_hypotheses=active_hypotheses or [],
        )

        system_instruction = (
            "You are an expert SRE incident investigator. Analyze the incident, "
            "established facts in the context snapshot, and available source capabilities. "
            "Identify what information is known and what critical questions must still be answered. "
            "For each item in missing_information, populate candidate_sources with relevant available "
            "source types (e.g. 'changes', 'logs'). Return valid JSON matching the MissingInformationAssessment schema."
        )

        assessment: MissingInformationAssessment = await self._execute_structured_call(
            method="assess_missing_information",
            prompt_version=PROMPT_VERSION_ASSESS,
            prompt=prompt,
            system_instruction=system_instruction,
            target_model=MissingInformationAssessment,
        )

        # Ensure candidate_sources are populated from available sources
        available_sources = [
            s.source_type for s in source_capabilities.sources if s.available
        ]
        updated_missing = []
        for item in assessment.missing_information:
            if not item.candidate_sources:
                q_lower = (item.question + " " + item.reason).lower()
                inferred = []
                if any(w in q_lower for w in ["log", "error", "exception", "traceback", "500", "status", "fail"]):
                    if SourceType.LOGS in available_sources:
                        inferred.append(SourceType.LOGS)
                if any(w in q_lower for w in ["change", "commit", "diff", "code", "author", "repo", "git", "recent"]):
                    if SourceType.CHANGES in available_sources:
                        inferred.append(SourceType.CHANGES)
                if any(w in q_lower for w in ["metric", "rate", "spike", "cpu", "memory", "latency"]):
                    if SourceType.METRICS in available_sources:
                        inferred.append(SourceType.METRICS)
                if any(w in q_lower for w in ["deploy", "release", "version"]):
                    if SourceType.DEPLOYMENTS in available_sources:
                        inferred.append(SourceType.DEPLOYMENTS)
                if any(w in q_lower for w in ["config", "database.yaml", "setting", "env"]):
                    if SourceType.CONFIGURATION in available_sources:
                        inferred.append(SourceType.CONFIGURATION)
                    elif SourceType.CHANGES in available_sources and SourceType.CHANGES not in inferred:
                        inferred.append(SourceType.CHANGES)

                final_sources = inferred if inferred else list(available_sources)
                updated_missing.append(item.model_copy(update={"candidate_sources": final_sources}))
            else:
                updated_missing.append(item)

        return assessment.model_copy(update={"missing_information": updated_missing})

    async def plan_queries(
        self,
        missing_information: MissingInformationAssessment,
        source_capabilities: SourceCapabilityCatalog,
        context: IncidentContextSnapshot,
        budget: InvestigationBudget,
    ) -> EvidenceQueryPlan:
        """
        Plan targeted evidence queries within source capabilities and budget limits.
        """
        prompt = self._build_plan_prompt(
            missing_information=missing_information,
            source_capabilities=source_capabilities,
            context=context,
            budget=budget,
        )

        system_instruction = (
            "You are an expert SRE incident investigator. Generate an EvidenceQueryPlan "
            "to answer missing information questions. Ensure every query uses only supported "
            "fields from available sources in the catalog. Center every time range on the "
            "incident detected_at timestamp, keep it within maximum_window_seconds, and use "
            "the incident's exact service name. Return valid JSON matching the "
            "EvidenceQueryPlan schema."
        )

        plan: EvidenceQueryPlan = await self._execute_structured_call(
            method="plan_queries",
            prompt_version=PROMPT_VERSION_PLAN,
            prompt=prompt,
            system_instruction=system_instruction,
            target_model=EvidenceQueryPlan,
        )

        # Normalize and filter parameters against capabilities to guarantee contract compliance
        catalog_map = {s.source_type: s for s in source_capabilities.sources}
        normalized_queries: list[EvidenceQueryPlanQuery] = []
        for q in plan.queries:
            cap = catalog_map.get(q.source_type)
            if cap is not None:
                supported = set(cap.supported_query_fields)
                synonyms = {
                    "service_name": "service",
                    "max_results": "limit",
                    "max_items": "limit",
                    "start_date": "start_time",
                    "end_date": "end_time",
                }
                new_params: dict[str, Any] = {}
                for k, v in q.parameters.items():
                    norm_k = synonyms.get(k, k)
                    if norm_k in supported:
                        new_params[norm_k] = v

                # LLM output is untrusted even when it satisfies the JSON schema.
                # Enforce semantic source constraints deterministically.
                if "service" in supported:
                    new_params["service"] = context.incident.service

                for count_field in ("limit", "max_commits", "max_items"):
                    if count_field in new_params:
                        try:
                            new_params[count_field] = max(
                                1, min(int(new_params[count_field]), cap.maximum_items)
                            )
                        except (TypeError, ValueError):
                            new_params.pop(count_field, None)

                time_pairs = [
                    ("start_time", "end_time"),
                    ("since", "until"),
                ]
                supported_pairs = [
                    pair for pair in time_pairs if pair[0] in supported and pair[1] in supported
                ]
                if supported_pairs and cap.maximum_window_seconds > 0:
                    selected_pair = next(
                        (
                            pair
                            for pair in supported_pairs
                            if pair[0] in new_params or pair[1] in new_params
                        ),
                        supported_pairs[0],
                    )
                    for start_key, end_key in time_pairs:
                        new_params.pop(start_key, None)
                        new_params.pop(end_key, None)
                    anchor = context.incident.detected_at.astimezone(timezone.utc)
                    start = anchor - timedelta(seconds=cap.maximum_window_seconds)
                    new_params[selected_pair[0]] = start.isoformat().replace("+00:00", "Z")
                    new_params[selected_pair[1]] = anchor.isoformat().replace("+00:00", "Z")
                normalized_queries.append(q.model_copy(update={"parameters": new_params}))
            else:
                normalized_queries.append(q)

        return plan.model_copy(update={"queries": normalized_queries})

    async def generate_hypotheses(
        self,
        incident: IncidentSeed,
        context: IncidentContextSnapshot,
        limits: InvestigationBudget,
    ) -> HypothesisSet:
        """
        Generate multiple plausible root-cause hypotheses with evidence citations.
        """
        prompt = self._build_generate_prompt(
            incident=incident,
            context=context,
            limits=limits,
        )

        system_instruction = (
            "You are an expert SRE incident investigator. Formulate plausible root-cause "
            "hypotheses for the incident. Every hypothesis must cite evidence IDs from the context, "
            "provide testable predictions, and consider alternative non-change explanations. "
            "Return valid JSON matching the HypothesisSet schema."
        )

        return await self._execute_structured_call(
            method="generate_hypotheses",
            prompt_version=PROMPT_VERSION_GENERATE,
            prompt=prompt,
            system_instruction=system_instruction,
            target_model=HypothesisSet,
        )

    async def revise_hypotheses(
        self,
        previous_hypotheses: HypothesisSet,
        new_context: IncidentContextSnapshot,
    ) -> HypothesisSet:
        """
        Update, strengthen, weaken, or reject hypotheses based on new evidence.
        """
        prompt = self._build_revise_prompt(
            previous_hypotheses=previous_hypotheses,
            new_context=new_context,
        )

        system_instruction = (
            "You are an expert SRE incident investigator. Update the hypotheses in light of new "
            "evidence. Strengthen, weaken, or reject hypotheses. Do not drop rejected hypotheses; "
            "keep them with status 'rejected'. Increment revision numbers. "
            "Return valid JSON matching the HypothesisSet schema."
        )

        return await self._execute_structured_call(
            method="revise_hypotheses",
            prompt_version=PROMPT_VERSION_REVISE,
            prompt=prompt,
            system_instruction=system_instruction,
            target_model=HypothesisSet,
        )

    # -----------------------------------------------------------------------
    # Core Execution Loop: Retries, Backoff, Schema Repair
    # -----------------------------------------------------------------------

    async def _execute_structured_call(
        self,
        method: str,
        prompt_version: str,
        prompt: str,
        system_instruction: str,
        target_model: type[T],
    ) -> T:
        """
        Execute call with transient retries and a single schema repair attempt.
        """
        start_time = time.monotonic()
        retry_count = 0
        backoff = self._initial_backoff
        last_error: Exception | None = None
        response: LLMResponse | None = None

        # 1. Transient Retry Loop
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.complete(
                    prompt=prompt,
                    system_instruction=system_instruction,
                    json_schema=target_model.model_json_schema(),
                    temperature=0.0,
                )
                break
            except Exception as exc:
                last_error = exc
                retry_count = attempt + 1
                if attempt < self._max_retries:
                    logger.warning(
                        "Transient error calling LLM in %s (attempt %d/%d): %r. Backing off for %.3fs",
                        method,
                        attempt + 1,
                        self._max_retries,
                        exc,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff *= self._backoff_multiplier
                else:
                    logger.error("Exhausted retries calling LLM in %s: %r", method, exc)

        if response is None:
            err = StructuredError(
                code=REASONING_PROVIDER_ERROR,
                message=f"LLM call failed after {retry_count} retries: {last_error}",
                retryable=True,
                source=self._provider_name,
                details={"method": method, "model": self._model},
            )
            raise LLMProviderError(err)

        # 2. Schema Validation and Single Schema-Repair Attempt
        schema_repaired = False
        parsed_obj: T | None = None
        validation_err: Exception | None = None

        try:
            parsed_obj = self._parse_and_validate(response.content, target_model)
        except (json.JSONDecodeError, ValidationError) as err:
            validation_err = err
            logger.warning(
                "Initial schema validation failed for %s: %s. Attempting schema repair.",
                method,
                err,
            )

        # 3. Schema Repair Attempt (one attempt)
        if parsed_obj is None:
            repair_prompt = (
                f"The following JSON response failed schema validation for model {target_model.__name__}:\n"
                f"Error: {validation_err}\n\n"
                f"Raw Response:\n{response.content}\n\n"
                f"Please fix the formatting and output ONLY a valid JSON object matching the schema."
            )
            try:
                repair_response = await self._client.complete(
                    prompt=repair_prompt,
                    system_instruction="You are a JSON schema repair assistant. Fix the JSON to strictly conform to the schema.",
                    json_schema=target_model.model_json_schema(),
                    temperature=0.0,
                )
                parsed_obj = self._parse_and_validate(repair_response.content, target_model)
                schema_repaired = True
                # Add tokens from repair call
                response = LLMResponse(
                    content=repair_response.content,
                    input_tokens=response.input_tokens + repair_response.input_tokens,
                    output_tokens=response.output_tokens + repair_response.output_tokens,
                )
            except Exception as repair_exc:
                logger.error("Schema repair attempt failed for %s: %s", method, repair_exc)
                err_struct = StructuredError(
                    code=SCHEMA_VALIDATION_FAILED,
                    message=f"LLM output failed schema validation after repair attempt: {repair_exc}",
                    retryable=False,
                    source=self._provider_name,
                    details={
                        "method": method,
                        "model": self._model,
                        "validation_error": str(validation_err),
                        "repair_error": str(repair_exc),
                    },
                )
                raise LLMProviderError(err_struct)

        # 4. Record Metrics & Return Validated Domain Model
        latency = time.monotonic() - start_time
        cost = self._pricing.calculate_cost(response.input_tokens, response.output_tokens)

        call_record = LLMCallRecord(
            provider=self._provider_name,
            model=self._model,
            prompt_version=prompt_version,
            schema_version=SCHEMA_VERSION,
            method=method,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            estimated_cost_usd=cost,
            latency_seconds=latency,
            retry_count=retry_count,
            schema_repaired=schema_repaired,
        )

        self._call_records.append(call_record)
        self._total_input_tokens += response.input_tokens
        self._total_output_tokens += response.output_tokens
        self._total_cost_usd += cost

        return parsed_obj

    def _parse_and_validate(self, content: str, target_model: type[T]) -> T:
        """Parse raw JSON string into target Pydantic model."""
        clean_content = content.strip()
        # Handle markdown codeblock fence if present
        if clean_content.startswith("```"):
            lines = clean_content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            clean_content = "\n".join(lines).strip()

        data = json.loads(clean_content)
        return target_model.model_validate(data)

    # -----------------------------------------------------------------------
    # Prompt Builders
    # -----------------------------------------------------------------------

    def _build_assess_prompt(
        self,
        incident: IncidentSeed,
        source_capabilities: SourceCapabilityCatalog,
        context: IncidentContextSnapshot,
        active_hypotheses: list[Hypothesis],
    ) -> str:
        payload = {
            "incident": incident.model_dump(mode="json"),
            "source_capabilities": source_capabilities.model_dump(mode="json"),
            "context_summary": {
                "snapshot_id": context.snapshot_id,
                "evidence_count": len(context.evidence),
                "evidence": [e.model_dump(mode="json") for e in context.evidence],
                "source_coverage": {
                    (k.value if hasattr(k, "value") else str(k)): (
                        v.value if hasattr(v, "value") else str(v)
                    )
                    for k, v in context.source_coverage.items()
                },
            },
            "active_hypotheses": [h.model_dump(mode="json") for h in active_hypotheses],
        }
        available_source_types = [
            (s.source_type.value if hasattr(s.source_type, "value") else str(s.source_type))
            for s in source_capabilities.sources
            if s.available
        ]
        return (
            f"Prompt Version: {PROMPT_VERSION_ASSESS}\n"
            f"Available source types in the catalog: {available_source_types}\n"
            f"CRITICAL: In 'missing_information', specify relevant available source types in 'candidate_sources' (e.g. ['changes', 'logs']).\n\n"
            f"Assess missing information for this incident:\n"
            f"{json.dumps(payload, indent=2)}"
        )

    def _build_plan_prompt(
        self,
        missing_information: MissingInformationAssessment,
        source_capabilities: SourceCapabilityCatalog,
        context: IncidentContextSnapshot,
        budget: InvestigationBudget,
    ) -> str:
        payload = {
            "missing_information": missing_information.model_dump(mode="json"),
            "source_capabilities": source_capabilities.model_dump(mode="json"),
            "budget": budget.model_dump(mode="json"),
            "context_snapshot_id": context.snapshot_id,
            "context_created_at": context.created_at.isoformat(),
            "incident": context.incident.model_dump(mode="json"),
        }
        allowed_params = {
            (s.source_type.value if hasattr(s.source_type, "value") else str(s.source_type)): s.supported_query_fields
            for s in source_capabilities.sources
            if s.available
        }
        return (
            f"Prompt Version: {PROMPT_VERSION_PLAN}\n"
            f"Allowed query parameter fields for available sources:\n"
            f"{json.dumps(allowed_params, indent=2)}\n\n"
            "CRITICAL: use the incident's exact service and detected_at timestamp. "
            "Every time range must be no larger than that source's maximum_window_seconds.\n\n"
            f"Plan queries for missing information (only use allowed fields above for each source):\n"
            f"{json.dumps(payload, indent=2)}"
        )

    def _build_generate_prompt(
        self,
        incident: IncidentSeed,
        context: IncidentContextSnapshot,
        limits: InvestigationBudget,
    ) -> str:
        payload = {
            "incident": incident.model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
            "limits": limits.model_dump(mode="json"),
        }
        return (
            f"Prompt Version: {PROMPT_VERSION_GENERATE}\n"
            f"Generate hypotheses with supporting and contradicting evidence:\n"
            f"{json.dumps(payload, indent=2)}"
        )

    def _build_revise_prompt(
        self,
        previous_hypotheses: HypothesisSet,
        new_context: IncidentContextSnapshot,
    ) -> str:
        payload = {
            "previous_hypotheses": previous_hypotheses.model_dump(mode="json"),
            "new_context": new_context.model_dump(mode="json"),
        }
        return (
            f"Prompt Version: {PROMPT_VERSION_REVISE}\n"
            f"Revise existing hypotheses based on new evidence:\n"
            f"{json.dumps(payload, indent=2)}"
        )
