"""Configured production factory for the fixture-free RecoverIT HTTP API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from contracts.errors.schemas import ProgressEvent
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import InvestigationBudget
from recoverit.composition import RuntimeSettings, build_runtime
from recoverit.runner import InvestigationResult, InvestigationRunner
from recoverit.web.live import create_live_app, result_payload


class _ConfiguredExecutor:
    """Bind the live runner during application startup, not at import time."""

    def __init__(self) -> None:
        self._runner: InvestigationRunner | None = None

    def configure(self, runtime) -> None:
        self._runner = InvestigationRunner(runtime)

    def _require_runner(self) -> InvestigationRunner:
        if self._runner is None:
            raise RuntimeError("The RecoverIT runtime has not started.")
        return self._runner

    async def run(
        self,
        incident: IncidentSeed,
        budget: InvestigationBudget | None = None,
        progress_callback: Callable[[ProgressEvent], None] | None = None,
    ) -> InvestigationResult:
        return await self._require_runner().run(incident, budget, progress_callback)

    async def resume(
        self,
        incident_id: str,
        progress_callback: Callable[[ProgressEvent], None] | None = None,
    ) -> InvestigationResult:
        return await self._require_runner().resume(incident_id, progress_callback)


def create_configured_app(settings: RuntimeSettings) -> FastAPI:
    """Build the live API and close its durable runtime during shutdown.

    The caller must provide validated settings.  This factory never loads
    benchmark scenarios, replay fixtures, or scripted reasoning providers.
    """

    executor = _ConfiguredExecutor()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = await build_runtime(settings)
        executor.configure(runtime)
        app.state.runtime = runtime
        try:
            yield
        finally:
            await runtime.aclose()

    app = create_live_app(executor, lifespan=lifespan)

    @app.get("/api/investigations/{incident_id}")
    async def get_investigation(incident_id: str) -> dict[str, object]:
        try:
            result = await executor.resume(incident_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Failed to retrieve investigation.") from exc
        return result_payload(result)

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/ui", include_in_schema=False)
        @app.get("/ui/", include_in_schema=False)
        async def ui_page():
            return FileResponse(str(static_dir / "index.html"))

    return app


__all__ = ["create_configured_app"]
