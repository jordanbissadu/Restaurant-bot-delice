"""Recherche hybride dans le menu : $rankFusion MongoDB puis rerank Cohere.

La combinaison des scores vectoriels et plein texte est deleguee a
`$rankFusion` (MongoDB 8.1+). Aucun score n'est recombine a la main.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import cohere
from pydantic import BaseModel, Field

from src.ingestion.embedder import embed_texts

if TYPE_CHECKING:
    from src.dependencies import AppDependencies

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 20
VECTOR_CANDIDATE_MULTIPLIER = 10


class SearchResult(BaseModel):
    """Fragment de menu retourne par la recherche."""

    chunk_id: str = Field(..., description="ObjectId du chunk, en chaine")
    document_id: str = Field(..., description="ObjectId du document parent")
    content: str = Field(..., description="Texte du fragment")
    score: float = Field(..., description="Score de pertinence")
    title: str = Field(default="", description="Titre du document parent")


async def hybrid_search(
    deps: "AppDependencies", query: str, limit: int = DEFAULT_LIMIT
) -> list[SearchResult]:
    """
    Recherche hybride vectorielle + plein texte sur la collection `chunks`.

    Args:
        deps: Dependances applicatives.
        query: Texte de la requete.
        limit: Nombre de resultats fusionnes a remonter.

    Returns:
        Les fragments les plus pertinents, ordonnes par score decroissant.

    Raises:
        pymongo.errors.OperationFailure: Si un index Atlas est absent ou si le
            cluster ne supporte pas `$rankFusion` (MongoDB < 8.1).
    """
    if not query or not query.strip():
        return []

    embedding = (await embed_texts(deps, [query]))[0]

    pipeline: list[dict[str, Any]] = [
        {
            "$rankFusion": {
                "input": {
                    "pipelines": {
                        "vector": [
                            {
                                "$vectorSearch": {
                                    "index": deps.settings.mongodb_vector_index,
                                    "path": "embedding",
                                    "queryVector": embedding,
                                    "numCandidates": limit * VECTOR_CANDIDATE_MULTIPLIER,
                                    "limit": limit,
                                }
                            }
                        ],
                        "text": [
                            {
                                "$search": {
                                    "index": deps.settings.mongodb_text_index,
                                    "text": {"query": query, "path": "content"},
                                }
                            },
                            {"$limit": limit},
                        ],
                    }
                },
                "combination": {"weights": {"vector": 0.7, "text": 0.3}},
            }
        },
        {"$addFields": {"score": {"$meta": "score"}}},
        {"$limit": limit},
        {
            "$lookup": {
                "from": deps.settings.mongodb_collection_documents,
                "localField": "document_id",
                "foreignField": "_id",
                "as": "document_info",
            }
        },
        {"$unwind": {"path": "$document_info", "preserveNullAndEmptyArrays": True}},
    ]

    raw = await deps.chunks.aggregate(pipeline).to_list(length=limit)

    results = [
        SearchResult(
            chunk_id=str(doc["_id"]),
            document_id=str(doc.get("document_id", "")),
            content=doc.get("content", ""),
            score=_as_float(doc.get("score")),
            title=(doc.get("document_info") or {}).get("title", ""),
        )
        for doc in raw
    ]

    logger.info("hybrid_search_done: query=%r results=%d", query[:60], len(results))
    return results


async def rerank(
    deps: "AppDependencies", query: str, results: list[SearchResult], top_n: int
) -> list[SearchResult]:
    """
    Reordonne les resultats avec le reranker Cohere.

    Sans cle API Cohere, les resultats sont renvoyes dans leur ordre d'origine,
    tronques a `top_n`.

    Args:
        deps: Dependances applicatives.
        query: Requete d'origine.
        results: Resultats a reordonner.
        top_n: Nombre de resultats a conserver.

    Returns:
        Les `top_n` resultats les plus pertinents.
    """
    if not results:
        return []
    if not deps.settings.cohere_api_key:
        logger.info("rerank_skipped: aucune cle Cohere configuree")
        return results[:top_n]

    client = cohere.Client(deps.settings.cohere_api_key)
    documents = [r.content for r in results]

    response = await asyncio.to_thread(
        client.rerank,
        model=deps.settings.rerank_model,
        query=query,
        documents=documents,
        top_n=min(top_n, len(documents)),
    )

    reranked: list[SearchResult] = []
    for item in response.results:
        original = results[item.index]
        reranked.append(original.model_copy(update={"score": item.relevance_score}))

    logger.info("rerank_done: in=%d out=%d", len(results), len(reranked))
    return reranked


async def search_menu(deps: "AppDependencies", query: str) -> list[SearchResult]:
    """
    Recherche complete dans le menu : hybride puis reranking.

    Args:
        deps: Dependances applicatives.
        query: Texte de la requete du client.

    Returns:
        Les fragments les plus pertinents, au plus `settings.rerank_top_n`.
    """
    candidates = await hybrid_search(deps, query)
    return await rerank(deps, query, candidates, deps.settings.rerank_top_n)


def _as_float(value: Any) -> float:
    """Convertit un score MongoDB heterogene en flottant, 0.0 par defaut."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
