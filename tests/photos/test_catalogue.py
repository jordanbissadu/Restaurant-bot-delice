"""Tests du parseur de catalogue photos."""

import pytest

from src.photos.catalogue import CatalogueRow, parse_catalogue

CSV = """plat,fichier,ordre,actif
Poulet Yassa,poulet-yassa.jpg,1,oui
Poulet Yassa,poulet-yassa-2.jpg,2,oui
Eau,eau.jpg,1,non
"Jus locaux (bissap, gingembre)",jus-locaux.jpg,1,oui
"""


@pytest.mark.unit
def test_parses_rows() -> None:
    """Les quatre lignes sont lues avec leurs colonnes."""
    rows = parse_catalogue(CSV)

    assert len(rows) == 4
    assert rows[0] == CatalogueRow(
        dish_name="Poulet Yassa", file_name="poulet-yassa.jpg", position=1, enabled=True
    )


@pytest.mark.unit
def test_actif_non_disables_row() -> None:
    """La colonne actif pilote l'activation."""
    rows = parse_catalogue(CSV)
    eau = [r for r in rows if r.dish_name == "Eau"][0]

    assert eau.enabled is False


@pytest.mark.unit
def test_quoted_name_with_comma_is_preserved() -> None:
    """Un nom contenant une virgule reste entier."""
    rows = parse_catalogue(CSV)

    assert rows[3].dish_name == "Jus locaux (bissap, gingembre)"


@pytest.mark.unit
def test_bom_is_tolerated() -> None:
    """Un CSV exporte avec BOM reste lisible."""
    rows = parse_catalogue("﻿" + CSV)

    assert rows[0].dish_name == "Poulet Yassa"


@pytest.mark.unit
def test_windows_path_quotes_are_stripped() -> None:
    """Les guillemets litteraux du 'Copier en tant que chemin' sont retires."""
    rows = parse_catalogue('plat,fichier,ordre,actif\nPoulet Yassa,"""poulet.jpg""",1,oui\n')

    assert rows[0].file_name == "poulet.jpg"


@pytest.mark.unit
def test_incomplete_rows_are_skipped() -> None:
    """Une ligne sans plat ou sans fichier est ignoree, pas fatale."""
    rows = parse_catalogue(
        "plat,fichier,ordre,actif\n,orphelin.jpg,1,oui\nPoulet Yassa,,1,oui\n"
    )

    assert rows == []


@pytest.mark.unit
def test_missing_position_defaults_to_one() -> None:
    """Un ordre absent ou illisible vaut 1."""
    rows = parse_catalogue("plat,fichier,ordre,actif\nPoulet Yassa,p.jpg,,oui\n")

    assert rows[0].position == 1


@pytest.mark.unit
def test_empty_csv_returns_empty_list() -> None:
    """Un catalogue vide ne fait pas echouer le parseur."""
    assert parse_catalogue("") == []


@pytest.mark.unit
def test_windows_full_path_is_reduced_to_basename() -> None:
    """Un chemin Windows complet colle par erreur se reduit au nom de fichier."""
    rows = parse_catalogue(
        'plat,fichier,ordre,actif\nPoulet Yassa,"""C:\\\\Users\\\\Resto\\\\poulet-yassa.jpg""",1,oui\n'
    )
    assert rows[0].file_name == "poulet-yassa.jpg"


@pytest.mark.unit
def test_posix_path_is_reduced_to_basename() -> None:
    """Un chemin avec des slashes se reduit aussi au nom de fichier."""
    rows = parse_catalogue("plat,fichier,ordre,actif\nPoulet Yassa,photos/poulet-yassa.jpg,1,oui\n")
    assert rows[0].file_name == "poulet-yassa.jpg"
