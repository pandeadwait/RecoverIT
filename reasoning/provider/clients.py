"""Concrete live LLM client adapters implementing the LLMClient protocol.

Supports OpenAI, Google Gemini, Groq, DeepSeek, and local Ollama via standard HTTP.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
import urllib.request

import httpx

from reasoning.provider.llm_provider import LLMClient, LLMResponse

logger = logging.getLogger(__name__)


class OpenAICompatibleClient(LLMClient):
    """Client for OpenAI and OpenAI-compatible APIs (Ollama, Groq, DeepSeek, vLLM)."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        timeout: float = 60.0,
        reasoning_effort: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.reasoning_effort = reasoning_effort
        self.max_tokens = max_tokens

    async def complete(
        self,
        prompt: str,
        system_instruction: str | None = None,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens

        # Enable structured JSON output if schema provided or json output requested
        if json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_investigation_output",
                    "strict": False,
                    "schema": json_schema,
                },
            }
        else:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        content = choice["message"]["content"]
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        return LLMResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_metadata={"model": data.get("model", self.model)},
        )


class GeminiClient(LLMClient):
    """Client for Google Gemini Generative Language API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-3.5-flash-lite",
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        model_aliases = {
            "gemini-1.5-flash": "gemini-3.5-flash-lite",
            "gemini-1.5-pro": "gemini-3.5-flash",
            "gemini-2.5-flash": "gemini-3.5-flash-lite",
            "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
            "gemini-2.5-pro": "gemini-3.5-flash",
            "gemini-flash": "gemini-3.5-flash-lite",
            "gemini-pro": "gemini-3.5-flash",
        }
        raw_model = model or "gemini-3.5-flash-lite"
        self.model = model_aliases.get(raw_model, raw_model)
        self.timeout = timeout
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    async def complete(
        self,
        prompt: str,
        system_instruction: str | None = None,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        url = f"{self.base_url}/models/{self.model}:generateContent"
        params = {"key": self.api_key}

        generation_config: dict[str, Any] = {
            "temperature": temperature,
            "responseMimeType": "application/json",
        }

        effective_system = system_instruction or ""
        if json_schema:
            effective_system = (
                f"{effective_system}\n\n"
                f"You MUST output ONLY valid JSON conforming strictly to this schema:\n"
                f"{json.dumps(json_schema)}"
            ).strip()

        body: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": generation_config,
        }

        if effective_system:
            body["systemInstruction"] = {
                "parts": [{"text": effective_system}],
            }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                params=params,
                json=body,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()

        candidate = data["candidates"][0]
        text_content = candidate["content"]["parts"][0]["text"]
        usage = data.get("usageMetadata", {})
        input_tokens = usage.get("promptTokenCount", 0)
        output_tokens = usage.get("candidatesTokenCount", 0)

        return LLMResponse(
            content=text_content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_metadata={"model": self.model},
        )


def _ollama_api_root(host: str | None = None) -> str:
    """Normalize Ollama's native API root from either native or /v1 URLs."""
    value = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
    if value.endswith("/v1"):
        value = value[:-3]
    if value.endswith("/api"):
        value = value[:-4]
    return value.rstrip("/")


def get_ollama_models(host: str | None = None) -> list[str]:
    """Return locally installed Ollama model names, or an empty list if unavailable."""
    try:
        with urllib.request.urlopen(f"{_ollama_api_root(host)}/api/tags", timeout=2.0) as response:
            data = json.load(response)
        return [str(item["name"]) for item in data.get("models", []) if item.get("name")]
    except Exception:
        return []


def is_ollama_available(host: str | None = None) -> bool:
    """Check whether local Ollama daemon is reachable."""
    try:
        with urllib.request.urlopen(f"{_ollama_api_root(host)}/api/tags", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def create_llm_client(
    provider_name: str = "auto",
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> LLMClient | None:
    """
    Factory creating the appropriate LLMClient based on settings and environment.

    Supports:
    - Gemini (via GEMINI_API_KEY or provider_name="gemini")
    - OpenAI / Groq / DeepSeek (via OPENAI_API_KEY or provider_name="openai")
    - Ollama local inference (via provider_name="ollama" or auto-detected on localhost:11434)
    """
    prov = (provider_name or "auto").lower().strip()

    if prov == "gemini":
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            return None
        return GeminiClient(api_key=key, model=model or "gemini-1.5-flash")

    if prov == "openai":
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            return None
        return OpenAICompatibleClient(api_key=key, model=model or "gpt-4o-mini")

    if prov == "ollama":
        ollama_root = _ollama_api_root(base_url)
        installed_models = get_ollama_models(ollama_root)
        if not installed_models:
            raise RuntimeError(f"Ollama is not reachable at {ollama_root} or has no installed models.")
        selected_model = model or os.environ.get("OLLAMA_MODEL") or installed_models[0]
        if selected_model not in installed_models:
            raise ValueError(
                f"Ollama model '{selected_model}' is not installed. "
                f"Available models: {', '.join(installed_models)}"
            )
        return OpenAICompatibleClient(
            api_key="ollama",
            base_url=f"{ollama_root}/v1",
            model=selected_model,
            timeout=90.0,
            reasoning_effort="none",
            max_tokens=1536,
        )

    # Auto-detection priority:
    # 1. Cloud Gemini if API key set
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiClient(model=model or "gemini-1.5-flash")

    # 2. Cloud OpenAI if API key set
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAICompatibleClient(model=model or "gpt-4o-mini")

    # 3. Local Ollama if daemon is active
    ollama_root = _ollama_api_root()
    if is_ollama_available(ollama_root):
        installed_models = get_ollama_models(ollama_root)
        target_model = model or os.environ.get("OLLAMA_MODEL") or installed_models[0]
        if target_model not in installed_models:
            raise ValueError(
                f"Ollama model '{target_model}' is not installed. "
                f"Available models: {', '.join(installed_models)}"
            )
        logger.info("Auto-detected local Ollama instance on %s (model: %s)", ollama_root, target_model)
        return OpenAICompatibleClient(
            api_key="ollama",
            base_url=f"{ollama_root}/v1",
            model=target_model,
            timeout=90.0,
            reasoning_effort="none",
            max_tokens=1536,
        )

    return None
