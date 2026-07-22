"""Tests du traitement d'un message Telegram."""

from types import SimpleNamespace
from typing import Any

import pytest

from src.telegram_bot import TelegramSender, handle_message


class _SpyBot:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {"loaded": [], "saved": [], "cleared": []}

    async def fake_load(deps: Any, chat_id: int) -> list[str]:
        calls["loaded"].append(chat_id)
        return ["histoire"]

    async def fake_save(deps: Any, chat_id: int, messages: list[Any]) -> None:
        calls["saved"].append((chat_id, messages))

    async def fake_clear(deps: Any, chat_id: int) -> None:
        calls["cleared"].append(chat_id)

    async def fake_answer(agent: Any, deps: Any, text: str, history: list[Any]):
        calls["history"] = history
        return SimpleNamespace(text=f"reponse a {text}", new_messages=["m1", "m2"])

    monkeypatch.setattr("src.telegram_bot.load_history", fake_load)
    monkeypatch.setattr("src.telegram_bot.save_turn", fake_save)
    monkeypatch.setattr("src.telegram_bot.clear_history", fake_clear)
    monkeypatch.setattr("src.telegram_bot.answer", fake_answer)
    return calls


@pytest.mark.unit
async def test_message_is_answered_with_history(wired) -> None:
    """L'historique est charge, la reponse produite, le tour persiste."""
    reply = await handle_message(
        deps=object(), agent=object(), chat_id=42, text="Bonjour", sender=object()
    )

    assert reply == "reponse a Bonjour"
    assert wired["loaded"] == [42]
    assert wired["history"] == ["histoire"]
    assert wired["saved"] == [(42, ["m1", "m2"])]


@pytest.mark.unit
async def test_reset_command_clears_history(wired) -> None:
    """La commande /reset efface la memoire sans appeler l'agent."""
    reply = await handle_message(
        deps=object(), agent=object(), chat_id=42, text="/reset", sender=object()
    )

    assert wired["cleared"] == [42]
    assert wired["saved"] == []
    assert "reinitialisee" in reply.lower() or "réinitialisée" in reply.lower()


@pytest.mark.unit
async def test_empty_message_is_ignored(wired) -> None:
    """Un message vide ne declenche ni agent ni ecriture."""
    reply = await handle_message(
        deps=object(), agent=object(), chat_id=42, text="   ", sender=object()
    )

    assert reply == ""
    assert wired["loaded"] == []
    assert wired["saved"] == []


@pytest.mark.unit
async def test_agent_failure_returns_friendly_message(
    monkeypatch: pytest.MonkeyPatch, wired
) -> None:
    """Une erreur de l'agent produit un message poli, pas une trace."""

    async def failing(*args: Any, **kwargs: Any):
        raise RuntimeError("OpenAI indisponible")

    monkeypatch.setattr("src.telegram_bot.answer", failing)

    reply = await handle_message(
        deps=object(), agent=object(), chat_id=42, text="Bonjour", sender=object()
    )

    assert "93 43 73 69" in reply
    assert "OpenAI" not in reply
    assert wired["saved"] == []


@pytest.mark.unit
async def test_telegram_sender_forwards_to_bot() -> None:
    """TelegramSender delegue l'envoi au bot."""
    bot = _SpyBot()
    sender = TelegramSender(bot)

    await sender.send("-500", "recap")

    assert bot.sent == [("-500", "recap")]
