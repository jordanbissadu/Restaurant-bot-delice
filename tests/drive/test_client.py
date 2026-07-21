"""Tests du client Google Drive (parcours recursif et export)."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.drive.client import EXPORT_FORMATS, DriveClient, sha256_bytes

FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_MIME = "application/vnd.google-apps.document"


class _FakeFilesResource:
    """Reproduit l'API `service.files()` pour une arborescence en memoire."""

    def __init__(self, tree: dict[str, list[dict[str, object]]]) -> None:
        self.tree = tree
        self.exported: list[tuple[str, str]] = []
        self.downloaded: list[str] = []

    def list(self, q: str, fields: str, pageToken: str | None = None, **kwargs: object):  # noqa: N803
        parent = q.split("'")[1]
        return _FakeRequest({"files": self.tree.get(parent, []), "nextPageToken": None})

    def export_media(self, fileId: str, mimeType: str):  # noqa: N803
        self.exported.append((fileId, mimeType))
        return _FakeMediaRequest(f"exported:{fileId}".encode())

    def get_media(self, fileId: str, **kwargs: object):  # noqa: N803
        self.downloaded.append(fileId)
        return _FakeMediaRequest(f"binary:{fileId}".encode())


class _FakeRequest:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def execute(self) -> dict[str, object]:
        return self.payload


class _FakeMediaRequest:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def execute(self) -> bytes:
        return self.data


class _FakeService:
    def __init__(self, files_resource: _FakeFilesResource) -> None:
        self._files = files_resource

    def files(self) -> _FakeFilesResource:
        return self._files


def _entry(file_id: str, name: str, mime: str) -> dict[str, object]:
    return {
        "id": file_id,
        "name": name,
        "mimeType": mime,
        "modifiedTime": "2026-07-21T10:00:00.000Z",
        "trashed": False,
    }


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> DriveClient:
    tree = {
        "root": [
            _entry("f1", "menu", DOC_MIME),
            _entry("sub", "promos", FOLDER_MIME),
        ],
        "sub": [_entry("f2", "promo-juillet.pdf", "application/pdf")],
    }
    resource = _FakeFilesResource(tree)
    monkeypatch.setattr(
        "src.drive.client.DriveClient._build_service",
        lambda self: _FakeService(resource),
    )
    instance = DriveClient(service_account_file="unused.json")
    instance._test_resource = resource  # type: ignore[attr-defined]
    return instance


@pytest.mark.unit
async def test_list_folder_files_is_recursive(client: DriveClient) -> None:
    """Le parcours descend dans les sous-dossiers et exclut les dossiers eux-memes."""
    files = await client.list_folder_files("root")

    assert {f.file_id for f in files} == {"f1", "f2"}
    assert all(f.mime_type != FOLDER_MIME for f in files)


@pytest.mark.unit
async def test_list_parses_modified_time(client: DriveClient) -> None:
    """modifiedTime est parse en datetime timezone-aware."""
    files = await client.list_folder_files("root")

    menu = next(f for f in files if f.file_id == "f1")
    assert menu.modified_time == datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)


@pytest.mark.unit
async def test_download_exports_google_native_as_pdf(
    client: DriveClient, tmp_path: Path
) -> None:
    """Un Google Doc natif est exporte en PDF."""
    files = await client.list_folder_files("root")
    menu = next(f for f in files if f.file_id == "f1")

    path, digest = await client.download(menu, tmp_path)

    assert path.suffix == ".pdf"
    assert path.read_bytes() == b"exported:f1"
    assert digest == sha256_bytes(b"exported:f1")
    assert client._test_resource.exported == [("f1", "application/pdf")]


@pytest.mark.unit
async def test_download_binary_file_directly(
    client: DriveClient, tmp_path: Path
) -> None:
    """Un fichier binaire uploade est telecharge tel quel."""
    files = await client.list_folder_files("root")
    promo = next(f for f in files if f.file_id == "f2")

    path, digest = await client.download(promo, tmp_path)

    assert path.read_bytes() == b"binary:f2"
    assert digest == sha256_bytes(b"binary:f2")
    assert client._test_resource.downloaded == ["f2"]


@pytest.mark.unit
def test_export_formats_cover_google_native_types() -> None:
    """Les trois types Google natifs attendus sont couverts."""
    assert EXPORT_FORMATS[DOC_MIME] == ("application/pdf", ".pdf")
    assert EXPORT_FORMATS["application/vnd.google-apps.presentation"] == (
        "application/pdf",
        ".pdf",
    )
    assert EXPORT_FORMATS["application/vnd.google-apps.spreadsheet"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    )


@pytest.mark.unit
def test_sha256_is_stable() -> None:
    """Le hash est deterministe et distingue deux contenus."""
    assert sha256_bytes(b"abc") == sha256_bytes(b"abc")
    assert sha256_bytes(b"abc") != sha256_bytes(b"abd")
