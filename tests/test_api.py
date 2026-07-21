"""Tests des endpoints operationnels."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder-1")


async def _noop_cleanup(self) -> None:  # type: ignore[no-untyped-def]
    """Doublure de cleanup : aucune connexion reelle n'a ete ouverte."""
    return None


@pytest.mark.unit
def test_health_returns_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /health repond 200 sans dependance externe."""
    from src.api import app

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.unit
def test_sync_endpoint_returns_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /sync declenche run_sync et renvoie son bilan."""
    from src.dependencies import AppDependencies
    from src.drive.sync import SyncReport

    async def fake_run_sync(deps, client):  # type: ignore[no-untyped-def]
        return SyncReport(ingested=2, deleted=1)

    async def fake_initialize(self: AppDependencies) -> None:
        self.mongo_client = object()  # type: ignore[assignment]

    monkeypatch.setattr("src.api.run_sync", fake_run_sync)
    monkeypatch.setattr(AppDependencies, "initialize", fake_initialize)
    monkeypatch.setattr(AppDependencies, "cleanup", _noop_cleanup)

    from src.api import app

    with TestClient(app) as client:
        response = client.post("/sync")

    assert response.status_code == 200
    body = response.json()
    assert body["ingested"] == 2
    assert body["deleted"] == 1
