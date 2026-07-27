"""Tests de la synchronisation du catalogue photos vers MongoDB."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from src.models import DriveFileMeta
from src.photos.sync import DriveRouting, sync_photo_catalogue

NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)

CSV = """plat,fichier,ordre,actif
Poulet Yassa,poulet-yassa.jpg,1,oui
Eau,eau.jpg,1,non
Plat Fantome,absente.jpg,1,oui
"""


class _FakeCollection:
    """Doublure minimale de collection MongoDB async."""

    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False
    ) -> None:
        existing = await self.find_one(query)
        if existing is None:
            if not upsert:
                return
            existing = dict(query)
            self.docs.append(existing)
        existing.update(update.get("$set", {}))

    async def delete_many(self, query: dict[str, Any]) -> SimpleNamespace:
        nor = query.get("$nor")
        if nor is not None:
            keep = [
                doc
                for doc in self.docs
                if any(
                    all(doc.get(k) == v for k, v in clause.items()) for clause in nor
                )
            ]
        else:
            keep = []
        removed = len(self.docs) - len(keep)
        self.docs = keep
        return SimpleNamespace(deleted_count=removed)


class _FakeClient:
    def __init__(self, csv_text: str) -> None:
        self.csv_text = csv_text
        self.exported: list[str] = []

    async def export_csv(self, meta: DriveFileMeta) -> str:
        self.exported.append(meta.file_id)
        return self.csv_text


def _meta(name: str, file_id: str, when: datetime = NOW) -> DriveFileMeta:
    return DriveFileMeta(
        file_id=file_id, name=name, mime_type="image/jpeg", modified_time=when
    )


def _deps(collection: _FakeCollection) -> Any:
    return SimpleNamespace(
        dish_photos=collection,
        settings=SimpleNamespace(drive_photos_catalogue_name="photos"),
    )


def _routing(images: list[DriveFileMeta]) -> DriveRouting:
    catalogue = DriveFileMeta(
        file_id="cat-1", name="photos", mime_type="text/csv", modified_time=NOW
    )
    return DriveRouting(documents=[], images=images, catalogue=catalogue)


@pytest.mark.unit
async def test_catalogue_rows_are_written() -> None:
    """Chaque ligne exploitable devient un document dish_photos."""
    collection = _FakeCollection()
    images = [_meta("poulet-yassa.jpg", "img-1"), _meta("eau.jpg", "img-2")]

    report = await sync_photo_catalogue(
        _deps(collection), _FakeClient(CSV), _routing(images)
    )

    assert report.synced == 2
    yassa = await collection.find_one({"drive_file_id": "img-1"})
    assert yassa["dish_name"] == "Poulet Yassa"
    assert yassa["dish_key"] == "poulet yassa"
    assert yassa["enabled"] is True
    assert yassa["telegram_file_id"] == ""


@pytest.mark.unit
async def test_disabled_row_is_written_but_inactive() -> None:
    """Une ligne actif=non est enregistree desactivee."""
    collection = _FakeCollection()
    images = [_meta("poulet-yassa.jpg", "img-1"), _meta("eau.jpg", "img-2")]

    await sync_photo_catalogue(_deps(collection), _FakeClient(CSV), _routing(images))

    eau = await collection.find_one({"drive_file_id": "img-2"})
    assert eau["enabled"] is False


@pytest.mark.unit
async def test_missing_image_is_reported_not_fatal() -> None:
    """Une ligne pointant vers un fichier absent est signalee, pas fatale."""
    collection = _FakeCollection()
    images = [_meta("poulet-yassa.jpg", "img-1"), _meta("eau.jpg", "img-2")]

    report = await sync_photo_catalogue(
        _deps(collection), _FakeClient(CSV), _routing(images)
    )

    assert report.missing_files == ["absente.jpg"]
    assert report.synced == 2


@pytest.mark.unit
async def test_unchanged_photo_keeps_its_telegram_cache() -> None:
    """Un sync sans changement ne doit pas invalider le cache."""
    collection = _FakeCollection()
    collection.docs.append(
        {
            "dish_key": "poulet yassa",
            "drive_file_id": "img-1",
            "drive_modified_time": NOW,
            "telegram_file_id": "cache-abc",
        }
    )
    images = [_meta("poulet-yassa.jpg", "img-1"), _meta("eau.jpg", "img-2")]

    await sync_photo_catalogue(_deps(collection), _FakeClient(CSV), _routing(images))

    yassa = await collection.find_one({"drive_file_id": "img-1"})
    assert yassa["telegram_file_id"] == "cache-abc"


@pytest.mark.unit
async def test_modified_photo_clears_its_telegram_cache() -> None:
    """Une image remplacee dans Drive invalide son cache Telegram."""
    collection = _FakeCollection()
    collection.docs.append(
        {
            "dish_key": "poulet yassa",
            "drive_file_id": "img-1",
            "drive_modified_time": NOW,
            "telegram_file_id": "cache-abc",
        }
    )
    later = NOW + timedelta(hours=1)
    images = [_meta("poulet-yassa.jpg", "img-1", later), _meta("eau.jpg", "img-2")]

    await sync_photo_catalogue(_deps(collection), _FakeClient(CSV), _routing(images))

    yassa = await collection.find_one({"drive_file_id": "img-1"})
    assert yassa["telegram_file_id"] == ""


@pytest.mark.unit
async def test_absent_catalogue_is_not_fatal() -> None:
    """Sans catalogue dans Drive, le sync photo ne fait rien."""
    collection = _FakeCollection()
    routing = DriveRouting(documents=[], images=[], catalogue=None)

    report = await sync_photo_catalogue(_deps(collection), _FakeClient(CSV), routing)

    assert report.synced == 0
    assert collection.docs == []


@pytest.mark.unit
async def test_stale_row_is_removed() -> None:
    """Un document dish_photos dont la ligne a disparu du catalogue est supprime."""
    collection = _FakeCollection()
    collection.docs.append(
        {
            "dish_key": "plat oublie",
            "drive_file_id": "img-stale",
            "drive_modified_time": NOW,
            "telegram_file_id": "cache-stale",
        }
    )
    images = [_meta("poulet-yassa.jpg", "img-1"), _meta("eau.jpg", "img-2")]

    report = await sync_photo_catalogue(
        _deps(collection), _FakeClient(CSV), _routing(images)
    )

    stale = await collection.find_one({"drive_file_id": "img-stale"})
    assert stale is None
    assert report.removed == 1


@pytest.mark.unit
async def test_shared_image_across_dishes_removes_only_the_dropped_row() -> None:
    """Deux plats partageant le meme fichier ne doivent pas se supprimer l'un l'autre."""
    collection = _FakeCollection()
    collection.docs.append(
        {
            "dish_key": "poulet yassa",
            "drive_file_id": "img-shared",
            "drive_modified_time": NOW,
            "telegram_file_id": "cache-yassa",
        }
    )
    collection.docs.append(
        {
            "dish_key": "thieboudienne",
            "drive_file_id": "img-shared",
            "drive_modified_time": NOW,
            "telegram_file_id": "cache-thiebou",
        }
    )
    csv_text = """plat,fichier,ordre,actif
Poulet Yassa,poulet-yassa.jpg,1,oui
"""
    images = [_meta("poulet-yassa.jpg", "img-shared")]

    report = await sync_photo_catalogue(
        _deps(collection), _FakeClient(csv_text), _routing(images)
    )

    kept = await collection.find_one(
        {"dish_key": "poulet yassa", "drive_file_id": "img-shared"}
    )
    dropped = await collection.find_one(
        {"dish_key": "thieboudienne", "drive_file_id": "img-shared"}
    )
    assert kept is not None
    assert kept["telegram_file_id"] == "cache-yassa"
    assert dropped is None
    assert report.removed == 1
