"""Diagnostic de l'acces Google Drive du compte de service.

Affiche ce que le compte de service voit reellement, pour distinguer un
probleme d'ID de dossier, de partage, ou de Drive partage.

Usage:
    uv run python -m scripts.diagnose_drive
"""

import asyncio

from src.dependencies import AppDependencies
from src.drive.client import DriveClient


def _svc(client: DriveClient):
    return client.service


async def main() -> None:
    """Interroge Drive de plusieurs facons et affiche les resultats bruts."""
    async with AppDependencies() as deps:
        folder_id = deps.settings.google_drive_folder_id
        client = DriveClient(deps.settings.google_service_account_file)
        svc = _svc(client)

        print(f"\n=== 1. Identite du compte de service ===")
        # Le mail du compte de service est dans le fichier JSON de credentials.
        import json

        with open(deps.settings.google_service_account_file, encoding="utf-8") as fh:
            sa = json.load(fh)
        print(f"email compte de service : {sa.get('client_email')}")
        print(f"projet                  : {sa.get('project_id')}")

        print(f"\n=== 2. Metadonnees du dossier (ID={folder_id}) ===")
        try:
            meta = (
                svc.files()
                .get(
                    fileId=folder_id,
                    fields="id, name, mimeType, driveId, trashed, shortcutDetails",
                    supportsAllDrives=True,
                )
                .execute()
            )
            print(f"nom      : {meta.get('name')}")
            print(f"type     : {meta.get('mimeType')}")
            print(f"driveId  : {meta.get('driveId')}  (present => Drive partage)")
            print(f"corbeille: {meta.get('trashed')}")
            is_folder = meta.get("mimeType") == "application/vnd.google-apps.folder"
            if not is_folder:
                print("  !!! Ce n'est PAS un dossier. L'ID pointe sur un fichier.")
        except Exception as exc:
            print(f"  !!! ECHEC get(): {type(exc).__name__}: {exc}")
            print("  => Le compte de service ne voit pas cet ID (partage ? ID ?).")

        print(f"\n=== 3. Enfants directs du dossier ===")
        try:
            resp = (
                svc.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields="files(id, name, mimeType)",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            files = resp.get("files", [])
            print(f"nombre d'enfants : {len(files)}")
            for f in files[:20]:
                print(f"  - {f['name']}  [{f['mimeType']}]")
        except Exception as exc:
            print(f"  !!! ECHEC list(): {type(exc).__name__}: {exc}")

        print(f"\n=== 4. TOUT ce que le compte de service peut voir (10 max) ===")
        try:
            resp = (
                svc.files()
                .list(
                    fields="files(id, name, mimeType, owners(emailAddress))",
                    pageSize=10,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            files = resp.get("files", [])
            print(f"nombre total visible : {len(files)}")
            for f in files:
                owner = (f.get("owners") or [{}])[0].get("emailAddress", "?")
                print(f"  - {f['name']}  [{f['mimeType']}]  proprietaire={owner}")
            if not files:
                print("  => Le compte de service ne voit AUCUN fichier partage.")
                print("     Le partage du dossier n'a pas atteint ce compte.")
        except Exception as exc:
            print(f"  !!! ECHEC list global: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
