"""Decoupage de documents via Docling HybridChunker."""

import logging
from pathlib import Path

from docling.chunking import HybridChunker
from docling.document_converter import DocumentConverter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TextChunk(BaseModel):
    """Fragment de texte issu du decoupage d'un document."""

    content: str = Field(..., description="Texte du fragment, contextualise")
    chunk_index: int = Field(..., ge=0, description="Position dans le document")
    token_count: int = Field(
        default=0, ge=0, description="Estimation du nombre de tokens"
    )


def chunk_file(path: Path, max_tokens: int = 512) -> list[TextChunk]:
    """
    Convertit un fichier puis le decoupe en respectant sa structure.

    Le HybridChunker de Docling respecte titres, sections et tableaux, ce qui
    evite de separer un plat de son prix. `contextualize` re-injecte le chemin
    de titres dans chaque fragment.

    Args:
        path: Chemin du fichier a traiter (PDF, DOCX, XLSX, MD, HTML...).
        max_tokens: Taille maximale d'un fragment, en tokens.

    Returns:
        La liste ordonnee des fragments. Liste vide si le document ne contient
        aucun texte exploitable.

    Raises:
        RuntimeError: Si Docling ne parvient pas a convertir le fichier.
    """
    converter = DocumentConverter()
    try:
        result = converter.convert(str(path))
    except Exception as exc:  # Docling remonte des exceptions heterogenes
        logger.exception("docling_conversion_failed: file=%s", path.name)
        raise RuntimeError(f"conversion Docling impossible pour {path.name}") from exc

    chunker = HybridChunker(max_tokens=max_tokens)
    chunks: list[TextChunk] = []

    for raw_chunk in chunker.chunk(dl_doc=result.document):
        content = chunker.contextualize(chunk=raw_chunk).strip()
        if not content:
            continue
        chunks.append(
            TextChunk(
                content=content,
                chunk_index=len(chunks),
                token_count=len(content.split()),
            )
        )

    logger.info("document_chunked: file=%s chunks=%d", path.name, len(chunks))
    return chunks
