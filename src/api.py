"""API operationnelle : sante du service et declenchement manuel de la sync."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException

from src.dependencies import AppDependencies
from src.drive.client import DriveClient
from src.drive.diff import DeletionGuardError
from src.drive.sync import SyncReport, run_sync

logger = logging.getLogger(__name__)

_state: dict[str, object] = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ouvre les dependances au demarrage et les ferme a l'arret."""
    deps = AppDependencies()
    _state["deps"] = deps
    _state["drive"] = DriveClient(deps.settings.google_service_account_file)
    yield
    await deps.cleanup()
    _state.clear()


app = FastAPI(title="Restaurant Le Delice - operations", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """
    Verifie que le service repond.

    Returns:
        `{"status": "ok"}`.
    """
    return {"status": "ok"}


@app.post("/sync", response_model=SyncReport)
async def trigger_sync() -> SyncReport:
    """
    Declenche immediatement un cycle de synchronisation Drive.

    Returns:
        Le bilan du cycle.

    Raises:
        HTTPException: 409 si le garde-fou de suppression a bloque le cycle.
    """
    deps: AppDependencies = _state["deps"]  # type: ignore[assignment]
    drive: DriveClient = _state["drive"]  # type: ignore[assignment]

    if deps.mongo_client is None:
        await deps.initialize()

    try:
        return await run_sync(deps, drive)
    except DeletionGuardError as exc:
        logger.error("sync_blocked_by_guard: %s", exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
