"""Notification de l'equipe cuisine sur Telegram.

Le recapitulatif est reconstruit depuis la commande enregistree en base. Un
recap redige par le modele pourrait diverger de ce qui a ete enregistre : la
cuisine preparerait alors un plat qui n'est pas celui de la commande.
"""

import logging
from typing import TYPE_CHECKING, Protocol

from src.models import Order

if TYPE_CHECKING:
    from src.dependencies import AppDependencies

logger = logging.getLogger(__name__)


class OrderNotFoundError(Exception):
    """Levee quand le numero de commande a notifier n'existe pas en base."""


class KitchenSender(Protocol):
    """Canal d'envoi vers le groupe cuisine."""

    async def send(self, chat_id: str, text: str) -> None:
        """Envoie un message texte a une conversation."""
        ...


def _fcfa(amount: int) -> str:
    """
    Formate un montant en FCFA avec des espaces comme separateur de milliers.

    Args:
        amount: Montant entier en FCFA.

    Returns:
        Par exemple `17 000 FCFA`.
    """
    return f"{amount:,}".replace(",", " ") + " FCFA"


def format_kitchen_recap(order: Order) -> str:
    """
    Construit le recapitulatif destine a la cuisine.

    Args:
        order: Commande enregistree.

    Returns:
        Le texte du message, en clair.
    """
    mode = "LIVRAISON" if order.service_mode == "livraison" else "SUR PLACE"

    lines = [
        f"NOUVELLE COMMANDE {order.order_number}",
        f"Mode: {mode}",
        f"Client: {order.customer_name}",
        "",
    ]
    lines.extend(
        f"- {item.quantity} x {item.name} — {_fcfa(item.total)}" for item in order.items
    )
    lines.append("")
    lines.append(f"TOTAL: {_fcfa(order.total_fcfa)}")

    if order.service_mode == "livraison":
        lines.append("")
        lines.append(f"Telephone: {order.customer_phone}")
        lines.append(f"Adresse: {order.delivery_address}")
        if order.delivery_instructions:
            lines.append(f"Instructions: {order.delivery_instructions}")

    return "\n".join(lines)


async def send_to_kitchen(
    deps: "AppDependencies", order_number: str, sender: KitchenSender
) -> str:
    """
    Envoie le recapitulatif d'une commande a l'equipe cuisine.

    Args:
        deps: Dependances applicatives.
        order_number: Numero renvoye par `save_order`.
        sender: Canal d'envoi Telegram.

    Returns:
        Le texte effectivement envoye.

    Raises:
        OrderNotFoundError: Si aucune commande ne porte ce numero.
        ValueError: Si `TELEGRAM_KITCHEN_CHAT_ID` n'est pas configure.
    """
    document = await deps.orders.find_one({"order_number": order_number})
    if document is None:
        raise OrderNotFoundError(
            f"aucune commande {order_number} en base: appelle save_order avant "
            "notify_kitchen"
        )

    chat_id = deps.settings.telegram_kitchen_chat_id
    if not chat_id:
        raise ValueError("TELEGRAM_KITCHEN_CHAT_ID n'est pas configure")

    order = Order(**{k: v for k, v in document.items() if k != "_id"})
    recap = format_kitchen_recap(order)

    await sender.send(chat_id, recap)
    logger.info("kitchen_notified: number=%s", order_number)
    return recap
