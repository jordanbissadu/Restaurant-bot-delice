"""Tests du conteneur de dependances."""

from typing import Any

import pytest

from src.dependencies import AppDependencies


class _FakeAdmin:
    def __init__(self) -> None:
        self.pinged = False

    async def command(self, name: str) -> dict[str, int]:
        assert name == "ping"
        self.pinged = True
        return {"ok": 1}


class _FakeMongoClient:
    def __init__(self, uri: str, **kwargs: Any) -> None:
        self.uri = uri
        self.admin = _FakeAdmin()
        self.closed = False
        self._dbs: dict[str, dict[str, str]] = {}

    def __getitem__(self, name: str) -> dict[str, str]:
        return self._dbs.setdefault(name, {})

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder-1")


@pytest.mark.unit
async def test_initialize_pings_mongodb(monkeypatch: pytest.MonkeyPatch) -> None:
    """initialize() verifie la connexion MongoDB par un ping."""
    monkeypatch.setattr("src.dependencies.AsyncMongoClient", _FakeMongoClient)

    deps = AppDependencies()
    await deps.initialize()

    assert isinstance(deps.mongo_client, _FakeMongoClient)
    assert deps.mongo_client.admin.pinged is True
    assert deps.openai_client is not None


@pytest.mark.unit
async def test_cleanup_closes_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """cleanup() ferme le client MongoDB et remet l'etat a zero."""
    monkeypatch.setattr("src.dependencies.AsyncMongoClient", _FakeMongoClient)

    deps = AppDependencies()
    await deps.initialize()
    client = deps.mongo_client
    await deps.cleanup()

    assert client.closed is True
    assert deps.mongo_client is None
    assert deps.db is None


@pytest.mark.unit
async def test_context_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le context manager initialise puis nettoie."""
    monkeypatch.setattr("src.dependencies.AsyncMongoClient", _FakeMongoClient)

    async with AppDependencies() as deps:
        assert deps.mongo_client is not None
        client = deps.mongo_client

    assert client.closed is True
