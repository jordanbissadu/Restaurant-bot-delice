"""Tests de la notification cuisine."""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from src.models import Order, OrderItem
from src.tools.notify_kitchen import (
    OrderNotFoundError,
    format_kitchen_recap,
    send_to_kitchen,
)

NOW = datetime(2026, 7, 21, 19, 30, tzinfo=timezone.utc)


class _FakeOrders:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self.docs:
            if doc.get("order_number") == query.get("order_number"):
                return doc
        return None


class _FakeDeps:
    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self.orders = _FakeOrders(docs or [])
        self.settings = SimpleNamespace(telegram_kitchen_chat_id="-5053968395")


class _SpySender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def _order(**overrides: Any) -> Order:
    payload: dict[str, Any] = {
        "order_number": "LD-20260721-0001",
        "chat_id": 42,
        "customer_name": "Kossi",
        "service_mode": "sur_place",
        "items": [
            OrderItem(name="Poulet Yassa", quantity=2, unit_price=8500, total=17000),
            OrderItem(name="Coca", quantity=1, unit_price=1000, total=1000),
        ],
        "total_fcfa": 18000,
        "created_at": NOW,
    }
    payload.update(overrides)
    return Order(**payload)


@pytest.mark.unit
def test_recap_contains_all_lines() -> None:
    """Le recap liste chaque article avec quantite et prix."""
    recap = format_kitchen_recap(_order())

    assert "LD-20260721-0001" in recap
    assert "2 x Poulet Yassa" in recap
    assert "17 000 FCFA" in recap
    assert "1 x Coca" in recap
    assert "18 000 FCFA" in recap


@pytest.mark.unit
def test_recap_marks_dine_in() -> None:
    """Une commande sur place est identifiee comme telle."""
    recap = format_kitchen_recap(_order())

    assert "SUR PLACE" in recap
    assert "Adresse" not in recap


@pytest.mark.unit
def test_recap_includes_delivery_details() -> None:
    """Une livraison affiche telephone, adresse et instructions."""
    recap = format_kitchen_recap(
        _order(
            service_mode="livraison",
            customer_phone="+228 93 43 73 69",
            delivery_address="Tokoin, rue 12",
            delivery_instructions="Portail bleu",
        )
    )

    assert "LIVRAISON" in recap
    assert "+228 93 43 73 69" in recap
    assert "Tokoin, rue 12" in recap
    assert "Portail bleu" in recap


@pytest.mark.unit
async def test_send_uses_order_from_database() -> None:
    """Le message envoye est construit depuis la commande en base."""
    deps = _FakeDeps([_order().model_dump()])
    sender = _SpySender()

    recap = await send_to_kitchen(deps, "LD-20260721-0001", sender)

    assert len(sender.sent) == 1
    chat_id, text = sender.sent[0]
    assert chat_id == "-5053968395"
    assert "LD-20260721-0001" in text
    assert text == recap


@pytest.mark.unit
async def test_send_rejects_unknown_order() -> None:
    """Notifier une commande inexistante est refuse, rien n'est envoye."""
    deps = _FakeDeps([])
    sender = _SpySender()

    with pytest.raises(OrderNotFoundError, match="LD-20260721-9999"):
        await send_to_kitchen(deps, "LD-20260721-9999", sender)

    assert sender.sent == []


@pytest.mark.unit
async def test_send_without_kitchen_chat_configured() -> None:
    """Sans chat cuisine configure, l'envoi est refuse explicitement."""
    deps = _FakeDeps([_order().model_dump()])
    deps.settings.telegram_kitchen_chat_id = ""
    sender = _SpySender()

    with pytest.raises(ValueError, match="TELEGRAM_KITCHEN_CHAT_ID"):
        await send_to_kitchen(deps, "LD-20260721-0001", sender)

    assert sender.sent == []
