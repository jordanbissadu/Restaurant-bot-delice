"""Tests des modeles de donnees et de leurs validateurs."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.models import DishPhoto, Order, OrderItem


def _item(name: str, qty: int, price: int) -> OrderItem:
    return OrderItem(name=name, quantity=qty, unit_price=price, total=qty * price)


def _order(**overrides: object) -> Order:
    payload: dict[str, object] = {
        "order_number": "LD-20260721-0001",
        "chat_id": 42,
        "customer_name": "Kossi",
        "service_mode": "sur_place",
        "items": [_item("Poisson Braise", 2, 11000)],
        "total_fcfa": 22000,
        "created_at": datetime.now(timezone.utc),
    }
    payload.update(overrides)
    return Order(**payload)  # type: ignore[arg-type]


@pytest.mark.unit
def test_order_accepts_coherent_totals() -> None:
    """Une commande dont l'arithmetique est juste est acceptee."""
    order = _order()
    assert order.total_fcfa == 22000
    assert order.status == "pending"


@pytest.mark.unit
def test_order_item_rejects_wrong_line_total() -> None:
    """Un total de ligne different de quantity * unit_price est rejete."""
    with pytest.raises(ValidationError, match="total de ligne"):
        OrderItem(name="Yassa", quantity=2, unit_price=8500, total=8500)


@pytest.mark.unit
def test_order_rejects_wrong_grand_total() -> None:
    """Un total general different de la somme des lignes est rejete."""
    with pytest.raises(ValidationError, match="total_fcfa"):
        _order(total_fcfa=20000)


@pytest.mark.unit
def test_order_rejects_zero_quantity() -> None:
    """Une quantite nulle ou negative est rejetee."""
    with pytest.raises(ValidationError):
        OrderItem(name="Yassa", quantity=0, unit_price=8500, total=0)


@pytest.mark.unit
def test_order_rejects_delivery_without_phone() -> None:
    """Une livraison sans telephone est rejetee."""
    with pytest.raises(ValidationError, match="customer_phone"):
        _order(service_mode="livraison", delivery_address="Tokoin, rue 12")


@pytest.mark.unit
def test_order_rejects_delivery_without_address() -> None:
    """Une livraison sans adresse est rejetee."""
    with pytest.raises(ValidationError, match="delivery_address"):
        _order(service_mode="livraison", customer_phone="+228 93 43 73 69")


@pytest.mark.unit
def test_order_accepts_complete_delivery() -> None:
    """Une livraison complete est acceptee."""
    order = _order(
        service_mode="livraison",
        customer_phone="+228 93 43 73 69",
        delivery_address="Tokoin, rue 12",
    )
    assert order.service_mode == "livraison"


@pytest.mark.unit
def test_order_requires_at_least_one_item() -> None:
    """Une commande vide est rejetee."""
    with pytest.raises(ValidationError):
        _order(items=[], total_fcfa=0)


@pytest.mark.unit
def test_dish_photo_defaults() -> None:
    """Une photo est active et sans cache Telegram par defaut."""
    photo = DishPhoto(
        dish_name="Poulet Yassa",
        dish_key="poulet yassa",
        drive_file_id="drive-1",
        file_name="poulet-yassa.jpg",
        drive_modified_time=datetime(2026, 7, 23, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
    )

    assert photo.enabled is True
    assert photo.telegram_file_id == ""
    assert photo.content_hash == ""
    assert photo.position == 1


@pytest.mark.unit
def test_dish_photo_rejects_position_zero() -> None:
    """La position commence a 1."""
    with pytest.raises(ValidationError):
        DishPhoto(
            dish_name="Poulet Yassa",
            dish_key="poulet yassa",
            drive_file_id="drive-1",
            file_name="poulet-yassa.jpg",
            position=0,
            drive_modified_time=datetime(2026, 7, 23, tzinfo=timezone.utc),
            updated_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
        )
