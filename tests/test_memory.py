"""Tests de la memoire conversationnelle."""

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from src.memory import clear_history, load_history, save_turn


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def sort(self, key: str, direction: int) -> "_FakeCursor":
        self.docs = sorted(self.docs, key=lambda d: d[key], reverse=direction < 0)
        return self

    def limit(self, count: int) -> "_FakeCursor":
        self.docs = self.docs[:count]
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self.docs


class _FakeMessages:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    def find(self, query: dict[str, Any]) -> _FakeCursor:
        return _FakeCursor([d for d in self.docs if d["chat_id"] == query["chat_id"]])

    async def insert_many(self, docs: list[dict[str, Any]]) -> None:
        self.docs.extend(docs)

    async def count_documents(self, query: dict[str, Any]) -> int:
        return len([d for d in self.docs if d["chat_id"] == query["chat_id"]])

    async def delete_many(self, query: dict[str, Any]) -> None:
        self.docs = [d for d in self.docs if d["chat_id"] != query["chat_id"]]


class _FakeConversations:
    def __init__(self) -> None:
        self.docs: dict[int, dict[str, Any]] = {}

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False
    ) -> None:
        doc = self.docs.setdefault(query["chat_id"], {"chat_id": query["chat_id"]})
        doc.update(update.get("$set", {}))

    async def delete_many(self, query: dict[str, Any]) -> None:
        self.docs.pop(query["chat_id"], None)


class _FakeDeps:
    def __init__(self, max_messages: int = 50) -> None:
        self._messages = _FakeMessages()
        self._conversations = _FakeConversations()
        self.db = {"messages": self._messages, "conversations": self._conversations}
        self.settings = SimpleNamespace(
            mongodb_collection_messages="messages",
            mongodb_collection_conversations="conversations",
            memory_max_messages=max_messages,
        )


def _turn(user_text: str, reply: str) -> list[Any]:
    return [
        ModelRequest(parts=[UserPromptPart(content=user_text)]),
        ModelResponse(parts=[TextPart(content=reply)]),
    ]


@pytest.mark.unit
async def test_empty_history() -> None:
    """Une conversation inconnue renvoie un historique vide."""
    deps = _FakeDeps()

    assert await load_history(deps, chat_id=1) == []


@pytest.mark.unit
async def test_roundtrip_preserves_messages() -> None:
    """Un tour sauvegarde est relu a l'identique."""
    deps = _FakeDeps()
    await save_turn(deps, chat_id=1, messages=_turn("Bonjour", "Bonjour !"))

    history = await load_history(deps, chat_id=1)

    assert len(history) == 2
    assert isinstance(history[0], ModelRequest)
    assert isinstance(history[1], ModelResponse)
    assert history[1].parts[0].content == "Bonjour !"


@pytest.mark.unit
async def test_history_is_isolated_per_chat() -> None:
    """Deux conversations ne se melangent pas."""
    deps = _FakeDeps()
    await save_turn(deps, chat_id=1, messages=_turn("A", "a"))
    await save_turn(deps, chat_id=2, messages=_turn("B", "b"))

    history = await load_history(deps, chat_id=1)

    assert len(history) == 2
    assert history[0].parts[0].content == "A"


@pytest.mark.unit
async def test_history_is_capped_and_chronological() -> None:
    """Seuls les N derniers messages sont relus, dans l'ordre chronologique."""
    deps = _FakeDeps(max_messages=4)
    for i in range(5):
        await save_turn(deps, chat_id=1, messages=_turn(f"q{i}", f"r{i}"))

    history = await load_history(deps, chat_id=1)

    assert len(history) == 4
    assert history[0].parts[0].content == "q3"
    assert history[-1].parts[0].content == "r4"


@pytest.mark.unit
async def test_clear_history_removes_everything() -> None:
    """clear_history vide les messages et la conversation."""
    deps = _FakeDeps()
    await save_turn(deps, chat_id=1, messages=_turn("Bonjour", "Bonjour !"))

    await clear_history(deps, chat_id=1)

    assert await load_history(deps, chat_id=1) == []
