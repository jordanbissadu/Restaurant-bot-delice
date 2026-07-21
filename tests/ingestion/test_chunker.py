"""Tests du decoupage Docling."""

from pathlib import Path

import pytest

from src.ingestion.chunker import TextChunk, chunk_file

MENU_MD = """# Menu Le Delice

## Grillades

Poisson Braise (dorade ou capitaine grille, attieke, legumes) - 11 000 FCFA

Poisson Braise Facon Togolaise (marinade piment, attieke, crudites) - 10 000 FCFA

## Volailles

Poulet Yassa - 8 500 FCFA

Poulet Akoume + Sauce Graine - 7 000 FCFA
"""


@pytest.fixture
def menu_path(tmp_path: Path) -> Path:
    path = tmp_path / "menu.md"
    path.write_text(MENU_MD, encoding="utf-8")
    return path


@pytest.mark.integration
def test_chunk_file_returns_chunks(menu_path: Path) -> None:
    """Le decoupage produit au moins un chunk non vide."""
    chunks = chunk_file(menu_path, max_tokens=512)

    assert len(chunks) > 0
    assert all(isinstance(c, TextChunk) for c in chunks)
    assert all(c.content.strip() for c in chunks)


@pytest.mark.integration
def test_chunk_indices_are_sequential(menu_path: Path) -> None:
    """Les chunks sont indexes de 0 a n-1 sans trou."""
    chunks = chunk_file(menu_path, max_tokens=512)

    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


@pytest.mark.integration
def test_prices_stay_with_dish_names(menu_path: Path) -> None:
    """Chaque prix reste dans le meme chunk que le nom du plat associe."""
    chunks = chunk_file(menu_path, max_tokens=512)
    joined = {c.content for c in chunks}

    yassa_chunks = [c for c in joined if "Poulet Yassa" in c]
    assert yassa_chunks, "le plat Poulet Yassa doit apparaitre dans un chunk"
    assert any("8 500" in c for c in yassa_chunks)
