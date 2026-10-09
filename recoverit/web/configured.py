"""Configured production factory for the fixture-free RecoverIT HTTP API."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from recoverit.composition import RuntimeSettings, build_runtime
from recoverit.runner import InvestigationRunner
from recoverit.web.live import create_live_app


def create_configured_app(settings: RuntimeSettings) -> FastAPI:
    """Build the live API and close its durable runtime during shutdown.

    The caller must provide validated settings.  This factory never loads
    benchmark scenarios, replay fixtures, or scripted reasoning providers.
    """

    runtime = build_runtime(settings)
    executor = InvestigationRunner(runtime)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            runtime.close()

    app = create_live_app(executor, lifespan=lifespan)
    app.state.runtime = runtime
    return app


__all__ = ["create_configured_app"]
