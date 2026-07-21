"""Worker planifie : synchronisation periodique du dossier Google Drive."""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.dependencies import AppDependencies
from src.drive.client import DriveClient
from src.drive.diff import DeletionGuardError
from src.drive.sync import run_sync

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger(__name__)


async def start_worker() -> None:
    """
    Demarre la boucle de synchronisation et la maintient en vie.

    Un premier cycle est declenche immediatement, puis toutes les
    `drive_sync_interval_minutes` minutes. Les erreurs d'un cycle sont loggees
    sans interrompre la planification.
    """
    async with AppDependencies() as deps:
        client = DriveClient(deps.settings.google_service_account_file)

        async def tick() -> None:
            try:
                report = await run_sync(deps, client)
                logger.info("sync_cycle_done: %s", report.model_dump())
            except DeletionGuardError as exc:
                logger.error("sync_cycle_blocked: %s", exc)
            except Exception:
                logger.exception("sync_cycle_failed")

        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            tick,
            "interval",
            minutes=deps.settings.drive_sync_interval_minutes,
            id="drive_sync",
        )
        scheduler.start()
        logger.info(
            "worker_started: interval=%d min",
            deps.settings.drive_sync_interval_minutes,
        )

        await tick()
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(start_worker())
