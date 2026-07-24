"""Tests du matcher de plats. Aucune dependance externe."""

import pytest

from src.photos.matcher import DishEntry, find_dishes, normalize

CATALOGUE = [
    DishEntry(name="Poisson Braisé", key=normalize("Poisson Braisé")),
    DishEntry(
        name="Poisson Braisé Façon Togolaise",
        key=normalize("Poisson Braisé Façon Togolaise"),
    ),
    DishEntry(name="Poulet Yassa", key=normalize("Poulet Yassa")),
    DishEntry(name="Thiéboudienne", key=normalize("Thiéboudienne")),
    DishEntry(name="Eau", key=normalize("Eau")),
    DishEntry(name="Tchakpalo", key=normalize("Tchakpalo")),
    DishEntry(name="Tchakpalo + Akpan", key=normalize("Tchakpalo + Akpan")),
    DishEntry(
        name="Fufu + Sauce Arachide + Agneau",
        key=normalize("Fufu + Sauce Arachide + Agneau"),
    ),
    DishEntry(
        name="Kékélen (Boules de Haricot)", key=normalize("Kékélen (Boules de Haricot)")
    ),
]


@pytest.mark.unit
def test_normalize_strips_accents_and_case() -> None:
    """La normalisation retire accents et casse."""
    assert normalize("Poisson Braisé Façon Togolaise") == "poisson braise facon togolaise"
    assert normalize("Steak de Bœuf Grillé") == "steak de boeuf grille"


@pytest.mark.unit
def test_agneau_does_not_trigger_eau() -> None:
    """'agneau' contient 'eau' mais ne doit pas declencher la photo Eau."""
    text = "Nous avons le Fufu + Sauce Arachide + Agneau à 8 000 FCFA."

    assert find_dishes(text, CATALOGUE) == ["Fufu + Sauce Arachide + Agneau"]


@pytest.mark.unit
def test_longest_name_wins_over_substring() -> None:
    """Le nom le plus long consomme le texte : une seule photo, pas deux."""
    text = "Le Poisson Braisé Façon Togolaise est à 10 000 FCFA."

    assert find_dishes(text, CATALOGUE) == ["Poisson Braisé Façon Togolaise"]


@pytest.mark.unit
def test_both_poisson_variants_are_found_when_both_cited() -> None:
    """Les deux versions citees rendent bien deux plats."""
    text = (
        "Poisson Braisé Façon Togolaise – 10 000 FCFA. "
        "Poisson Braisé – 11 000 FCFA."
    )

    assert find_dishes(text, CATALOGUE) == [
        "Poisson Braisé Façon Togolaise",
        "Poisson Braisé",
    ]


@pytest.mark.unit
def test_tchakpalo_akpan_does_not_trigger_tchakpalo() -> None:
    """La meme regle vaut pour Tchakpalo."""
    text = "Le Tchakpalo + Akpan est disponible."

    assert find_dishes(text, CATALOGUE) == ["Tchakpalo + Akpan"]


@pytest.mark.unit
def test_name_ending_with_parenthesis_is_found() -> None:
    """Un nom finissant par une parenthese est detecte."""
    text = "Nous proposons le Kékélen (Boules de Haricot) en entrée."

    assert find_dishes(text, CATALOGUE) == ["Kékélen (Boules de Haricot)"]


@pytest.mark.unit
def test_case_and_accents_are_ignored() -> None:
    """Majuscules et accents absents ne bloquent pas la detection."""
    assert find_dishes("POULET YASSA", CATALOGUE) == ["Poulet Yassa"]
    assert find_dishes("thieboudienne", CATALOGUE) == ["Thiéboudienne"]


@pytest.mark.unit
def test_dishes_are_returned_in_text_order() -> None:
    """L'ordre suit le texte, pas l'ordre du catalogue."""
    text = "Nous avons Thiéboudienne, puis Poulet Yassa, puis Tchakpalo."

    assert find_dishes(text, CATALOGUE) == [
        "Thiéboudienne",
        "Poulet Yassa",
        "Tchakpalo",
    ]


@pytest.mark.unit
def test_no_dish_returns_empty() -> None:
    """Un texte sans plat ne declenche rien."""
    assert find_dishes("Bonjour, comment allez-vous ?", CATALOGUE) == []


@pytest.mark.unit
def test_above_threshold_returns_empty() -> None:
    """Au-dela du seuil, on considere que le bot deroule la carte."""
    text = (
        "Poulet Yassa, Thiéboudienne, Poisson Braisé, Tchakpalo, "
        "Kékélen (Boules de Haricot)"
    )

    assert find_dishes(text, CATALOGUE, max_dishes=4) == []


@pytest.mark.unit
def test_exactly_at_threshold_is_kept() -> None:
    """Le seuil est inclusif."""
    text = "Poulet Yassa, Thiéboudienne, Poisson Braisé, Tchakpalo."

    assert len(find_dishes(text, CATALOGUE, max_dishes=4)) == 4


@pytest.mark.unit
def test_same_dish_cited_twice_is_returned_once() -> None:
    """Une repetition ne double pas la photo."""
    text = "Le Poulet Yassa est excellent. Je confirme le Poulet Yassa."

    assert find_dishes(text, CATALOGUE) == ["Poulet Yassa"]
