"""Tests de l'envoi des photos et du cache file_id."""

from types import SimpleNamespace
from typing import Any, Sequence

import pytest

from src.photos.sender import maybe_send_photos


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    async def to_list(self, _length: Any) -> list[dict[str, Any]]:
        return self._docs


class _FakeCollection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs
        self.updates: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def find(self, query: dict[str, Any], *args: Any, **kwargs: Any) -> _FakeCursor:
        if query.get("enabled") is True:
            return _FakeCursor([d for d in self.docs if d.get("enabled")])
        return _FakeCursor(list(self.docs))

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> None:
        self.updates.append((query, update))
        for doc in self.docs:
            if doc.get("drive_file_id") == query.get("drive_file_id"):
                doc.update(update.get("$set", {}))


class _SpySender:
    def __init__(self) -> None:
        self.photos: list[tuple[int, Any, str]] = []
        self.groups: list[tuple[int, list[tuple[Any, str]]]] = []

    async def send_photo(self, chat_id: int, photo: Any, caption: str) -> str:
        self.photos.append((chat_id, photo, caption))
        return "tg-single"

    async def send_media_group(
        self, chat_id: int, media: Sequence[tuple[Any, str]]
    ) -> list[str]:
        self.groups.append((chat_id, list(media)))
        return [f"tg-{i}" for i in range(len(media))]


class _SpyDrive:
    def __init__(self) -> None:
        self.downloads: list[str] = []

    async def fetch_bytes(self, meta: Any) -> bytes:
        self.downloads.append(meta.file_id)
        return b"octets"


def _doc(dish: str, key: str, file_id: str, cache: str = "", position: int = 1) -> dict[str, Any]:
    return {
        "dish_name": dish,
        "dish_key": key,
        "drive_file_id": file_id,
        "file_name": f"{file_id}.jpg",
        "telegram_file_id": cache,
        "position": position,
        "enabled": True,
    }


def _deps(collection: _FakeCollection, **overrides: Any) -> Any:
    settings = SimpleNamespace(
        photos_enabled=True,
        photos_max_dishes=4,
        photos_max_images=10,
        photos_caption_suffix="",
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return SimpleNamespace(dish_photos=collection, settings=settings)


@pytest.mark.unit
async def test_single_dish_uses_send_photo() -> None:
    """Un seul plat cite part en sendPhoto, pas en album."""
    collection = _FakeCollection([_doc("Poulet Yassa", "poulet yassa", "img-1")])
    sender, drive = _SpySender(), _SpyDrive()

    sent = await maybe_send_photos(
        _deps(collection), 42, "Le Poulet Yassa est à 8 500 FCFA.", sender, drive
    )

    assert sent == ["Poulet Yassa"]
    assert len(sender.photos) == 1
    assert sender.groups == []
    assert sender.photos[0][2] == "Poulet Yassa"


@pytest.mark.unit
async def test_several_dishes_use_media_group() -> None:
    """Deux plats cites partent en album."""
    collection = _FakeCollection(
        [
            _doc("Poulet Yassa", "poulet yassa", "img-1"),
            _doc("Thiéboudienne", "thieboudienne", "img-2"),
        ]
    )
    sender, drive = _SpySender(), _SpyDrive()

    sent = await maybe_send_photos(
        _deps(collection), 42, "Poulet Yassa et Thiéboudienne.", sender, drive
    )

    assert sent == ["Poulet Yassa", "Thiéboudienne"]
    assert sender.photos == []
    assert len(sender.groups[0][1]) == 2


@pytest.mark.unit
async def test_first_send_downloads_then_caches() -> None:
    """Le premier envoi telecharge et enregistre le file_id Telegram."""
    collection = _FakeCollection([_doc("Poulet Yassa", "poulet yassa", "img-1")])
    sender, drive = _SpySender(), _SpyDrive()

    await maybe_send_photos(
        _deps(collection), 42, "Le Poulet Yassa.", sender, drive
    )

    assert drive.downloads == ["img-1"]
    assert collection.docs[0]["telegram_file_id"] == "tg-single"


@pytest.mark.unit
async def test_second_send_downloads_nothing() -> None:
    """Le cache evite tout telechargement au second envoi."""
    collection = _FakeCollection(
        [_doc("Poulet Yassa", "poulet yassa", "img-1", cache="tg-cache")]
    )
    sender, drive = _SpySender(), _SpyDrive()

    await maybe_send_photos(
        _deps(collection), 42, "Le Poulet Yassa.", sender, drive
    )

    assert drive.downloads == []
    assert sender.photos[0][1] == "tg-cache"


@pytest.mark.unit
async def test_caption_suffix_is_appended() -> None:
    """Le suffixe configure apparait dans la legende."""
    collection = _FakeCollection([_doc("Poulet Yassa", "poulet yassa", "img-1")])
    sender, drive = _SpySender(), _SpyDrive()

    await maybe_send_photos(
        _deps(collection, photos_caption_suffix="Photo d'illustration"),
        42,
        "Le Poulet Yassa.",
        sender,
        drive,
    )

    assert sender.photos[0][2] == "Poulet Yassa — Photo d'illustration"


@pytest.mark.unit
async def test_images_are_truncated_to_the_limit() -> None:
    """Quatre plats a trois photos rendent dix images, pas douze."""
    docs = []
    for dish, key in [
        ("Poulet Yassa", "poulet yassa"),
        ("Thiéboudienne", "thieboudienne"),
        ("Tarte Tatin", "tarte tatin"),
        ("Bissap", "bissap"),
    ]:
        for position in (1, 2, 3):
            docs.append(_doc(dish, key, f"{key}-{position}", position=position))
    collection = _FakeCollection(docs)
    sender, drive = _SpySender(), _SpyDrive()

    await maybe_send_photos(
        _deps(collection),
        42,
        "Poulet Yassa, Thiéboudienne, Tarte Tatin et Bissap.",
        sender,
        drive,
    )

    assert len(sender.groups[0][1]) == 10


@pytest.mark.unit
async def test_disabled_flag_sends_nothing() -> None:
    """photos_enabled=false coupe la fonctionnalite."""
    collection = _FakeCollection([_doc("Poulet Yassa", "poulet yassa", "img-1")])
    sender, drive = _SpySender(), _SpyDrive()

    sent = await maybe_send_photos(
        _deps(collection, photos_enabled=False), 42, "Le Poulet Yassa.", sender, drive
    )

    assert sent == []
    assert sender.photos == []


@pytest.mark.unit
async def test_no_dish_sends_nothing() -> None:
    """Un texte sans plat ne declenche aucun appel Telegram."""
    collection = _FakeCollection([_doc("Poulet Yassa", "poulet yassa", "img-1")])
    sender, drive = _SpySender(), _SpyDrive()

    sent = await maybe_send_photos(_deps(collection), 42, "Bonjour !", sender, drive)

    assert sent == []
    assert sender.photos == []
    assert sender.groups == []


@pytest.mark.unit
async def test_telegram_failure_is_swallowed() -> None:
    """Un echec d'envoi ne remonte jamais au client."""

    class _FailingSender(_SpySender):
        async def send_photo(self, chat_id: int, photo: Any, caption: str) -> str:
            raise RuntimeError("Telegram indisponible")

    collection = _FakeCollection([_doc("Poulet Yassa", "poulet yassa", "img-1")])

    sent = await maybe_send_photos(
        _deps(collection), 42, "Le Poulet Yassa.", _FailingSender(), _SpyDrive()
    )

    assert sent == []


@pytest.mark.unit
async def test_drive_failure_is_swallowed() -> None:
    """Drive injoignable ne fait pas echouer le tour de conversation."""

    class _FailingDrive:
        async def fetch_bytes(self, meta: Any) -> bytes:
            raise RuntimeError("Drive injoignable")

    collection = _FakeCollection([_doc("Poulet Yassa", "poulet yassa", "img-1")])

    sent = await maybe_send_photos(
        _deps(collection), 42, "Le Poulet Yassa.", _SpySender(), _FailingDrive()
    )

    assert sent == []
