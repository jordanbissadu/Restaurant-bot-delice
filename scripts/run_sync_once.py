"""Declenche un unique cycle de synchronisation Drive, pour debug."""

import asyncio
import json
import logging

from src.dependencies import AppDependencies
from src.drive.client import DriveClient
from src.drive.sync import run_sync

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    """Execute un cycle et affiche le bilan en JSON."""
    async with AppDependencies() as deps:
        client = DriveClient(deps.settings.google_service_account_file)
        report = await run_sync(deps, client)
        print(json.dumps(report.model_dump(), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
