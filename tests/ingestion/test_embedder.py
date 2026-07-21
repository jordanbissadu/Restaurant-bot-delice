"""Tests de la generation d'embeddings par lots."""

from types import SimpleNamespace

import pytest

from src.ingestion.embedder import embed_texts


class _FakeEmbeddings:
    def __init__(self, dimension: int) -> None:
        self.dimension = dimension
        self.calls: list[list[str]] = []

    async def create(self, model: str, input: list[str]):  # noqa: A002
        self.calls.append(list(input))
        data = [SimpleNamespace(embedding=[0.1] * self.dimension) for _ in input]
        return SimpleNamespace(data=data)


class _FakeOpenAI:
    def __init__(self, dimension: int = 1536) -> None:
        self.embeddings = _FakeEmbeddings(dimension)


class _FakeDeps:
    def __init__(self) -> None:
        self.openai_client = _FakeOpenAI()
        self.settings = SimpleNamespace(
            embedding_model="text-embedding-3-small", embedding_dimension=1536
        )


@pytest.mark.unit
async def test_embed_texts_returns_one_vector_per_text() -> None:
    """Un vecteur est renvoye par texte, dans l'ordre d'entree."""
    deps = _FakeDeps()

    vectors = await embed_texts(deps, ["a", "b", "c"])

    assert len(vectors) == 3
    assert all(len(v) == 1536 for v in vectors)


@pytest.mark.unit
async def test_embed_texts_batches_requests() -> None:
    """Les textes sont envoyes par lots de batch_size."""
    deps = _FakeDeps()

    await embed_texts(deps, [f"t{i}" for i in range(250)], batch_size=100)

    assert [len(c) for c in deps.openai_client.embeddings.calls] == [100, 100, 50]


@pytest.mark.unit
async def test_embed_texts_empty_input() -> None:
    """Une entree vide ne declenche aucun appel API."""
    deps = _FakeDeps()

    vectors = await embed_texts(deps, [])

    assert vectors == []
    assert deps.openai_client.embeddings.calls == []
