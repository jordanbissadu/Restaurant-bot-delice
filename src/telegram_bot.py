"""Bot Telegram en long-polling.

Le long-polling evite toute URL publique : le bot interroge les serveurs
Telegram depuis la machine ou il tourne.
"""

import asyncio
import logging
from typing import Any

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from src.agent import BotDeps, answer, build_agent
from src.dependencies import AppDependencies
from src.memory import clear_history, load_history, save_turn

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger(__name__)

RESET_COMMANDS = {"/reset", "/start"}
FALLBACK_MESSAGE = (
    "Je rencontre un souci technique. Merci de reessayer dans un instant, "
    "ou d'appeler le restaurant au +228 93 43 73 69."
)
RESET_MESSAGE = "Conversation reinitialisee. Comment puis-je vous aider ?"


class TelegramSender:
    """Adaptateur d'envoi Telegram, conforme au protocole `KitchenSender`."""

    def __init__(self, bot: Any) -> None:
        """
        Args:
            bot: Instance `telegram.Bot` ou equivalent.
        """
        self.bot = bot

    async def send(self, chat_id: str, text: str) -> None:
        """
        Envoie un message texte.

        Args:
            chat_id: Identifiant de la conversation destinataire.
            text: Contenu du message.
        """
        await self.bot.send_message(chat_id=chat_id, text=text)


async def handle_message(
    deps: Any, agent: Any, chat_id: int, text: str, sender: Any
) -> str:
    """
    Traite un message entrant et renvoie la reponse a afficher au client.

    Args:
        deps: Dependances applicatives.
        agent: Agent conversationnel.
        chat_id: Identifiant de conversation Telegram.
        text: Texte du message recu.
        sender: Canal d'envoi pour la notification cuisine.

    Returns:
        La reponse a envoyer, ou une chaine vide si le message est ignore.
    """
    message = (text or "").strip()
    if not message:
        return ""

    if message.lower() in RESET_COMMANDS:
        await clear_history(deps, chat_id)
        return RESET_MESSAGE

    history = await load_history(deps, chat_id)
    bot_deps = BotDeps(app=deps, chat_id=chat_id, sender=sender)

    try:
        reply = await answer(agent, bot_deps, message, history)
    except Exception:
        logger.exception("agent_turn_failed: chat_id=%d", chat_id)
        return FALLBACK_MESSAGE

    await save_turn(deps, chat_id, reply.new_messages)
    return reply.text


async def main() -> None:
    """Demarre le bot et maintient la boucle de long-polling."""
    async with AppDependencies() as deps:
        agent = build_agent()
        application = (
            Application.builder().token(deps.settings.telegram_bot_token).build()
        )
        sender = TelegramSender(application.bot)

        async def on_message(
            update: Update, context: ContextTypes.DEFAULT_TYPE
        ) -> None:
            """Relaie un message Telegram vers l'agent."""
            if update.message is None or update.message.chat is None:
                return

            chat_id = update.message.chat.id
            reply = await handle_message(
                deps, agent, chat_id, update.message.text or "", sender
            )
            if reply:
                await update.message.reply_text(reply)

        application.add_handler(MessageHandler(filters.TEXT, on_message))

        logger.info("telegram_bot_started: long polling")
        async with application:
            await application.start()
            await application.updater.start_polling()
            await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
