"""Runtime composition primitives for LangGraph investigations.

This module owns runtime-only objects: source discovery, graph dependencies,
checkpoint persistence, and progress dispatch.  It deliberately does not place
service objects or callbacks in graph state.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

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
    max_concurrency: int = Field(default=4, ge=1)
    query_timeout_seconds: float = Field(default=10.0, gt=0)
    source_configs: list[SourceAdapterConfig] = Field(default_factory=list)


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

    ``checkpoint_context`` is retained so a SQLite-backed saver remains open for
    the life of the runner.  Call :meth:`close` during orderly shutdown.
    """

    settings: RuntimeSettings
    registry: CapabilityRegistry
    graph: CompiledStateGraph
    progress_dispatcher: ProgressDispatcher
    checkpoint_context: AbstractContextManager[BaseCheckpointSaver] | None = None

    def close(self) -> None:
        if self.checkpoint_context is not None:
            self.checkpoint_context.__exit__(None, None, None)


def build_runtime(
    settings: RuntimeSettings,
    *,
    registry: CapabilityRegistry,
    dependencies: GraphDependencies,
) -> RuntimeContainer:
    """Compile the graph with the selected durable checkpoint backend.

    Concrete collector and reasoning services are supplied by their owners and
    injected through ``GraphDependencies``.  This keeps this composition root
    generic and prevents benchmark fixtures from entering live runtime code.
    """

    dispatcher = ProgressDispatcher()
    if settings.mode is RuntimeMode.TEST:
        checkpointer: BaseCheckpointSaver = InMemorySaver()
        checkpoint_context: AbstractContextManager[BaseCheckpointSaver] | None = None
    else:
        path = settings.checkpoint_database_path
        if path is None:
            path = Path(".recoverit") / "checkpoints.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_context = SqliteSaver.from_conn_string(str(path))
        checkpointer = checkpoint_context.__enter__()

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
    )
    graph = build_investigation_graph(graph_dependencies, checkpointer=checkpointer)
    return RuntimeContainer(
        settings=settings,
        registry=registry,
        graph=graph,
        progress_dispatcher=dispatcher,
        checkpoint_context=checkpoint_context,
    )


__all__ = [
    "CapabilityRegistry",
    "ProgressDispatcher",
    "RuntimeContainer",
    "RuntimeMode",
    "RuntimeSettings",
    "SourceAdapterConfig",
    "build_runtime",
]
