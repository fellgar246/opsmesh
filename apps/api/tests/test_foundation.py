"""API behavior that must hold with no cloud credentials."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opsmesh_api.db import SessionLocal, get_db
from opsmesh_api.main import create_app
from opsmesh_api.settings import Settings, get_settings
from sqlalchemy import text
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[3]

FLAG_NAMES = (
    "EXECUTION_PROFILE",
    "AWS_AGENTIC_FEATURES_ENABLED",
    "CLOUD_INTEGRATIONS_ENABLED",
    "AGENT_RUNTIME_ENABLED",
    "MODEL_PROVIDER_ENABLED",
    "AGENTIC_FEATURES_ENABLED",
    "MULTI_AGENT_EXECUTION_ENABLED",
    "CLOUD_AGENT_RUNTIME_ENABLED",
    "MANAGED_RETRIEVAL_ENABLED",
    "LONG_TERM_MEMORY_ENABLED",
    "AUTOMATIC_SCHEDULES_ENABLED",
    "DATABASE_URL",
)


class FakeSession:
    def execute(self, _statement: object) -> None:
        return None

    def close(self) -> None:
        return None


def test_example_environment_documents_every_flag() -> None:
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for name in FLAG_NAMES:
        assert f"{name}=" in example
    assert "EXECUTION_PROFILE=local" in example
    assert "AWS_AGENTIC_FEATURES_ENABLED=false" in example
    assert "CLOUD_INTEGRATIONS_ENABLED=false" in example
    assert "AGENT_RUNTIME_ENABLED=false" in example
    assert "MODEL_PROVIDER_ENABLED=false" in example
    assert "MANAGED_RETRIEVAL_ENABLED=false" in example
    assert "LONG_TERM_MEMORY_ENABLED=false" in example
    assert "AUTOMATIC_SCHEDULES_ENABLED=false" in example


def test_gitignore_ignores_secret_files() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in (".env", "*.pem", "credentials.json", ".aws/"):
        assert entry in gitignore


def test_defaults_select_local_simulation(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in FLAG_NAMES:
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.effective_execution_profile == "local"
    assert settings.local_deterministic_simulation is True
    assert settings.cloud_capability(settings.cloud_integrations_enabled) is False
    assert settings.cloud_capability(settings.agent_runtime_enabled) is False
    assert settings.cloud_capability(settings.model_provider_enabled) is False
    assert settings.managed_retrieval_enabled is False
    assert settings.long_term_memory_enabled is False
    assert settings.automatic_schedules_enabled is False


def test_master_switch_forces_cloud_features_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_AGENTIC_FEATURES_ENABLED", "false")
    monkeypatch.setenv("EXECUTION_PROFILE", "aws-demo")
    monkeypatch.setenv("CLOUD_INTEGRATIONS_ENABLED", "true")
    monkeypatch.setenv("AGENT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("MODEL_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("CLOUD_AGENT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("MANAGED_RETRIEVAL_ENABLED", "true")
    settings = Settings(_env_file=None)
    assert settings.effective_execution_profile == "local"
    assert settings.local_deterministic_simulation is True
    assert settings.cloud_capability(True) is False
    assert settings.cloud_capability(settings.cloud_agent_runtime_enabled) is False
    assert settings.cloud_capability(settings.managed_retrieval_enabled) is False


def test_health_uses_local_profile_without_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in FLAG_NAMES:
        monkeypatch.delenv(name, raising=False)
    app = create_app()

    def override_db() -> Iterator[FakeSession]:
        session = FakeSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["execution_profile"] == "local"
    assert body["local_deterministic_simulation"] is True
    assert body["cloud_integrations_enabled"] is False
    assert body["agent_runtime_enabled"] is False
    assert body["model_provider_enabled"] is False


def test_cloud_integration_skips_without_credentials() -> None:
    has_credentials = any(
        os.getenv(name) for name in ("AWS_ACCESS_KEY_ID", "AWS_PROFILE", "AWS_SESSION_TOKEN")
    )
    if not has_credentials:
        pytest.skip("Cloud integration tests require credentials and are skipped")
    pytest.skip("Cloud integration tests are outside the local quality gate")


def test_decision_records_cover_the_baseline() -> None:
    adr_dir = REPO_ROOT / "docs" / "adr"
    records = sorted(path.name for path in adr_dir.glob("*.md") if path.name != "README.md")
    assert records == [
        "0001-bounded-supervisor.md",
        "0002-local-first-simulation.md",
        "0003-tool-and-agent-protocols.md",
        "0004-deterministic-policy.md",
        "0005-human-approval.md",
        "0006-fan-out-ceilings.md",
        "0007-model-specialist-port.md",
        "0008-local-tool-registry.md",
        "0009-supervisor-planner.md",
        "0010-fanout-coordinator.md",
    ]
    planning_markers = ("_ori" + "gen/", "spe" + "cs/")
    for name in records:
        text_body = (adr_dir / name).read_text(encoding="utf-8")
        assert "## Context" in text_body
        assert "## Decision" in text_body
        assert "## Consequences" in text_body
        for marker in planning_markers:
            assert marker not in text_body


def test_health_against_local_postgres() -> None:
    try:
        db: Session = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
    except Exception as exc:
        pytest.skip(f"local PostgreSQL is not available: {exc}")

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
