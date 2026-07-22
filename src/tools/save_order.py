"""Enregistrement valide des commandes clients.

Les regles que le system prompt enonce comme consignes (total correct,
coordonnees de livraison completes) sont ici des contraintes de code. Une
commande invalide ne peut pas atteindre la base, quelle que soit la sortie du
modele.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from src.models import Order

if TYPE_CHECKING:
    from src.dependencies import AppDependencies

logger = logging.getLogger(__name__)

IDEMPOTENCY_WINDOW = timedelta(minutes=2)


class OrderValidationError(Exception):
    """Commande refusee. Le message est destine a etre relu par le modele."""


async def next_order_number(deps: "AppDependencies", now: datetime) -> str:
    """
    Alloue le prochain numero de commande du jour.

    Le compteur est incremente atomiquement : deux commandes simultanees ne
    peuvent pas recevoir le meme numero.

    Args:
        deps: Dependances applicatives.
        now: Horodatage de reference, qui determine le jour du compteur.

    Returns:
        Un numero au format `LD-YYYYMMDD-NNNN`.
    """
    day = now.strftime("%Y%m%d")
    counters = deps.db[deps.settings.mongodb_collection_counters]

    document = await counters.find_one_and_update(
        {"_id": f"orders-{day}"},
        {"$inc": {"value": 1}},
        upsert=True,
        return_document=True,
    )

    return f"LD-{day}-{int(document['value']):04d}"


async def persist_order(
    deps: "AppDependencies",
    chat_id: int,
    customer_name: str,
    service_mode: str,
    items: list[dict[str, Any]],
    total_fcfa: int,
    customer_phone: str = "",
    delivery_address: str = "",
    delivery_instructions: str = "",
    now: datetime | None = None,
) -> Order:
    """
    Valide puis enregistre une commande.

    Args:
        deps: Dependances applicatives.
        chat_id: Identifiant de conversation Telegram.
        customer_name: Nom complet du client.
        service_mode: `sur_place` ou `livraison`.
        items: Articles au format `{name, quantity, unit_price, total}`.
        total_fcfa: Total annonce, verifie contre la somme des lignes.
        customer_phone: Telephone, obligatoire en livraison.
        delivery_address: Adresse, obligatoire en livraison.
        delivery_instructions: Instructions particulieres, optionnelles.
        now: Horodatage de reference, `None` pour l'instant courant.

    Returns:
        La commande enregistree, ou la commande identique deja enregistree dans
        les deux dernieres minutes.

    Raises:
        OrderValidationError: Si la commande est arithmetiquement incoherente
            ou si des coordonnees de livraison manquent.
    """
    moment = now or datetime.now(timezone.utc)

    duplicate = await deps.orders.find_one(
        {
            "chat_id": chat_id,
            "total_fcfa": total_fcfa,
            "created_at": {"$gte": moment - IDEMPOTENCY_WINDOW},
        }
    )
    if duplicate is not None:
        logger.info(
            "order_duplicate_ignored: chat_id=%d number=%s",
            chat_id,
            duplicate["order_number"],
        )
        return Order(**{k: v for k, v in duplicate.items() if k != "_id"})

    number = await next_order_number(deps, moment)

    try:
        order = Order(
            order_number=number,
            chat_id=chat_id,
            customer_name=customer_name,
            customer_phone=customer_phone,
            service_mode=service_mode,  # type: ignore[arg-type]
            delivery_address=delivery_address,
            delivery_instructions=delivery_instructions,
            items=items,  # type: ignore[arg-type]
            total_fcfa=total_fcfa,
            created_at=moment,
        )
    except ValidationError as exc:
        message = _explain(exc)
        logger.warning("order_rejected: chat_id=%d reason=%s", chat_id, message)
        raise OrderValidationError(message) from exc

    await deps.orders.insert_one(order.model_dump())
    logger.info(
        "order_saved: number=%s chat_id=%d total=%d",
        order.order_number,
        chat_id,
        order.total_fcfa,
    )
    return order


def _explain(error: ValidationError) -> str:
    """
    Transforme une erreur Pydantic en consigne lisible par le modele.

    Args:
        error: L'erreur de validation levee par `Order`.

    Returns:
        Un message d'une ligne par probleme, formule comme une instruction.
    """
    lines: list[str] = []
    for detail in error.errors():
        location = ".".join(str(part) for part in detail["loc"]) or "commande"
        lines.append(f"- {location}: {detail['msg']}")
    return "Commande refusee, corrige puis rappelle l'outil:\n" + "\n".join(lines)
