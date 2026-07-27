"""Tests de l'orchestration de synchronisation."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.drive.diff import DeletionGuardError
from src.drive.sync import run_sync
from src.models import DriveFileMeta

T0 = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=1)


class _FakeDocuments:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def find(self, query: dict[str, Any], projection: dict[str, int]):
        return _FakeCursor(self.docs)

    async def count_documents(self, query: dict[str, Any]) -> int:
        return len(self.docs)


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self.docs


class _FakeDeps:
    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self.documents = _FakeDocuments(docs or [])
        self.settings = SimpleNamespace(
            google_drive_folder_id="root",
            drive_sync_max_delete_ratio=0.5,
            drive_photos_catalogue_name="photos",
            photos_enabled=True,
        )


class _FakeClient:
    def __init__(self, remote: list[DriveFileMeta], hashes: dict[str, str]) -> None:
        self.remote = remote
        self.hashes = hashes

    async def list_folder_files(self, folder_id: str) -> list[DriveFileMeta]:
        return self.remote

    async def download(self, meta: DriveFileMeta, target_dir: Path) -> tuple[Path, str]:
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{meta.file_id}.pdf"
        path.write_bytes(b"data")
        return path, self.hashes[meta.file_id]


def _remote(file_id: str, modified: datetime) -> DriveFileMeta:
    return DriveFileMeta(
        file_id=file_id,
        name=f"{file_id}.pdf",
        mime_type="application/pdf",
        modified_time=modified,
    )


def _local(file_id: str, modified: datetime, content_hash: str) -> dict[str, Any]:
    return {
        "drive": {
            "file_id": file_id,
            "modified_time": modified,
            "content_hash": content_hash,
        }
    }


@pytest.fixture
def spies(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {"ingest": [], "delete": [], "touch": []}

    async def fake_ingest(deps, path, meta, content_hash):  # type: ignore[no-untyped-def]
        calls["ingest"].append((meta.file_id, content_hash))
        return 3

    async def fake_delete(deps, file_id):  # type: ignore[no-untyped-def]
        calls["delete"].append(file_id)

    async def fake_touch(deps, file_id, modified_time):  # type: ignore[no-untyped-def]
        calls["touch"].append((file_id, modified_time))

    monkeypatch.setattr("src.drive.sync.ingest_document", fake_ingest)
    monkeypatch.setattr("src.drive.sync.delete_document", fake_delete)
    monkeypatch.setattr("src.drive.sync.touch_document", fake_touch)
    return calls


@pytest.mark.unit
async def test_new_file_is_ingested(spies) -> None:
    """Un nouveau fichier est telecharge puis ingere."""
    deps = _FakeDeps()
    client = _FakeClient([_remote("f1", T0)], {"f1": "h1"})

    report = await run_sync(deps, client)

    assert spies["ingest"] == [("f1", "h1")]
    assert report.ingested == 1


@pytest.mark.unit
async def test_modified_file_with_new_hash_is_reingested(spies) -> None:
    """Un fichier modifie dont le hash change est re-ingere."""
    deps = _FakeDeps([_local("f1", T0, "h1")])
    client = _FakeClient([_remote("f1", T1)], {"f1": "h2"})

    report = await run_sync(deps, client)

    assert spies["ingest"] == [("f1", "h2")]
    assert report.updated == 1
    assert spies["touch"] == []


@pytest.mark.unit
async def test_modified_file_with_identical_hash_is_only_touched(spies) -> None:
    """Un modifiedTime qui bouge sans changement de contenu ne re-ingere rien."""
    deps = _FakeDeps([_local("f1", T0, "h1")])
    client = _FakeClient([_remote("f1", T1)], {"f1": "h1"})

    report = await run_sync(deps, client)

    assert spies["ingest"] == []
    assert spies["touch"] == [("f1", T1)]
    assert report.skipped_identical == 1


@pytest.mark.unit
async def test_deleted_file_is_removed(spies) -> None:
    """Un fichier disparu du Drive est supprime de la base."""
    deps = _FakeDeps([_local("f1", T0, "h1"), _local("f2", T0, "h2")])
    client = _FakeClient([_remote("f1", T0)], {"f1": "h1"})

    report = await run_sync(deps, client)

    assert spies["delete"] == ["f2"]
    assert report.deleted == 1


@pytest.mark.unit
async def test_mass_deletion_is_blocked(spies) -> None:
    """Un listing distant vide face a une base peuplee ne supprime rien."""
    deps = _FakeDeps([_local(f"f{i}", T0, "h") for i in range(4)])
    client = _FakeClient([], {})

    with pytest.raises(DeletionGuardError):
        await run_sync(deps, client)

    assert spies["delete"] == []


@pytest.mark.unit
async def test_failing_file_does_not_stop_the_others(
    spies, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une erreur sur un fichier est loggee, les autres sont traites."""

    async def failing_ingest(deps, path, meta, content_hash):  # type: ignore[no-untyped-def]
        if meta.file_id == "f1":
            raise RuntimeError("PDF corrompu")
        spies["ingest"].append((meta.file_id, content_hash))
        return 2

    monkeypatch.setattr("src.drive.sync.ingest_document", failing_ingest)
    deps = _FakeDeps()
    client = _FakeClient(
        [_remote("f1", T0), _remote("f2", T0)], {"f1": "h1", "f2": "h2"}
    )

    report = await run_sync(deps, client)

    assert spies["ingest"] == [("f2", "h2")]
    assert report.failed == ["f1"]
    assert report.ingested == 1


def _photo_meta(file_id: str, name: str, mime: str) -> DriveFileMeta:
    return DriveFileMeta(
        file_id=file_id, name=name, mime_type=mime, modified_time=T0
    )


@pytest.mark.unit
async def test_run_sync_wires_photo_catalogue_sync(
    spies, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_sync appelle la sync du catalogue avec le routing, et l'image n'est
    jamais ingeree comme document."""
    captured: dict[str, Any] = {"called": 0}

    async def fake_photo_sync(deps, client, routing):  # type: ignore[no-untyped-def]
        captured["called"] += 1
        captured["routing"] = routing
        return SimpleNamespace(synced=1, missing_files=[], removed=0)

    monkeypatch.setattr("src.drive.sync.sync_photo_catalogue", fake_photo_sync)

    deps = _FakeDeps()
    remote = [
        _remote("doc1", T0),
        _photo_meta("img-1", "poulet-yassa.jpg", "image/jpeg"),
        _photo_meta("cat-1", "photos", "text/csv"),
    ]
    client = _FakeClient(remote, {"doc1": "h1"})

    await run_sync(deps, client)

    assert captured["called"] == 1
    routing = captured["routing"]
    assert any(m.file_id == "img-1" for m in routing.images)
    assert routing.catalogue is not None
    assert routing.catalogue.file_id == "cat-1"
    # L'image ne doit jamais partir dans l'ingestion documentaire.
    assert all(file_id != "img-1" for file_id, _ in spies["ingest"])
    assert spies["ingest"] == [("doc1", "h1")]


@pytest.mark.unit
async def test_photos_disabled_skips_catalogue_sync(
    spies, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Avec photos_enabled=False, la sync du catalogue n'est pas appelee."""
    captured: dict[str, int] = {"called": 0}

    async def fake_photo_sync(deps, client, routing):  # type: ignore[no-untyped-def]
        captured["called"] += 1
        return SimpleNamespace(synced=0, missing_files=[], removed=0)

    monkeypatch.setattr("src.drive.sync.sync_photo_catalogue", fake_photo_sync)

    deps = _FakeDeps()
    deps.settings.photos_enabled = False
    client = _FakeClient([_remote("doc1", T0)], {"doc1": "h1"})

    await run_sync(deps, client)

    assert captured["called"] == 0
