"""Conteneur de dependances externes (MongoDB, OpenAI)."""

import logging
from typing import Optional

from openai import AsyncOpenAI
from pymongo import AsyncMongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from src.settings import Settings, load_settings

logger = logging.getLogger(__name__)


class AppDependencies:
    """
    Regroupe les clients externes partages par le bot et le worker de sync.

    Utilisable comme context manager async :

        async with AppDependencies() as deps:
            await deps.documents.find_one({})
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        """
        Args:
            settings: Configuration pre-chargee, ou None pour la charger.
        """
        self.settings: Settings = settings or load_settings()
        self.mongo_client: Optional[AsyncMongoClient] = None
        self.db = None
        self.openai_client: Optional[AsyncOpenAI] = None

    async def initialize(self) -> None:
        """
        Ouvre les connexions externes et verifie la connectivite MongoDB.

        Raises:
            ConnectionFailure: Si la connexion MongoDB echoue.
            ServerSelectionTimeoutError: Si aucun serveur MongoDB n'est joignable.
        """
        if self.mongo_client is None:
            try:
                self.mongo_client = AsyncMongoClient(
                    self.settings.mongodb_uri, serverSelectionTimeoutMS=5000
                )
                self.db = self.mongo_client[self.settings.mongodb_database]
                await self.mongo_client.admin.command("ping")
                logger.info(
                    "mongodb_connected: database=%s", self.settings.mongodb_database
                )
            except (ConnectionFailure, ServerSelectionTimeoutError):
                logger.exception("mongodb_connection_failed")
                raise

        if self.openai_client is None:
            self.openai_client = AsyncOpenAI(api_key=self.settings.openai_api_key)

    async def cleanup(self) -> None:
        """Ferme les connexions externes."""
        if self.mongo_client is not None:
            await self.mongo_client.close()
            self.mongo_client = None
            self.db = None
            logger.info("mongodb_connection_closed")
        self.openai_client = None

    async def __aenter__(self) -> "AppDependencies":
        await self.initialize()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.cleanup()

    @property
    def documents(self):
        """Collection des documents sources."""
        return self.db[self.settings.mongodb_collection_documents]

    @property
    def chunks(self):
        """Collection des fragments vectorises."""
        return self.db[self.settings.mongodb_collection_chunks]

    @property
    def orders(self):
        """Collection des commandes clients."""
        return self.db[self.settings.mongodb_collection_orders]
