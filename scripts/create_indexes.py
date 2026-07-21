"""Cree les index MongoDB classiques et documente l'index vectoriel Atlas.

L'index vectoriel ne peut pas etre cree via pymongo : il doit l'etre depuis
l'UI Atlas (Atlas Search > Create Search Index > JSON Editor). Ce script
affiche la definition exacte a coller.
"""

import asyncio
import json
import logging

from src.dependencies import AppDependencies

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Cree les index classiques puis affiche les definitions Atlas."""
    async with AppDependencies() as deps:
        await deps.documents.create_index("drive.file_id", unique=True)
        await deps.chunks.create_index([("document_id", 1), ("version", 1)])
        await deps.orders.create_index([("chat_id", 1), ("created_at", -1)])
        await deps.orders.create_index("order_number", unique=True)
        logger.info("classic_indexes_created")

    vector_index = {
        "name": "vector_index",
        "type": "vectorSearch",
        "definition": {
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": 1536,
                    "similarity": "cosine",
                }
            ]
        },
    }
    text_index = {
        "name": "text_index",
        "type": "search",
        "definition": {
            "mappings": {"dynamic": False, "fields": {"content": {"type": "string"}}}
        },
    }

    print("\n=== A creer dans l'UI Atlas, collection `chunks` ===\n")
    print(json.dumps(vector_index, indent=2))
    print()
    print(json.dumps(text_index, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
