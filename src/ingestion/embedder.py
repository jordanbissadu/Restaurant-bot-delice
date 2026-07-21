"""Generation d'embeddings OpenAI par lots."""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.dependencies import AppDependencies

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 100


async def embed_texts(
    deps: "AppDependencies",
    texts: list[str],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[list[float]]:
    """
    Genere les embeddings d'une liste de textes, par lots.

    Args:
        deps: Dependances applicatives fournissant le client OpenAI.
        texts: Textes a vectoriser.
        batch_size: Nombre de textes par appel API.

    Returns:
        Les vecteurs, dans le meme ordre que `texts`.

    Raises:
        openai.APIError: Si l'API OpenAI renvoie une erreur.
    """
    if not texts:
        return []

    vectors: list[list[float]] = []
    model = deps.settings.embedding_model

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        response = await deps.openai_client.embeddings.create(model=model, input=batch)
        vectors.extend(item.embedding for item in response.data)
        logger.info(
            "embeddings_batch_done: model=%s size=%d total=%d/%d",
            model,
            len(batch),
            len(vectors),
            len(texts),
        )

    return vectors
