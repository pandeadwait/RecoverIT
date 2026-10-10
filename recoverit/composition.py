"""Runtime composition primitives for LangGraph investigations.

This module owns runtime-only objects: source discovery, graph dependencies,
checkpoint persistence, and progress dispatch.  It deliberately does not place
service objects or callbacks in graph state.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field, ValidationError

from contracts.collection.schemas import SourceCapabilityCatalog
from contracts.enums import SourceType
from contracts.errors.schemas import ProgressEvent
from contracts.incident.schemas import IncidentSeed
from investigation.graph import GraphDependencies, build_investigation_graph


class RuntimeMode(StrEnum):
    """Explicitly separate application, benchmark, and test composition."""

    LIVE = "live"
    BENCHMARK = "benchmark"
    TEST = "test"


class RuntimeConfigurationError(ValueError):
    """Raised when a live runtime cannot be assembled safely."""


class SourceAdapterConfig(BaseModel):
    """Configuration for one registered, server-side source adapter."""

    source_type: SourceType
    implementation: str
    enabled: bool = True
    options: dict[str, Any] = Field(default_factory=dict)


class RuntimeSettings(BaseModel):
    """Settings required to build one investigation runtime."""

    mode: RuntimeMode = RuntimeMode.LIVE
    checkpoint_database_path: Path | None = None
    llm_provider: str = "unconfigured"
    llm_model: str = "unconfigured"
    llm_timeout_seconds: float = Field(default=180.0, gt=0)
    llm_max_output_tokens: int = Field(default=4096, ge=256)
    max_concurrency: int = Field(default=4, ge=1)
    query_timeout_seconds: float = Field(default=10.0, gt=0)
    source_configs: list[SourceAdapterConfig] = Field(default_factory=list)


def load_runtime_settings(path: str | Path) -> RuntimeSettings:
    """Load and validate the trusted JSON configuration for a live runtime.

    Configuration is deliberately file-based rather than inferred from CLI
    flags, environment heuristics, fixtures, or scenario names.  Secrets stay
    in the provider's normal credential mechanism; this file contains only the
    selected adapter and model settings.
    """

    config_path = Path(path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeConfigurationError(
            f"Could not read runtime configuration '{config_path}': {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeConfigurationError(
            f"Runtime configuration '{config_path}' is not valid JSON: {exc.msg}."
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeConfigurationError(
            f"Runtime configuration '{config_path}' must contain a JSON object."
        )
    try:
        return RuntimeSettings.model_validate(payload)
    except ValidationError as exc:
        raise RuntimeConfigurationError(
            f"Runtime configuration '{config_path}' does not match RuntimeSettings: {exc}"
        ) from exc


class CapabilityRegistry(Protocol):
    """The only registry behavior required by the application runner."""

    def capabilities(self, incident: IncidentSeed) -> SourceCapabilityCatalog: ...


ProgressCallback = Callable[[ProgressEvent], None]


class ProgressDispatcher:
    """Route node progress to the callback bound to the active investigation."""

    def __init__(self) -> None:
        self._callback: ContextVar[ProgressCallback | None] = ContextVar(
            "recoverit_progress_callback", default=None
        )

    @contextmanager
    def bind(self, callback: ProgressCallback | None) -> Iterator[None]:
        """Bind a callback for one async task without leaking it to other runs."""

        token = self._callback.set(callback)
        try:
            yield
        finally:
            self._callback.reset(token)

    def emit(self, event: ProgressEvent) -> None:
        callback = self._callback.get()
        if callback is not None:
            callback(event)


@dataclass(frozen=True, slots=True)
class RuntimeContainer:
    """Fully composed graph application dependencies.

    ``checkpoint_context`` is retained so an async SQLite-backed saver remains
    open for the life of the runner. Call :meth:`aclose` during orderly
    shutdown.
    """

    settings: RuntimeSettings
    registry: CapabilityRegistry
    graph: CompiledStateGraph
    progress_dispatcher: ProgressDispatcher
    checkpoint_context: AbstractAsyncContextManager[BaseCheckpointSaver] | None = None

    async def aclose(self) -> None:
        if self.checkpoint_context is not None:
            await self.checkpoint_context.__aexit__(None, None, None)


async def build_runtime(
    settings: RuntimeSettings,
    *,
    registry: CapabilityRegistry | None = None,
    dependencies: GraphDependencies | None = None,
) -> RuntimeContainer:
    """Compile the graph with the selected durable checkpoint backend.

    Tests may inject a registry and dependencies directly.  Live composition
    builds only configured read-only adapters and requires a real LLM client;
    it never substitutes fixtures, scripted providers, or scenario presets.
    """

    dispatcher = ProgressDispatcher()
    if registry is None or dependencies is None:
        if settings.mode is not RuntimeMode.LIVE:
            raise RuntimeConfigurationError(
                "Only live runtimes may be assembled automatically. "
                "Tests must inject their dependencies explicitly."
            )
        registry, dependencies = _build_live_dependencies(settings, dispatcher)
    if settings.mode is RuntimeMode.TEST:
        checkpointer: BaseCheckpointSaver = InMemorySaver()
        checkpoint_context: AbstractAsyncContextManager[BaseCheckpointSaver] | None = None
    else:
        path = settings.checkpoint_database_path
        if path is None:
            path = Path(".recoverit") / "checkpoints.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_context = AsyncSqliteSaver.from_conn_string(str(path))
        checkpointer = await checkpoint_context.__aenter__()

    graph_dependencies = GraphDependencies(
        collection_service=dependencies.collection_service,
        context_builder=dependencies.context_builder,
        missing_information_service=dependencies.missing_information_service,
        query_planning_service=dependencies.query_planning_service,
        hypothesis_service=dependencies.hypothesis_service,
        stopping_service=dependencies.stopping_service,
        ranking_service=dependencies.ranking_service,
        progress_sink=dispatcher,
        clock=dependencies.clock,
        remediation_planning_service=dependencies.remediation_planning_service,
    )
    graph = build_investigation_graph(graph_dependencies, checkpointer=checkpointer)
    return RuntimeContainer(
        settings=settings,
        registry=registry,
        graph=graph,
        progress_dispatcher=dispatcher,
        checkpoint_context=checkpoint_context,
    )


def _build_live_dependencies(
    settings: RuntimeSettings,
    dispatcher: ProgressDispatcher,
) -> tuple[CapabilityRegistry, GraphDependencies]:
    """Compose real adapters and services without importing benchmark code."""

    from collectors.registry import SourceRegistry
    from collectors.service import DefaultCollectionService, SystemClock
    from evidence.context.builder import DefaultContextBuilder
    from investigation.missing_information.assessor import MissingInformationAssessor
    from investigation.query_planning.planner import EvidenceQueryPlanner
    from reasoning.hypotheses.service import DefaultHypothesisService
    from reasoning.provider.clients import create_llm_client
    from reasoning.provider.llm_provider import LLMReasoningProvider
    from reasoning.ranking.ranking_engine import RankingEngine
    from reasoning.stopping import DefaultStoppingService

    client = create_llm_client(
        provider_name=settings.llm_provider,
        model=settings.llm_model if settings.llm_model != "unconfigured" else None,
        timeout=settings.llm_timeout_seconds,
        max_output_tokens=settings.llm_max_output_tokens,
    )
    if client is None:
        raise RuntimeConfigurationError(
            "No live LLM client is configured. Set llm_provider/llm_model and the "
            "corresponding credentials; live investigations never use a fixture "
            "or scripted reasoning provider."
        )

    registry = SourceRegistry(_build_source_adapters(settings.source_configs))
    clock = SystemClock()
    provider = LLMReasoningProvider(
        client=client,
        provider_name=settings.llm_provider,
        model=getattr(client, "model", settings.llm_model),
    )
    ranking = RankingEngine()
    try:
        from remediation.planner import LLMRemediationPlanningService
    except ImportError:  # pragma: no cover
        LLMRemediationPlanningService = None  # type: ignore

    remediation_service = (
        LLMRemediationPlanningService(reasoning_provider=provider)
        if LLMRemediationPlanningService is not None
        else None
    )
    dependencies = GraphDependencies(
        collection_service=DefaultCollectionService(
            registry=registry,
            clock=clock,
            max_concurrency=settings.max_concurrency,
            query_timeout_seconds=settings.query_timeout_seconds,
        ),
        context_builder=DefaultContextBuilder(),
        missing_information_service=MissingInformationAssessor(provider),
        query_planning_service=EvidenceQueryPlanner(provider),
        hypothesis_service=DefaultHypothesisService(provider),
        stopping_service=DefaultStoppingService(ranking_engine=ranking),
        ranking_service=ranking,
        progress_sink=dispatcher,
        clock=clock,
        remediation_planning_service=remediation_service,
    )
    return registry, dependencies


def _build_source_adapters(configs: list[SourceAdapterConfig]) -> list[object]:
    """Instantiate allowlisted source adapters from trusted runtime settings."""

    from collectors.changes.local_git import LocalGitChangeAdapter
    from collectors.configuration.git_configuration import GitConfigurationAdapter
    from collectors.deployments.kubernetes import KubernetesDeploymentAdapter
    from collectors.health.http_health import HttpHealthAdapter
    from collectors.logs.file import FileLogAdapter
    from collectors.metrics.prometheus import PrometheusMetricAdapter
    from collectors.pipelines.github_actions import GitHubActionsPipelineAdapter

    factories: dict[str, type[object]] = {
        "local_git": LocalGitChangeAdapter,
        "file_log": FileLogAdapter,
        "prometheus": PrometheusMetricAdapter,
        "github_actions": GitHubActionsPipelineAdapter,
        "kubernetes": KubernetesDeploymentAdapter,
        "git_configuration": GitConfigurationAdapter,
        "http_health": HttpHealthAdapter,
    }
    adapters: list[object] = []
    for config in configs:
        if not config.enabled:
            continue
        factory = factories.get(config.implementation)
        if factory is None:
            allowed = ", ".join(sorted(factories))
            raise RuntimeConfigurationError(
                f"Unsupported source implementation '{config.implementation}'. "
                f"Allowed values: {allowed}."
            )
        adapter = factory(**config.options)
        if getattr(adapter, "source_type", None) != config.source_type:
            raise RuntimeConfigurationError(
                f"Adapter '{config.implementation}' does not implement source type "
                f"'{config.source_type.value}'."
            )
        adapters.append(adapter)
    return adapters


__all__ = [
    "CapabilityRegistry",
    "ProgressDispatcher",
    "RuntimeConfigurationError",
    "RuntimeContainer",
    "RuntimeMode",
    "RuntimeSettings",
    "SourceAdapterConfig",
    "build_runtime",
    "load_runtime_settings",
]
