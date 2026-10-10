"""Tests for the configuration-driven production entry points."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from recoverit.composition import (
    RuntimeConfigurationError,
    RuntimeMode,
    RuntimeSettings,
    load_runtime_settings,
)
from recoverit.web import configured


def test_load_runtime_settings_validates_a_json_contract(tmp_path) -> None:
    config_path = tmp_path / "runtime.json"
    config_path.write_text(
        json.dumps(
            {
                "mode": "live",
                "llm_provider": "openai",
                "llm_model": "gpt-4o-mini",
                "source_configs": [],
            }
        ),
        encoding="utf-8",
    )

    settings = load_runtime_settings(config_path)

    assert settings.mode is RuntimeMode.LIVE
    assert settings.llm_provider == "openai"


def test_load_runtime_settings_rejects_non_object_json(tmp_path) -> None:
    config_path = tmp_path / "runtime.json"
    config_path.write_text("[]", encoding="utf-8")

    with pytest.raises(RuntimeConfigurationError, match="JSON object"):
        load_runtime_settings(config_path)


def test_configured_app_owns_runtime_lifecycle(monkeypatch) -> None:
    class FakeRuntime:
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    runtime = FakeRuntime()

    async def build_fake_runtime(_):
        return runtime

    monkeypatch.setattr(configured, "build_runtime", build_fake_runtime)

    app = configured.create_configured_app(RuntimeSettings())
    with TestClient(app) as client:
        response = client.get("/")
        assert app.state.runtime is runtime

    assert response.status_code == 200
    assert response.json()["service"] == "RecoverIT Live Investigation API"
    assert runtime.closed is True
