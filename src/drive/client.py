"""Client Google Drive authentifie par compte de service.

L'API `google-api-python-client` est synchrone : chaque appel reseau est
deporte dans un thread via `asyncio.to_thread` pour ne pas bloquer la boucle.
"""

import asyncio
import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

from src.models import DriveFileMeta

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
FOLDER_MIME = "application/vnd.google-apps.folder"

# mime Google natif -> (mime d'export, extension du fichier ecrit sur disque)
EXPORT_FORMATS: dict[str, tuple[str, str]] = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
}

_LIST_FIELDS = "nextPageToken, files(id, name, mimeType, modifiedTime, trashed)"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def sha256_bytes(data: bytes) -> str:
    """
    Calcule le sha256 hexadecimal d'un contenu binaire.

    Args:
        data: Contenu a hacher.

    Returns:
        Le digest hexadecimal.
    """
    return hashlib.sha256(data).hexdigest()


class DriveClient:
    """Acces en lecture a un dossier Google Drive partage."""

    def __init__(self, service_account_file: str) -> None:
        """
        Args:
            service_account_file: Chemin du fichier JSON du compte de service.
        """
        self.service_account_file = service_account_file
        self._service: Any | None = None

    def _build_service(self) -> Any:
        """Construit le client Drive v3 a partir du compte de service."""
        credentials = service_account.Credentials.from_service_account_file(
            self.service_account_file, scopes=SCOPES
        )
        return build("drive", "v3", credentials=credentials, cache_discovery=False)

    @property
    def service(self) -> Any:
        """Client Drive, construit paresseusement."""
        if self._service is None:
            self._service = self._build_service()
        return self._service

    async def list_folder_files(self, folder_id: str) -> list[DriveFileMeta]:
        """
        Liste recursivement les fichiers d'un dossier Drive.

        Les sous-dossiers sont parcourus mais ne figurent pas dans le resultat.

        Args:
            folder_id: Identifiant du dossier racine.

        Returns:
            Les metadonnees de tous les fichiers, sous-dossiers inclus.

        Raises:
            googleapiclient.errors.HttpError: Si l'API Drive renvoie une erreur.
        """
        collected: list[DriveFileMeta] = []
        pending: list[str] = [folder_id]
        seen: set[str] = set()

        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)

            for entry in await asyncio.to_thread(self._list_children, current):
                if entry["mimeType"] == FOLDER_MIME:
                    pending.append(entry["id"])
                    continue
                collected.append(
                    DriveFileMeta(
                        file_id=entry["id"],
                        name=entry["name"],
                        mime_type=entry["mimeType"],
                        modified_time=_parse_drive_time(entry["modifiedTime"]),
                        trashed=bool(entry.get("trashed", False)),
                    )
                )

        logger.info("drive_listed: folder=%s files=%d", folder_id, len(collected))
        return collected

    def _list_children(self, folder_id: str) -> list[dict[str, Any]]:
        """Liste les enfants directs d'un dossier, toutes pages confondues."""
        children: list[dict[str, Any]] = []
        page_token: str | None = None

        while True:
            response = (
                self.service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields=_LIST_FIELDS,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            children.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return children

    async def download(self, meta: DriveFileMeta, target_dir: Path) -> tuple[Path, str]:
        """
        Telecharge un fichier Drive et calcule le hash de son contenu.

        Les documents Google natifs sont exportes selon `EXPORT_FORMATS`; les
        autres fichiers sont recuperes tels quels.

        Args:
            meta: Metadonnees du fichier a telecharger.
            target_dir: Repertoire ou ecrire le fichier.

        Returns:
            Le chemin local du fichier ecrit et le sha256 de son contenu.

        Raises:
            googleapiclient.errors.HttpError: Si l'API Drive renvoie une erreur.
        """
        data = await asyncio.to_thread(self._fetch_bytes, meta)
        suffix = self._suffix_for(meta)
        safe_name = _SAFE_NAME.sub("_", Path(meta.name).stem) or meta.file_id

        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{safe_name}{suffix}"
        path.write_bytes(data)

        digest = sha256_bytes(data)
        logger.info(
            "drive_downloaded: file=%s bytes=%d hash=%s",
            meta.name,
            len(data),
            digest[:12],
        )
        return path, digest

    def _fetch_bytes(self, meta: DriveFileMeta) -> bytes:
        """Recupere le contenu binaire d'un fichier (export ou telechargement)."""
        export = EXPORT_FORMATS.get(meta.mime_type)
        if export is not None:
            return (
                self.service.files()
                .export_media(fileId=meta.file_id, mimeType=export[0])
                .execute()
            )
        return (
            self.service.files()
            .get_media(fileId=meta.file_id, supportsAllDrives=True)
            .execute()
        )

    @staticmethod
    def _suffix_for(meta: DriveFileMeta) -> str:
        """Determine l'extension du fichier ecrit sur disque."""
        export = EXPORT_FORMATS.get(meta.mime_type)
        if export is not None:
            return export[1]
        return Path(meta.name).suffix or ".bin"


def _parse_drive_time(value: str) -> datetime:
    """
    Parse un horodatage RFC 3339 renvoye par l'API Drive.

    Args:
        value: Horodatage de la forme `2026-07-21T10:00:00.000Z`.

    Returns:
        Le datetime timezone-aware correspondant.
    """
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
