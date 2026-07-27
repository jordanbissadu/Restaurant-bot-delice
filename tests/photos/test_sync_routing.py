"""Tests du routage des fichiers Drive par type MIME."""

from datetime import datetime, timezone

import pytest

from src.models import DriveFileMeta
from src.photos.sync import route_drive_files

NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)


def _meta(name: str, mime: str, file_id: str = "id") -> DriveFileMeta:
    return DriveFileMeta(
        file_id=file_id, name=name, mime_type=mime, modified_time=NOW
    )


@pytest.mark.unit
def test_images_are_not_routed_to_documents() -> None:
    """Une image ne doit jamais partir en vectorisation."""
    files = [
        _meta("menu.gdoc", "application/vnd.google-apps.document", "doc-1"),
        _meta("poulet-yassa.jpg", "image/jpeg", "img-1"),
        _meta("poisson.png", "image/png", "img-2"),
    ]

    routing = route_drive_files(files, catalogue_name="photos")

    assert [f.file_id for f in routing.documents] == ["doc-1"]
    assert [f.file_id for f in routing.images] == ["img-1", "img-2"]


@pytest.mark.unit
def test_catalogue_sheet_is_isolated() -> None:
    """Le Sheet du catalogue n'est ni un document ni une image."""
    files = [
        _meta("menu.gdoc", "application/vnd.google-apps.document", "doc-1"),
        _meta("photos", "application/vnd.google-apps.spreadsheet", "cat-1"),
    ]

    routing = route_drive_files(files, catalogue_name="photos")

    assert routing.catalogue is not None
    assert routing.catalogue.file_id == "cat-1"
    assert [f.file_id for f in routing.documents] == ["doc-1"]


@pytest.mark.unit
def test_catalogue_as_plain_csv_is_recognized() -> None:
    """Le catalogue peut aussi etre un simple fichier .csv."""
    files = [_meta("photos.csv", "text/csv", "cat-1")]

    routing = route_drive_files(files, catalogue_name="photos")

    assert routing.catalogue is not None
    assert routing.catalogue.file_id == "cat-1"
    assert routing.documents == []


@pytest.mark.unit
def test_other_spreadsheet_stays_a_document() -> None:
    """Un tableur qui n'est pas le catalogue reste un document du menu."""
    files = [_meta("tarifs", "application/vnd.google-apps.spreadsheet", "sheet-1")]

    routing = route_drive_files(files, catalogue_name="photos")

    assert routing.catalogue is None
    assert [f.file_id for f in routing.documents] == ["sheet-1"]


@pytest.mark.unit
def test_no_files_gives_empty_routing() -> None:
    """Un dossier vide ne fait pas echouer le routage."""
    routing = route_drive_files([], catalogue_name="photos")

    assert routing.documents == []
    assert routing.images == []
    assert routing.catalogue is None
