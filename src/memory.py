"""Memoire conversationnelle persistee dans MongoDB, indexee par chat_id.

Le format natif de Pydantic AI est conserve : il preserve les appels d'outils,
donc l'agent sait qu'il a deja enregistre une commande, meme apres un
redemarrage du bot.
"""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pydantic_core import to_jsonable_python

if TYPE_CHECKING:
    from src.dependencies import AppDependencies

logger = logging.getLogger(__name__)


def _messages(deps: "AppDependencies") -> Any:
    """Collection des messages individuels."""
    return deps.db[deps.settings.mongodb_collection_messages]


def _conversations(deps: "AppDependencies") -> Any:
    """Collection des metadonnees de conversation."""
    return deps.db[deps.settings.mongodb_collection_conversations]


async def load_history(deps: "AppDependencies", chat_id: int) -> list[ModelMessage]:
    """
    Recharge les derniers messages d'une conversation.

    Args:
        deps: Dependances applicatives.
        chat_id: Identifiant de conversation Telegram.

    Returns:
        Les `settings.memory_max_messages` derniers messages, du plus ancien au
        plus recent. Liste vide si la conversation est inconnue.
    """
    docs = await (
        _messages(deps)
        .find({"chat_id": chat_id})
        .sort("seq", -1)
        .limit(deps.settings.memory_max_messages)
        .to_list(length=deps.settings.memory_max_messages)
    )

    if not docs:
        return []

    docs.reverse()
    payloads = [doc["payload"] for doc in docs]
    return ModelMessagesTypeAdapter.validate_python(payloads)


async def save_turn(
    deps: "AppDependencies", chat_id: int, messages: list[ModelMessage]
) -> None:
    """
    Persiste les messages produits par un tour de conversation.

    Args:
        deps: Dependances applicatives.
        chat_id: Identifiant de conversation Telegram.
        messages: Messages nouvellement produits (`result.new_messages()`).
    """
    if not messages:
        return

    start = await _messages(deps).count_documents({"chat_id": chat_id})
    now = datetime.now(timezone.utc)

    await _messages(deps).insert_many(
        [
            {
                "chat_id": chat_id,
                "seq": start + offset,
                "payload": to_jsonable_python(message),
                "created_at": now,
            }
            for offset, message in enumerate(messages)
        ]
    )

    await _conversations(deps).update_one(
        {"chat_id": chat_id},
        {"$set": {"chat_id": chat_id, "last_active_at": now}},
        upsert=True,
    )

    logger.info("memory_saved: chat_id=%d messages=%d", chat_id, len(messages))


async def clear_history(deps: "AppDependencies", chat_id: int) -> None:
    """
    Efface l'integralite de l'historique d'une conversation.

    Args:
        deps: Dependances applicatives.
        chat_id: Identifiant de conversation Telegram.
    """
    await _messages(deps).delete_many({"chat_id": chat_id})
    await _conversations(deps).delete_many({"chat_id": chat_id})
    logger.info("memory_cleared: chat_id=%d", chat_id)
