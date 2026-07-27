"""Orchestration d'un cycle de synchronisation Google Drive -> MongoDB."""

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from src.drive.client import DriveClient
from src.drive.diff import assert_deletion_is_safe, compute_diff
from src.ingestion.ingest import delete_document, ingest_document, touch_document
from src.models import DriveFileMeta
from src.photos.sync import route_drive_files, sync_photo_catalogue

if TYPE_CHECKING:
    from src.dependencies import AppDependencies

logger = logging.getLogger(__name__)


class SyncReport(BaseModel):
    """Bilan chiffre d'un cycle de synchronisation."""

    ingested: int = Field(default=0, description="Nouveaux documents ingeres")
    updated: int = Field(
        default=0, description="Documents re-ingeres apres modification"
    )
    skipped_identical: int = Field(
        default=0, description="Documents dont seul l'horodatage a change"
    )
    deleted: int = Field(default=0, description="Documents supprimes")
    unchanged: int = Field(default=0, description="Documents non touches")
    failed: list[str] = Field(
        default_factory=list, description="file_id ayant echoue, a retenter"
    )


async def run_sync(deps: "AppDependencies", client: DriveClient) -> SyncReport:
    """
    Execute un cycle complet de synchronisation.

    Les erreurs par fichier sont capturees et loggees : le fichier fautif est
    reporte dans `SyncReport.failed` et sera re-tente au cycle suivant, puisque
    ses horodatages en base restent inchanges.

    Args:
        deps: Dependances applicatives.
        client: Client Google Drive authentifie.

    Returns:
        Le bilan du cycle.

    Raises:
        DeletionGuardError: Si le diff propose une suppression de masse.
    """
    all_remote = await client.list_folder_files(deps.settings.google_drive_folder_id)
    routing = route_drive_files(
        all_remote, deps.settings.drive_photos_catalogue_name
    )
    remote = routing.documents
    local_docs = await deps.documents.find(
        {}, {"drive.file_id": 1, "drive.modified_time": 1, "drive.content_hash": 1}
    ).to_list(None)

    local_times = {
        d["drive"]["file_id"]: d["drive"]["modified_time"] for d in local_docs
    }
    local_hashes = {
        d["drive"]["file_id"]: d["drive"]["content_hash"] for d in local_docs
    }

    diff = compute_diff(remote, local_times)
    assert_deletion_is_safe(
        diff, len(local_times), deps.settings.drive_sync_max_delete_ratio
    )

    report = SyncReport(unchanged=len(diff.unchanged))

    with tempfile.TemporaryDirectory(prefix="drive-sync-") as tmp:
        workdir = Path(tmp)

        for file in diff.new:
            if await _ingest_one(deps, client, file, workdir, report):
                report.ingested += 1

        for file in diff.modified:
            outcome = await _sync_modified(
                deps, client, file, workdir, local_hashes.get(file.file_id, ""), report
            )
            if outcome == "updated":
                report.updated += 1
            elif outcome == "identical":
                report.skipped_identical += 1

    for file_id in diff.deleted:
        try:
            await delete_document(deps, file_id)
            report.deleted += 1
        except Exception:
            logger.exception("delete_failed: file_id=%s", file_id)
            report.failed.append(file_id)

    # La sync du catalogue photos est isolee : un echec ici ne doit jamais
    # faire echouer ni annuler le sync des documents deja effectue.
    if deps.settings.photos_enabled:
        try:
            photo_report = await sync_photo_catalogue(deps, client, routing)
            logger.info(
                "photo_sync_done: synced=%d missing=%d removed=%d",
                photo_report.synced,
                len(photo_report.missing_files),
                photo_report.removed,
            )
        except Exception:
            logger.exception("photo_sync_failed")

    logger.info(
        "sync_done: ingested=%d updated=%d identical=%d deleted=%d unchanged=%d failed=%d",
        report.ingested,
        report.updated,
        report.skipped_identical,
        report.deleted,
        report.unchanged,
        len(report.failed),
    )
    return report


async def _ingest_one(
    deps: "AppDependencies",
    client: DriveClient,
    file: DriveFileMeta,
    workdir: Path,
    report: SyncReport,
) -> bool:
    """
    Telecharge et ingere un fichier, en capturant les erreurs.

    Returns:
        True si l'ingestion a reussi.
    """
    try:
        path, digest = await client.download(file, workdir)
        await ingest_document(deps, path, file, digest)
        return True
    except Exception:
        logger.exception("ingest_failed: file=%s id=%s", file.name, file.file_id)
        report.failed.append(file.file_id)
        return False


async def _sync_modified(
    deps: "AppDependencies",
    client: DriveClient,
    file: DriveFileMeta,
    workdir: Path,
    known_hash: str,
    report: SyncReport,
) -> str:
    """
    Traite un fichier dont le modifiedTime a change.

    Le telechargement est gratuit; le hash decide s'il faut payer le decoupage
    et la vectorisation.

    Returns:
        `"updated"`, `"identical"` ou `"failed"`.
    """
    try:
        path, digest = await client.download(file, workdir)
    except Exception:
        logger.exception("download_failed: file=%s id=%s", file.name, file.file_id)
        report.failed.append(file.file_id)
        return "failed"

    if digest == known_hash:
        await touch_document(deps, file.file_id, file.modified_time)
        return "identical"

    try:
        await ingest_document(deps, path, file, digest)
        return "updated"
    except Exception:
        logger.exception("reingest_failed: file=%s id=%s", file.name, file.file_id)
        report.failed.append(file.file_id)
        return "failed"
