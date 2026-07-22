"""Agent conversationnel du restaurant Le Delice.

Les erreurs metier des outils sont converties en `ModelRetry` : le modele
recoit l'explication et corrige son appel, au lieu de faire echouer le tour de
conversation face au client.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from src.prompts import SYSTEM_PROMPT
from src.settings import load_settings
from src.tools.notify_kitchen import KitchenSender, OrderNotFoundError, send_to_kitchen
from src.tools.save_order import OrderValidationError, persist_order

# Alias : le nom `search_menu` est reserve a l'outil expose au modele, dont le
# nom de fonction determine le nom de l'outil vu par le LLM.
from src.tools.search_menu import search_menu as run_menu_search

if TYPE_CHECKING:
    from src.dependencies import AppDependencies

logger = logging.getLogger(__name__)


@dataclass
class BotDeps:
    """Dependances d'un tour de conversation."""

    app: "AppDependencies"
    chat_id: int
    sender: KitchenSender


class AgentReply(BaseModel):
    """Resultat d'un tour de conversation."""

    text: str = Field(..., description="Reponse a envoyer au client")
    new_messages: list[Any] = Field(
        default_factory=list, description="Messages a persister en memoire"
    )


def build_agent() -> Agent[BotDeps, str]:
    """
    Construit l'agent et enregistre ses trois outils.

    Returns:
        L'agent configure avec le system prompt du restaurant.
    """
    settings = load_settings()
    # La cle est passee explicitement au provider : pydantic-ai lit sinon la
    # variable d'environnement OPENAI_API_KEY, absente ici puisque la config
    # vient du fichier .env (charge par pydantic-settings, non exporte).
    model = OpenAIChatModel(
        settings.llm_model,
        provider=OpenAIProvider(api_key=settings.openai_api_key),
    )
    agent: Agent[BotDeps, str] = Agent(
        model, deps_type=BotDeps, system_prompt=SYSTEM_PROMPT
    )

    @agent.tool
    async def search_menu(ctx: RunContext[BotDeps], query: str) -> str:
        """
        Recherche des plats, boissons et informations dans la base du menu.

        Args:
            query: Termes de recherche, par exemple un nom de plat ou une
                categorie ("poisson", "boissons", "horaires").

        Returns:
            Les fragments de menu pertinents, un par ligne.
        """
        results = await run_menu_search(ctx.deps.app, query)
        if not results:
            return f"Aucun resultat pour '{query}'."
        return "\n\n".join(f"[{r.score:.2f}] {r.content}" for r in results)

    @agent.tool
    async def save_order(
        ctx: RunContext[BotDeps],
        customer_name: str,
        service_mode: str,
        items: list[dict[str, Any]],
        total_fcfa: int,
        customer_phone: str = "",
        delivery_address: str = "",
        delivery_instructions: str = "",
    ) -> str:
        """
        Enregistre la commande validee par le client.

        Args:
            customer_name: Nom complet du client.
            service_mode: `sur_place` ou `livraison`.
            items: Articles, chacun `{"name": str, "quantity": int,
                "unit_price": int, "total": int}` en FCFA entiers.
            total_fcfa: Somme des totaux de ligne, en FCFA entiers.
            customer_phone: Telephone, obligatoire en livraison.
            delivery_address: Adresse, obligatoire en livraison.
            delivery_instructions: Instructions particulieres, optionnelles.

        Returns:
            Le numero de commande, a transmettre ensuite a notify_kitchen.
        """
        try:
            order = await persist_order(
                ctx.deps.app,
                chat_id=ctx.deps.chat_id,
                customer_name=customer_name,
                service_mode=service_mode,
                items=items,
                total_fcfa=total_fcfa,
                customer_phone=customer_phone,
                delivery_address=delivery_address,
                delivery_instructions=delivery_instructions,
            )
        except OrderValidationError as exc:
            raise ModelRetry(str(exc)) from exc

        return order.order_number

    @agent.tool
    async def notify_kitchen(ctx: RunContext[BotDeps], order_number: str) -> str:
        """
        Notifie l'equipe cuisine d'une commande deja enregistree.

        Args:
            order_number: Numero renvoye par save_order.

        Returns:
            Une confirmation d'envoi.
        """
        try:
            await send_to_kitchen(ctx.deps.app, order_number, ctx.deps.sender)
        except OrderNotFoundError as exc:
            raise ModelRetry(str(exc)) from exc

        return f"Cuisine notifiee pour la commande {order_number}."

    return agent


async def answer(
    agent: Agent[BotDeps, str],
    deps: BotDeps,
    text: str,
    history: list[ModelMessage],
) -> AgentReply:
    """
    Traite un message client et produit la reponse.

    Args:
        agent: Agent construit par `build_agent`.
        deps: Dependances du tour de conversation.
        text: Message du client.
        history: Messages precedents, charges depuis la memoire.

    Returns:
        La reponse a envoyer et les messages a persister.
    """
    result = await agent.run(text, deps=deps, message_history=history)
    logger.info("agent_turn_done: chat_id=%d", deps.chat_id)
    return AgentReply(text=result.output, new_messages=list(result.new_messages()))
