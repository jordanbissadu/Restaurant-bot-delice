"""Routage des fichiers Drive et synchronisation du catalogue photos.

Sans ce routage, `run_sync` enverrait les images au decoupage et a la
vectorisation : des chunks binaires dans la base du menu et une facture
d'embeddings pour rien.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from src.models import DriveFileMeta

logger = logging.getLogger(__name__)

IMAGE_MIME_PREFIX = "image/"
CSV_MIMES = {"text/csv", "application/csv"}
SHEET_MIME = "application/vnd.google-apps.spreadsheet"


@dataclass(frozen=True)
class DriveRouting:
    """Fichiers Drive repartis par destination."""

    documents: list[DriveFileMeta] = field(default_factory=list)
    images: list[DriveFileMeta] = field(default_factory=list)
    catalogue: DriveFileMeta | None = None


def route_drive_files(
    files: Sequence[DriveFileMeta], catalogue_name: str
) -> DriveRouting:
    """
    Repartit les fichiers Drive entre documents, images et catalogue.

    Args:
        files: Fichiers remontes par `DriveClient.list_folder_files`.
        catalogue_name: Nom du catalogue photos, sans extension.

    Returns:
        Le routage. Le catalogue vaut None s'il est absent du dossier.
    """
    documents: list[DriveFileMeta] = []
    images: list[DriveFileMeta] = []
    catalogue: DriveFileMeta | None = None

    wanted = catalogue_name.strip().lower()

    for meta in files:
        stem = Path(meta.name).stem.strip().lower()
        is_catalogue_candidate = meta.mime_type in CSV_MIMES or meta.mime_type == SHEET_MIME

        if catalogue is None and is_catalogue_candidate and stem == wanted:
            catalogue = meta
            continue

        if meta.mime_type.startswith(IMAGE_MIME_PREFIX):
            images.append(meta)
            continue

        documents.append(meta)

    logger.info(
        "drive_routed: documents=%d images=%d catalogue=%s",
        len(documents),
        len(images),
        catalogue.name if catalogue else "absent",
    )
    return DriveRouting(documents=documents, images=images, catalogue=catalogue)
