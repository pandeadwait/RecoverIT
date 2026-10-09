"""Fixture-free HTTP interface for a composed LangGraph investigation runtime."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
from typing import AsyncIterator, Callable, Protocol

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from contracts.errors.schemas import ProgressEvent
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import InvestigationBudget
from recoverit.runner import InvestigationResult


class InvestigationExecutor(Protocol):
    """Application boundary used by the live HTTP routes."""

    async def run(
        self,
        incident: IncidentSeed,
        budget: InvestigationBudget | None = None,
        progress_callback: Callable[[ProgressEvent], None] | None = None,
    ) -> InvestigationResult: ...

    async def resume(
        self,
        incident_id: str,
        progress_callback: Callable[[ProgressEvent], None] | None = None,
    ) -> InvestigationResult: ...


class InvestigationRequest(BaseModel):
    """A generic, validated incident request with no scenario-specific fields."""

    incident: IncidentSeed
    budget: InvestigationBudget | None = None


def result_payload(result: InvestigationResult) -> dict[str, object]:
    """Return the stable result DTO plus its downloadable Markdown report."""

    payload = asdict(result)
    payload["markdown_report"] = result.to_markdown_report()
    return payload


def create_live_app(executor: InvestigationExecutor) -> FastAPI:
    """Create a live API bound to a pre-composed, fixture-free runtime."""

    app = FastAPI(
        title="RecoverIT Live Investigation API",
        description="Incident investigation through the LangGraph runtime.",
        version="0.2.0",
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/investigations")
    async def investigate(request: InvestigationRequest) -> dict[str, object]:
        try:
            result = await executor.run(request.incident, request.budget)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Investigation execution failed.") from exc
        return result_payload(result)

    @app.post("/api/investigations/{incident_id}/resume")
    async def resume(incident_id: str) -> dict[str, object]:
        try:
            result = await executor.resume(incident_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Investigation resume failed.") from exc
        return result_payload(result)

    @app.post("/api/investigations/stream")
    async def investigate_stream(request: InvestigationRequest) -> StreamingResponse:
        """Stream typed progress events followed by the terminal result as SSE."""

        loop = asyncio.get_running_loop()
        events: asyncio.Queue[ProgressEvent | None] = asyncio.Queue()

        def publish(event: ProgressEvent) -> None:
            loop.call_soon_threadsafe(events.put_nowait, event)

        task = asyncio.create_task(
            executor.run(
                request.incident,
                request.budget,
                progress_callback=publish,
            )
        )
        task.add_done_callback(
            lambda _: loop.call_soon_threadsafe(events.put_nowait, None)
        )

        async def event_stream() -> AsyncIterator[str]:
            try:
                while True:
                    event = await events.get()
                    if event is None:
                        break
                    yield _sse("progress", event.model_dump(mode="json"))
                yield _sse("result", result_payload(await task))
            except ValueError as exc:
                yield _sse("error", {"detail": str(exc), "status": 422})
            except Exception:
                yield _sse("error", {"detail": "Investigation execution failed.", "status": 500})

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return app


def _sse(event_name: str, payload: dict[str, object]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, default=str)}\n\n"


__all__ = [
    "InvestigationExecutor",
    "InvestigationRequest",
    "create_live_app",
    "result_payload",
]
