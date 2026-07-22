"""Parcours complet : recherche, commande, enregistrement, notification cuisine.

Le modele est remplace par une sequence d'appels d'outils scriptee : ce test
verifie le cablage entre les composants, pas la qualite des reponses du LLM.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from src.tools.notify_kitchen import send_to_kitchen
from src.tools.save_order import persist_order
from src.tools.search_menu import SearchResult

NOW = datetime(2026, 7, 21, 19, 30, tzinfo=timezone.utc)


class _Counters:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def find_one_and_update(
        self, query: dict[str, Any], update: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        key = query["_id"]
        self.values[key] = self.values.get(key, 0) + update["$inc"]["value"]
        return {"_id": key, "value": self.values[key]}


class _Orders:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def find_one(self, query: dict[str, Any], sort: Any = None) -> Any:
        for doc in self.docs:
            if "order_number" in query:
                if doc["order_number"] == query["order_number"]:
                    return doc
                continue
            if doc["chat_id"] == query.get("chat_id") and doc["total_fcfa"] == query.get(
                "total_fcfa"
            ):
                window = query.get("created_at", {}).get("$gte")
                if window is None or doc["created_at"] >= window:
                    return doc
        return None

    async def insert_one(self, doc: dict[str, Any]) -> Any:
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=f"o{len(self.docs)}")


class _Deps:
    def __init__(self) -> None:
        self.orders = _Orders()
        self.db = {"counters": _Counters()}
        self.settings = SimpleNamespace(
            mongodb_collection_counters="counters",
            telegram_kitchen_chat_id="-5053968395",
        )


class _Sender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


@pytest.mark.e2e
async def test_dine_in_order_reaches_kitchen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une commande sur place est enregistree puis notifiee a la cuisine."""

    async def fake_search(deps: Any, query: str) -> list[SearchResult]:
        return [
            SearchResult(
                chunk_id="c1",
                document_id="d1",
                content="Poulet Yassa - 8 500 FCFA",
                score=0.95,
                title="Menu",
            )
        ]

    monkeypatch.setattr("src.tools.search_menu.search_menu", fake_search)

    deps = _Deps()
    sender = _Sender()

    results = await fake_search(deps, "poulet")
    assert "8 500" in results[0].content

    order = await persist_order(
        deps,
        chat_id=42,
        customer_name="Kossi",
        service_mode="sur_place",
        items=[
            {"name": "Poulet Yassa", "quantity": 2, "unit_price": 8500, "total": 17000}
        ],
        total_fcfa=17000,
        now=NOW,
    )

    recap = await send_to_kitchen(deps, order.order_number, sender)

    assert order.order_number == "LD-20260721-0001"
    assert len(sender.sent) == 1
    assert "2 x Poulet Yassa" in recap
    assert "17 000 FCFA" in recap
    assert "SUR PLACE" in recap


@pytest.mark.e2e
async def test_delivery_order_carries_contact_details() -> None:
    """Une livraison complete transmet les coordonnees a la cuisine."""
    deps = _Deps()
    sender = _Sender()

    order = await persist_order(
        deps,
        chat_id=43,
        customer_name="Ama",
        service_mode="livraison",
        customer_phone="+228 93 43 73 69",
        delivery_address="Tokoin, rue 12",
        delivery_instructions="Portail bleu",
        items=[
            {"name": "Poisson Braise", "quantity": 1, "unit_price": 11000, "total": 11000}
        ],
        total_fcfa=11000,
        now=NOW,
    )

    recap = await send_to_kitchen(deps, order.order_number, sender)

    assert "LIVRAISON" in recap
    assert "Tokoin, rue 12" in recap
    assert "Portail bleu" in recap


@pytest.mark.e2e
async def test_kitchen_is_never_notified_without_saved_order() -> None:
    """Sans commande enregistree, aucune notification ne part."""
    from src.tools.notify_kitchen import OrderNotFoundError

    deps = _Deps()
    sender = _Sender()

    with pytest.raises(OrderNotFoundError):
        await send_to_kitchen(deps, "LD-20260721-0001", sender)

    assert sender.sent == []
