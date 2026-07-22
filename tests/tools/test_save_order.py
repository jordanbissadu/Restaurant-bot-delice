"""Tests de l'enregistrement des commandes."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from src.tools.save_order import (
    OrderValidationError,
    next_order_number,
    persist_order,
)

NOW = datetime(2026, 7, 21, 19, 30, tzinfo=timezone.utc)


class _FakeCounters:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def find_one_and_update(
        self, query: dict[str, Any], update: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        key = query["_id"]
        self.values[key] = self.values.get(key, 0) + update["$inc"]["value"]
        return {"_id": key, "value": self.values[key]}


class _FakeOrders:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def find_one(self, query: dict[str, Any], sort: Any = None) -> Any:
        matches = [d for d in self.docs if self._matches(d, query)]
        if not matches:
            return None
        return sorted(matches, key=lambda d: d["created_at"], reverse=True)[0]

    async def insert_one(self, doc: dict[str, Any]) -> Any:
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=f"o{len(self.docs)}")

    @staticmethod
    def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
        for key, expected in query.items():
            value = doc.get(key)
            if isinstance(expected, dict) and "$gte" in expected:
                if not (value and value >= expected["$gte"]):
                    return False
            elif value != expected:
                return False
        return True


class _FakeDeps:
    def __init__(self) -> None:
        self.orders = _FakeOrders()
        self._counters = _FakeCounters()
        self.db = {"counters": self._counters}
        self.settings = SimpleNamespace(mongodb_collection_counters="counters")


def _items() -> list[dict[str, Any]]:
    return [{"name": "Poulet Yassa", "quantity": 2, "unit_price": 8500, "total": 17000}]


@pytest.mark.unit
async def test_order_number_format_and_increment() -> None:
    """Les numeros suivent LD-YYYYMMDD-NNNN et s'incrementent."""
    deps = _FakeDeps()

    first = await next_order_number(deps, NOW)
    second = await next_order_number(deps, NOW)

    assert first == "LD-20260721-0001"
    assert second == "LD-20260721-0002"


@pytest.mark.unit
async def test_order_number_resets_per_day() -> None:
    """Le compteur repart a 1 le lendemain."""
    deps = _FakeDeps()
    await next_order_number(deps, NOW)

    tomorrow = await next_order_number(deps, NOW + timedelta(days=1))

    assert tomorrow == "LD-20260722-0001"


@pytest.mark.unit
async def test_persist_valid_order() -> None:
    """Une commande valide est ecrite en base."""
    deps = _FakeDeps()

    order = await persist_order(
        deps,
        chat_id=42,
        customer_name="Kossi",
        service_mode="sur_place",
        items=_items(),
        total_fcfa=17000,
        now=NOW,
    )

    assert order.order_number == "LD-20260721-0001"
    assert order.total_fcfa == 17000
    assert len(deps.orders.docs) == 1


@pytest.mark.unit
async def test_wrong_total_raises_explanatory_error() -> None:
    """Un total faux leve une erreur exploitable par le LLM."""
    deps = _FakeDeps()

    with pytest.raises(OrderValidationError, match="total"):
        await persist_order(
            deps,
            chat_id=42,
            customer_name="Kossi",
            service_mode="sur_place",
            items=_items(),
            total_fcfa=15000,
            now=NOW,
        )

    assert deps.orders.docs == []


@pytest.mark.unit
async def test_incomplete_delivery_raises() -> None:
    """Une livraison sans adresse est refusee avant toute ecriture."""
    deps = _FakeDeps()

    with pytest.raises(OrderValidationError, match="delivery_address"):
        await persist_order(
            deps,
            chat_id=42,
            customer_name="Kossi",
            service_mode="livraison",
            customer_phone="+228 93 43 73 69",
            items=_items(),
            total_fcfa=17000,
            now=NOW,
        )

    assert deps.orders.docs == []


@pytest.mark.unit
async def test_duplicate_within_window_returns_existing() -> None:
    """Deux enregistrements identiques rapproches ne creent qu'une commande."""
    deps = _FakeDeps()

    first = await persist_order(
        deps,
        chat_id=42,
        customer_name="Kossi",
        service_mode="sur_place",
        items=_items(),
        total_fcfa=17000,
        now=NOW,
    )
    second = await persist_order(
        deps,
        chat_id=42,
        customer_name="Kossi",
        service_mode="sur_place",
        items=_items(),
        total_fcfa=17000,
        now=NOW + timedelta(seconds=30),
    )

    assert second.order_number == first.order_number
    assert len(deps.orders.docs) == 1


@pytest.mark.unit
async def test_duplicate_outside_window_creates_new_order() -> None:
    """Passe la fenetre d'idempotence, une nouvelle commande est creee."""
    deps = _FakeDeps()
    await persist_order(
        deps,
        chat_id=42,
        customer_name="Kossi",
        service_mode="sur_place",
        items=_items(),
        total_fcfa=17000,
        now=NOW,
    )

    second = await persist_order(
        deps,
        chat_id=42,
        customer_name="Kossi",
        service_mode="sur_place",
        items=_items(),
        total_fcfa=17000,
        now=NOW + timedelta(minutes=10),
    )

    assert second.order_number == "LD-20260721-0002"
    assert len(deps.orders.docs) == 2
