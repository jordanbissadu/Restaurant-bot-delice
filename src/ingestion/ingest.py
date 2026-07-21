"""Ingestion d'un fichier Drive vers MongoDB, avec remplacement versionne.

Le remplacement des chunks suit l'ordre insertion-puis-suppression : les
nouveaux chunks (version N+1) sont ecrits avant que les anciens (version <= N)
soient supprimes. La fenetre transitoire expose donc des doublons plutot qu'un
document sans aucun chunk, ce qui degrade la recherche sans jamais faire
disparaitre le menu d'une conversation en cours.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.ingestion.chunker import chunk_file
from src.ingestion.embedder import embed_texts
from src.models import DriveFileMeta

if TYPE_CHECKING:
    from src.dependencies import AppDependencies

logger = logging.getLogger(__name__)


async def ingest_document(
    deps: "AppDependencies",
    path: Path,
    meta: DriveFileMeta,
    content_hash: str,
) -> int:
    """
    Ingere un fichier telecharge : decoupage, vectorisation, ecriture MongoDB.

    Args:
        deps: Dependances applicatives.
        path: Chemin local du fichier telecharge.
        meta: Metadonnees Drive du fichier.
        content_hash: sha256 du contenu telecharge.

    Returns:
        Le nombre de chunks ecrits.

    Raises:
        RuntimeError: Si le decoupage Docling echoue.
        openai.APIError: Si la vectorisation echoue.
    """
    chunks = chunk_file(path, max_tokens=deps.settings.chunk_max_tokens)
    if not chunks:
        logger.warning("document_without_content: file=%s", meta.name)

    existing = await deps.documents.find_one({"drive.file_id": meta.file_id})
    version = int(existing["version"]) + 1 if existing else 1

    now = datetime.now(timezone.utc)
    trace: dict[str, Any] = {
        "file_id": meta.file_id,
        "modified_time": meta.modified_time,
        "content_hash": content_hash,
        "mime_type": meta.mime_type,
        "last_synced_at": now,
    }

    if existing is None:
        result = await deps.documents.insert_one(
            {
                "title": meta.name,
                "source": f"gdrive://{meta.file_id}",
                "drive": trace,
                "chunk_count": 0,
                "version": version,
                "created_at": now,
            }
        )
        document_id = result.inserted_id
    else:
        document_id = existing["_id"]

    if chunks:
        embeddings = await embed_texts(deps, [c.content for c in chunks])
        await deps.chunks.insert_many(
            [
                {
                    "document_id": document_id,
                    "content": chunk.content,
                    "embedding": embedding,
                    "chunk_index": chunk.chunk_index,
                    "version": version,
                    "token_count": chunk.token_count,
                }
                for chunk, embedding in zip(chunks, embeddings, strict=True)
            ]
        )

    await deps.chunks.delete_many(
        {"document_id": document_id, "version": {"$lt": version}}
    )

    await deps.documents.update_one(
        {"drive.file_id": meta.file_id},
        {
            "$set": {
                "title": meta.name,
                "drive": trace,
                "chunk_count": len(chunks),
                "version": version,
            }
        },
    )

    logger.info(
        "document_ingested: file=%s version=%d chunks=%d",
        meta.name,
        version,
        len(chunks),
    )
    return len(chunks)


async def delete_document(deps: "AppDependencies", file_id: str) -> None:
    """
    Supprime un document et l'integralite de ses chunks.

    Args:
        deps: Dependances applicatives.
        file_id: Identifiant Drive du document a supprimer.
    """
    document = await deps.documents.find_one({"drive.file_id": file_id})
    if document is None:
        logger.warning("delete_skipped_unknown_document: file_id=%s", file_id)
        return

    await deps.chunks.delete_many({"document_id": document["_id"]})
    await deps.documents.delete_many({"drive.file_id": file_id})
    logger.info("document_deleted: file_id=%s title=%s", file_id, document.get("title"))


async def touch_document(
    deps: "AppDependencies", file_id: str, modified_time: datetime
) -> None:
    """
    Met a jour les horodatages d'un document dont le contenu n'a pas change.

    Evite de re-declencher une ingestion au tick suivant alors que seul le
    `modifiedTime` de Drive a bouge (ouverture-fermeture d'un Google Doc).

    Args:
        deps: Dependances applicatives.
        file_id: Identifiant Drive du document.
        modified_time: Nouvel horodatage renvoye par Drive.
    """
    await deps.documents.update_one(
        {"drive.file_id": file_id},
        {
            "$set": {
                "drive.modified_time": modified_time,
                "drive.last_synced_at": datetime.now(timezone.utc),
            }
        },
    )
    logger.info("document_touched: file_id=%s", file_id)
