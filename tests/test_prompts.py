"""Verifie que le system prompt conserve ses regles metier essentielles."""

import pytest

from src.prompts import SYSTEM_PROMPT


@pytest.mark.unit
def test_prompt_is_substantial() -> None:
    """Le prompt complet est present, pas une version tronquee."""
    assert len(SYSTEM_PROMPT) > 5000


@pytest.mark.unit
@pytest.mark.parametrize(
    "fragment",
    [
        "Le Délice",
        "FCFA",
        "+228 93 43 73 69",
        "save_order",
        "notify_kitchen",
        "sur_place",
        "livraison",
    ],
)
def test_prompt_contains_business_anchors(fragment: str) -> None:
    """Les ancres metier du workflow n8n sont conservees."""
    assert fragment in SYSTEM_PROMPT


@pytest.mark.unit
def test_prompt_keeps_ambiguity_rule() -> None:
    """La regle de clarification des choix ambigus est presente."""
    assert "CLARIFICATION" in SYSTEM_PROMPT or "clarification" in SYSTEM_PROMPT
    assert "ambigu" in SYSTEM_PROMPT.lower()


@pytest.mark.unit
def test_prompt_keeps_tool_ordering_rule() -> None:
    """La regle d'ordre save_order puis notify_kitchen est presente."""
    assert SYSTEM_PROMPT.index("save_order") < SYSTEM_PROMPT.index("notify_kitchen")
