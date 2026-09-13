"""Focused tests for live client request and local Ollama selection behavior."""

from __future__ import annotations

import json

import pytest

from reasoning.provider import clients


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "model": "qwen3.5:9b",
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3},
        }


class _AsyncClient:
    def __init__(self, captured: dict, **kwargs) -> None:
        self._captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def post(self, url: str, *, json: dict, headers: dict):
        self._captured.update(url=url, payload=json, headers=headers)
        return _Response()


@pytest.mark.asyncio
async def test_ollama_request_disables_reasoning_and_bounds_output(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        clients.httpx,
        "AsyncClient",
        lambda **kwargs: _AsyncClient(captured, **kwargs),
    )
    client = clients.OpenAICompatibleClient(
        api_key="ollama",
        base_url="http://localhost:11434/v1",
        model="qwen3.5:9b",
        reasoning_effort="none",
        max_tokens=1536,
    )

    response = await client.complete("return JSON")

    assert json.loads(response.content) == {"ok": True}
    assert captured["payload"]["reasoning_effort"] == "none"
    assert captured["payload"]["max_tokens"] == 1536


def test_ollama_factory_selects_an_installed_model(monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.setattr(clients, "get_ollama_models", lambda host=None: ["qwen3.5:9b"])

    client = clients.create_llm_client(provider_name="ollama")

    assert isinstance(client, clients.OpenAICompatibleClient)
    assert client.model == "qwen3.5:9b"
    assert client.base_url == "http://localhost:11434/v1"
    assert client.reasoning_effort == "none"


def test_ollama_factory_rejects_a_missing_model(monkeypatch) -> None:
    monkeypatch.setattr(clients, "get_ollama_models", lambda host=None: ["qwen3.5:9b"])

    with pytest.raises(ValueError, match="is not installed"):
        clients.create_llm_client(provider_name="ollama", model="missing:latest")
