"""Routage des fichiers Drive et synchronisation du catalogue photos.

Sans ce routage, `run_sync` enverrait les images au decoupage et a la
vectorisation : des chunks binaires dans la base du menu et une facture
d'embeddings pour rien.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from src.models import DriveFileMeta
from src.photos.catalogue import parse_catalogue
from src.photos.matcher import normalize

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


@dataclass
class PhotoSyncReport:
    """Bilan d'un cycle de synchronisation du catalogue photos."""

    synced: int = 0
    missing_files: list[str] = field(default_factory=list)
    removed: int = 0


async def sync_photo_catalogue(
    deps: Any, client: Any, routing: DriveRouting
) -> PhotoSyncReport:
    """
    Met a jour la collection `dish_photos` a partir du catalogue Drive.

    Le cache Telegram d'une photo est invalide si et seulement si son
    `modifiedTime` Drive a change : l'image a ete remplacee.

    Args:
        deps: Dependances applicatives.
        client: Client Drive authentifie.
        routing: Fichiers Drive deja repartis par `route_drive_files`.

    Returns:
        Le bilan du cycle.
    """
    report = PhotoSyncReport()
    if routing.catalogue is None:
        logger.warning("photo_catalogue_absent")
        return report

    csv_text = await client.export_csv(routing.catalogue)
    rows = parse_catalogue(csv_text)

    images_by_name = {meta.name.strip().lower(): meta for meta in routing.images}
    now = datetime.now(timezone.utc)
    kept_keys: list[dict[str, str]] = []

    for row in rows:
        image = images_by_name.get(row.file_name.strip().lower())
        if image is None:
            report.missing_files.append(row.file_name)
            logger.warning(
                "photo_file_missing: plat=%s fichier=%s", row.dish_name, row.file_name
            )
            continue

        dish_key = normalize(row.dish_name)
        query = {"dish_key": dish_key, "drive_file_id": image.file_id}
        existing = await deps.dish_photos.find_one(query)

        changes: dict[str, Any] = {
            "dish_name": row.dish_name,
            "dish_key": dish_key,
            "drive_file_id": image.file_id,
            "file_name": image.name,
            "drive_modified_time": image.modified_time,
            "position": row.position,
            "enabled": row.enabled,
            "updated_at": now,
        }

        if existing is None:
            changes["telegram_file_id"] = ""
            changes["content_hash"] = ""
        elif existing.get("drive_modified_time") != image.modified_time:
            changes["telegram_file_id"] = ""
            changes["content_hash"] = ""
            logger.info("photo_cache_invalidated: fichier=%s", image.name)

        await deps.dish_photos.update_one(query, {"$set": changes}, upsert=True)
        kept_keys.append({"dish_key": dish_key, "drive_file_id": image.file_id})
        report.synced += 1

    if kept_keys:
        deleted = await deps.dish_photos.delete_many(
            {
                "$nor": [
                    {"dish_key": k["dish_key"], "drive_file_id": k["drive_file_id"]}
                    for k in kept_keys
                ]
            }
        )
    else:
        deleted = await deps.dish_photos.delete_many({})
    report.removed = deleted.deleted_count

    logger.info(
        "photo_catalogue_synced: synced=%d missing=%d removed=%d",
        report.synced,
        len(report.missing_files),
        report.removed,
    )
    return report
