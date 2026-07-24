"""Lecture du catalogue photos, un CSV maintenu par le restaurant dans Drive.

Le parseur est tolerant : une ligne incomplete est ignoree plutot que fatale,
pour qu'une faute de saisie du restaurant ne prive pas le bot de tout son
catalogue.
"""

import csv
import io
import logging

from dataclasses import dataclass
from pathlib import PureWindowsPath

logger = logging.getLogger(__name__)

TRUE_VALUES = {"oui", "yes", "true", "1", "o", "y"}


@dataclass(frozen=True)
class CatalogueRow:
    """Une ligne du catalogue : un plat, une photo."""

    dish_name: str
    file_name: str
    position: int
    enabled: bool


def _clean(value: str | None) -> str:
    """Retire espaces et guillemets litteraux autour d'une valeur."""
    return (value or "").strip().strip('"').strip()


def parse_catalogue(csv_text: str) -> list[CatalogueRow]:
    """
    Parse le contenu CSV du catalogue photos.

    Colonnes attendues : `plat`, `fichier`, `ordre`, `actif`.

    Args:
        csv_text: Contenu textuel du CSV, BOM tolere.

    Returns:
        Les lignes exploitables. Les lignes sans plat ou sans fichier sont
        ignorees et loggees.
    """
    text = csv_text.lstrip("﻿")
    if not text.strip():
        return []

    rows: list[CatalogueRow] = []
    for record in csv.DictReader(io.StringIO(text)):
        dish_name = _clean(record.get("plat"))
        file_name = _clean(record.get("fichier"))
        file_name = PureWindowsPath(file_name).name
        if not dish_name or not file_name:
            logger.warning(
                "catalogue_row_skipped: plat=%r fichier=%r", dish_name, file_name
            )
            continue

        try:
            position = int(_clean(record.get("ordre")) or 1)
        except ValueError:
            position = 1

        enabled = _clean(record.get("actif")).lower() in TRUE_VALUES

        rows.append(
            CatalogueRow(
                dish_name=dish_name,
                file_name=file_name,
                position=max(position, 1),
                enabled=enabled,
            )
        )

    logger.info("catalogue_parsed: rows=%d", len(rows))
    return rows
