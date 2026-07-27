"""Tests du cablage de l'agent conversationnel.

Note version : sous pydantic-ai 2.x, `TestModel` rappelle mecaniquement un
outil qui leve `ModelRetry` jusqu'a epuiser les essais, puis leve
`UnexpectedModelBehavior`. Un vrai LLM corrigerait au lieu de boucler ; la
couche Telegram enveloppe `answer()` dans un try/except. Les tests de retry
verifient donc que le message metier atteint le modele (capture_run_messages),
en tolerant cette exception mecanique.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai import capture_run_messages
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models.test import TestModel

from src.agent import BotDeps, answer, build_agent
from src.models import Order, OrderItem

NOW = datetime(2026, 7, 21, 19, 30, tzinfo=timezone.utc)


@dataclass
class _SpySender:
    sent: list[tuple[str, str]]

    async def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def _order() -> Order:
    return Order(
        order_number="LD-20260721-0001",
        chat_id=42,
        customer_name="Kossi",
        service_mode="sur_place",
        items=[OrderItem(name="Poulet Yassa", quantity=1, unit_price=8500, total=8500)],
        total_fcfa=8500,
        created_at=NOW,
    )


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder-1")


@pytest.fixture
def deps() -> BotDeps:
    app = SimpleNamespace(settings=SimpleNamespace(rerank_top_n=10))
    return BotDeps(app=app, chat_id=42, sender=_SpySender(sent=[]))  # type: ignore[arg-type]


@pytest.mark.unit
async def test_agent_exposes_the_three_n8n_tools(deps: BotDeps) -> None:
    """Le modele voit exactement search_menu, save_order et notify_kitchen."""
    agent = build_agent()
    model = TestModel(call_tools=[])

    with agent.override(model=model):
        await answer(agent, deps, "Bonjour", history=[])

    seen = {t.name for t in model.last_model_request_parameters.function_tools}
    assert seen == {"search_menu", "save_order", "notify_kitchen"}


@pytest.mark.unit
def test_build_agent_without_env_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_agent() lit la cle depuis la config, pas la variable d'environnement.

    En production, OPENAI_API_KEY vient du fichier .env (pydantic-settings) et
    n'est pas exportee dans l'environnement du processus. Le provider OpenAI de
    pydantic-ai la cherchant dans os.environ, la cle doit lui etre passee
    explicitement.
    """
    from src.settings import Settings

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # La cle vient de la config (ici en argument), jamais de l'environnement.
    settings = Settings(_env_file=None, openai_api_key="sk-from-config")

    monkeypatch.setattr("src.agent.load_settings", lambda: settings)

    # Ne doit pas lever UserError("Set the OPENAI_API_KEY environment variable").
    build_agent()


@pytest.mark.unit
async def test_answer_returns_text_and_new_messages(deps: BotDeps) -> None:
    """answer() renvoie la reponse et les messages a persister."""
    agent = build_agent()

    with agent.override(model=TestModel(call_tools=[])):
        reply = await answer(agent, deps, "Bonjour", history=[])

    assert isinstance(reply.text, str)
    assert reply.text
    assert len(reply.new_messages) >= 2


@pytest.mark.unit
async def test_search_tool_formats_results(
    deps: BotDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le tool de recherche transmet le contenu des fragments au modele."""
    from src.tools.search_menu import SearchResult

    async def fake_search(app: Any, query: str) -> list[SearchResult]:
        return [
            SearchResult(
                chunk_id="c1",
                document_id="d1",
                content="Poulet Yassa - 8 500 FCFA",
                score=0.9,
                title="Menu",
            )
        ]

    monkeypatch.setattr("src.agent.run_menu_search", fake_search)
    agent = build_agent()

    with agent.override(model=TestModel(call_tools=["search_menu"])):
        with capture_run_messages() as messages:
            await answer(agent, deps, "poulet", history=[])

    rendered = str(messages)
    assert "Poulet Yassa" in rendered


@pytest.mark.unit
async def test_search_tool_reports_no_result(
    deps: BotDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une recherche sans resultat le dit explicitement au modele."""

    async def empty_search(app: Any, query: str) -> list[Any]:
        return []

    monkeypatch.setattr("src.agent.run_menu_search", empty_search)
    agent = build_agent()

    with agent.override(model=TestModel(call_tools=["search_menu"])):
        with capture_run_messages() as messages:
            await answer(agent, deps, "sushi", history=[])

    assert "Aucun resultat" in str(messages)


@pytest.mark.unit
async def test_save_order_validation_error_becomes_retry(
    deps: BotDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une commande invalide renvoie une consigne de correction, pas un crash."""
    from src.tools.save_order import OrderValidationError

    async def failing(*args: Any, **kwargs: Any) -> Any:
        raise OrderValidationError("total_fcfa incoherent: 15000 au lieu de 17000")

    monkeypatch.setattr("src.agent.persist_order", failing)
    agent = build_agent()

    with agent.override(model=TestModel(call_tools=["save_order"])):
        with capture_run_messages() as messages:
            with pytest.raises(UnexpectedModelBehavior):
                await answer(agent, deps, "je valide", history=[])

    assert "incoherent" in str(messages)


@pytest.mark.unit
async def test_notify_kitchen_success_arms_conversation_reset(
    deps: BotDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une notification cuisine reussie arme le drapeau de reset de conversation."""

    async def ok(*args: Any, **kwargs: Any) -> str:
        return "recap"

    monkeypatch.setattr("src.agent.send_to_kitchen", ok)
    agent = build_agent()

    assert deps.order_notified is False
    with agent.override(model=TestModel(call_tools=["notify_kitchen"])):
        await answer(agent, deps, "envoie en cuisine", history=[])

    assert deps.order_notified is True


@pytest.mark.unit
async def test_notify_kitchen_unknown_order_becomes_retry(
    deps: BotDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Notifier une commande inconnue renvoie une consigne au modele."""
    from src.tools.notify_kitchen import OrderNotFoundError

    async def failing(*args: Any, **kwargs: Any) -> Any:
        raise OrderNotFoundError(
            "aucune commande LD-X en base: appelle save_order avant"
        )

    monkeypatch.setattr("src.agent.send_to_kitchen", failing)
    agent = build_agent()

    with agent.override(model=TestModel(call_tools=["notify_kitchen"])):
        with capture_run_messages() as messages:
            with pytest.raises(UnexpectedModelBehavior):
                await answer(agent, deps, "envoie en cuisine", history=[])

    assert "appelle save_order" in str(messages)
