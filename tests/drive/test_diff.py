"""Tests du calcul de diff Drive <-> MongoDB et du garde-fou de suppression."""

from datetime import datetime, timedelta, timezone

import pytest

from src.drive.diff import DeletionGuardError, assert_deletion_is_safe, compute_diff
from src.models import DriveFileMeta

T0 = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=1)


def _remote(file_id: str, modified: datetime, trashed: bool = False) -> DriveFileMeta:
    return DriveFileMeta(
        file_id=file_id,
        name=f"{file_id}.pdf",
        mime_type="application/pdf",
        modified_time=modified,
        trashed=trashed,
    )


@pytest.mark.unit
def test_new_file_is_detected() -> None:
    """Un fichier distant absent en local est marque nouveau."""
    diff = compute_diff([_remote("f1", T0)], local={})

    assert [f.file_id for f in diff.new] == ["f1"]
    assert diff.modified == []
    assert diff.deleted == []


@pytest.mark.unit
def test_modified_file_is_detected() -> None:
    """Un modifiedTime distant plus recent marque le fichier comme modifie."""
    diff = compute_diff([_remote("f1", T1)], local={"f1": T0})

    assert [f.file_id for f in diff.modified] == ["f1"]
    assert diff.new == []


@pytest.mark.unit
def test_unchanged_file_is_detected() -> None:
    """Un modifiedTime identique laisse le fichier inchange."""
    diff = compute_diff([_remote("f1", T0)], local={"f1": T0})

    assert diff.unchanged == ["f1"]
    assert diff.modified == []


@pytest.mark.unit
def test_older_remote_time_is_not_a_modification() -> None:
    """Un modifiedTime distant plus ancien n'est pas une modification."""
    diff = compute_diff([_remote("f1", T0)], local={"f1": T1})

    assert diff.modified == []
    assert diff.unchanged == ["f1"]


@pytest.mark.unit
def test_naive_stored_time_does_not_crash() -> None:
    """Une date locale naive (relue de Mongo) se compare sans TypeError.

    Reproduit le crash de production : Drive fournit une date aware, MongoDB la
    relit en naive; sans alignement, la comparaison levait
    `TypeError: can't compare offset-naive and offset-aware datetimes`.
    """
    naive_t0 = datetime(2026, 7, 21, 10, 0)  # naive, meme instant que T0

    unchanged = compute_diff([_remote("f1", T0)], local={"f1": naive_t0})
    assert unchanged.unchanged == ["f1"]
    assert unchanged.modified == []

    modified = compute_diff([_remote("f1", T1)], local={"f1": naive_t0})
    assert [f.file_id for f in modified.modified] == ["f1"]


@pytest.mark.unit
def test_deleted_file_is_detected() -> None:
    """Un fichier local absent du distant est marque supprime."""
    diff = compute_diff([_remote("f1", T0)], local={"f1": T0, "f2": T0})

    assert diff.deleted == ["f2"]


@pytest.mark.unit
def test_trashed_file_is_treated_as_deleted() -> None:
    """Un fichier distant dans la corbeille est traite comme supprime."""
    diff = compute_diff([_remote("f1", T0, trashed=True)], local={"f1": T0})

    assert diff.deleted == ["f1"]
    assert diff.unchanged == []


@pytest.mark.unit
def test_trashed_file_absent_locally_is_ignored() -> None:
    """Un fichier distant en corbeille et jamais ingere n'est pas traite."""
    diff = compute_diff([_remote("f1", T0, trashed=True)], local={})

    assert diff.new == []
    assert diff.deleted == []


@pytest.mark.unit
def test_guard_allows_normal_deletion() -> None:
    """Supprimer 1 document sur 10 passe le garde-fou."""
    diff = compute_diff([], local={})
    diff.deleted = ["f1"]

    assert_deletion_is_safe(diff, local_count=10, max_ratio=0.5)


@pytest.mark.unit
def test_guard_blocks_mass_deletion() -> None:
    """Supprimer plus de max_ratio des documents leve DeletionGuardError."""
    diff = compute_diff([], local={})
    diff.deleted = ["f1", "f2", "f3", "f4", "f5", "f6"]

    with pytest.raises(DeletionGuardError, match="6/10"):
        assert_deletion_is_safe(diff, local_count=10, max_ratio=0.5)


@pytest.mark.unit
def test_guard_blocks_empty_remote_listing() -> None:
    """Un listing distant vide face a une base peuplee est bloque."""
    diff = compute_diff([], local={f"f{i}": T0 for i in range(4)})

    with pytest.raises(DeletionGuardError):
        assert_deletion_is_safe(diff, local_count=4, max_ratio=0.5)


@pytest.mark.unit
def test_guard_noop_on_empty_database() -> None:
    """Aucune suppression a valider quand la base est vide."""
    diff = compute_diff([], local={})

    assert_deletion_is_safe(diff, local_count=0, max_ratio=0.5)
