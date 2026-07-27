"""Comparaison de l'etat Google Drive avec l'etat MongoDB."""

import logging
from datetime import datetime, timezone

from src.models import DriveFileMeta, SyncDiff

logger = logging.getLogger(__name__)


class DeletionGuardError(Exception):
    """Levee quand un diff propose une suppression de masse suspecte."""


def _as_utc(value: datetime) -> datetime:
    """
    Force une date en UTC timezone-aware.

    L'API Drive fournit des dates aware; MongoDB les relit en naive (pymongo
    renvoie du naive par defaut). On aligne les deux avant toute comparaison,
    sinon Python leve `TypeError: can't compare offset-naive and offset-aware`.

    Args:
        value: Date aware ou naive, supposee exprimee en UTC.

    Returns:
        La meme date en UTC timezone-aware.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compute_diff(remote: list[DriveFileMeta], local: dict[str, datetime]) -> SyncDiff:
    """
    Compare l'etat distant Drive a l'etat local MongoDB.

    Args:
        remote: Fichiers listes sur Drive (corbeille incluse).
        local: `{file_id: modified_time}` des documents deja en base.

    Returns:
        Le diff decrivant les fichiers nouveaux, modifies, supprimes, inchanges.
    """
    diff = SyncDiff()
    seen: set[str] = set()

    for file in remote:
        if file.trashed:
            # Un fichier en corbeille jamais ingere n'a rien a supprimer.
            if file.file_id in local:
                seen.add(file.file_id)
                diff.deleted.append(file.file_id)
            continue

        seen.add(file.file_id)
        known_time = local.get(file.file_id)

        if known_time is None:
            diff.new.append(file)
        elif _as_utc(file.modified_time) > _as_utc(known_time):
            diff.modified.append(file)
        else:
            diff.unchanged.append(file.file_id)

    diff.deleted.extend(file_id for file_id in local if file_id not in seen)

    logger.info(
        "sync_diff: new=%d modified=%d deleted=%d unchanged=%d",
        len(diff.new),
        len(diff.modified),
        len(diff.deleted),
        len(diff.unchanged),
    )
    return diff


def assert_deletion_is_safe(diff: SyncDiff, local_count: int, max_ratio: float) -> None:
    """
    Refuse un diff qui supprimerait une proportion excessive des documents.

    Un dossier temporairement inaccessible (permission retiree, quota) renvoie
    une liste vide : sans ce garde-fou, la base serait videe.

    Args:
        diff: Diff calcule par `compute_diff`.
        local_count: Nombre de documents actuellement en base.
        max_ratio: Proportion maximale de suppressions toleree, dans ]0, 1].

    Raises:
        DeletionGuardError: Si la proportion de suppressions depasse max_ratio.
    """
    if local_count == 0 or not diff.deleted:
        return

    ratio = len(diff.deleted) / local_count
    if ratio > max_ratio:
        message = (
            f"suppression de masse refusee: {len(diff.deleted)}/{local_count} "
            f"documents ({ratio:.0%} > {max_ratio:.0%})"
        )
        logger.error("deletion_guard_triggered: %s", message)
        raise DeletionGuardError(message)
