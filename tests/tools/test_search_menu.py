"""Tests de la recherche hybride et du reranking."""

from types import SimpleNamespace
from typing import Any

import pytest

from src.tools.search_menu import SearchResult, hybrid_search, rerank, search_menu


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self.docs


class _FakeChunks:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs
        self.pipelines: list[list[dict[str, Any]]] = []

    async def aggregate(self, pipeline: list[dict[str, Any]]) -> _FakeCursor:
        # PyMongo async : aggregate() est une coroutine, contrairement a find().
        # Le fake doit l'etre aussi, sinon il masque le vrai comportement du
        # driver (bug 'coroutine' object has no attribute 'to_list').
        self.pipelines.append(pipeline)
        return _FakeCursor(self.docs)


class _FakeDeps:
    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self.chunks = _FakeChunks(docs or [])
        self.settings = SimpleNamespace(
            mongodb_vector_index="vector_index",
            mongodb_text_index="text_index",
            mongodb_collection_documents="documents",
            embedding_model="text-embedding-3-small",
            cohere_api_key="co-test",
            rerank_model="rerank-v3.5",
            rerank_top_n=10,
        )


def _chunk_doc(chunk_id: str, content: str, score: float = 0.5) -> dict[str, Any]:
    return {
        "_id": chunk_id,
        "document_id": "doc-1",
        "content": content,
        "score": score,
        "document_info": {"title": "Menu Le Delice"},
    }


@pytest.fixture
def fake_embed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _embed(deps: Any, texts: list[str], batch_size: int = 100):
        return [[0.1] * 1536 for _ in texts]

    monkeypatch.setattr("src.tools.search_menu.embed_texts", _embed)


@pytest.mark.unit
async def test_hybrid_search_maps_results(fake_embed) -> None:
    """Les documents MongoDB sont convertis en SearchResult."""
    deps = _FakeDeps([_chunk_doc("c1", "Poulet Yassa - 8 500 FCFA", 0.9)])

    results = await hybrid_search(deps, "poulet")

    assert len(results) == 1
    assert results[0].chunk_id == "c1"
    assert results[0].content == "Poulet Yassa - 8 500 FCFA"
    assert results[0].title == "Menu Le Delice"


@pytest.mark.unit
async def test_hybrid_search_uses_rankfusion(fake_embed) -> None:
    """Le pipeline combine vectoriel et plein texte via $rankFusion."""
    deps = _FakeDeps([_chunk_doc("c1", "x")])

    await hybrid_search(deps, "poulet")

    stage_names = [next(iter(stage)) for stage in deps.chunks.pipelines[0]]
    assert stage_names[0] == "$rankFusion"
    fusion = deps.chunks.pipelines[0][0]["$rankFusion"]
    assert set(fusion["input"]["pipelines"]) == {"vector", "text"}
    assert "$lookup" in stage_names
    assert "$unwind" in stage_names


@pytest.mark.unit
async def test_hybrid_search_empty_query_returns_nothing(fake_embed) -> None:
    """Une requete vide ne declenche aucun appel MongoDB."""
    deps = _FakeDeps([_chunk_doc("c1", "x")])

    results = await hybrid_search(deps, "   ")

    assert results == []
    assert deps.chunks.pipelines == []


@pytest.mark.unit
async def test_rerank_reorders_and_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le reranker reordonne les resultats et applique top_n."""

    class _FakeCohere:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def rerank(self, model: str, query: str, documents: list[str], top_n: int):
            # Inverse l'ordre d'entree pour rendre l'effet observable.
            order = list(reversed(range(len(documents))))[:top_n]
            return SimpleNamespace(
                results=[
                    SimpleNamespace(index=i, relevance_score=1.0 - n * 0.1)
                    for n, i in enumerate(order)
                ]
            )

    monkeypatch.setattr("src.tools.search_menu.cohere.Client", _FakeCohere)
    deps = _FakeDeps()
    results = [
        SearchResult(chunk_id="c1", document_id="d", content="a", score=0.1, title="t"),
        SearchResult(chunk_id="c2", document_id="d", content="b", score=0.2, title="t"),
        SearchResult(chunk_id="c3", document_id="d", content="c", score=0.3, title="t"),
    ]

    reranked = await rerank(deps, "poulet", results, top_n=2)

    assert [r.chunk_id for r in reranked] == ["c3", "c2"]
    assert reranked[0].score == 1.0


@pytest.mark.unit
async def test_rerank_without_api_key_is_passthrough() -> None:
    """Sans cle Cohere, les resultats sont renvoyes tronques mais inchanges."""
    deps = _FakeDeps()
    deps.settings.cohere_api_key = ""
    results = [
        SearchResult(chunk_id=f"c{i}", document_id="d", content="x", score=0.1, title="t")
        for i in range(5)
    ]

    reranked = await rerank(deps, "poulet", results, top_n=3)

    assert [r.chunk_id for r in reranked] == ["c0", "c1", "c2"]


@pytest.mark.unit
async def test_rerank_empty_results() -> None:
    """Aucun appel Cohere quand il n'y a rien a reordonner."""
    deps = _FakeDeps()

    assert await rerank(deps, "poulet", [], top_n=10) == []


@pytest.mark.unit
async def test_search_menu_chains_search_and_rerank(
    fake_embed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """search_menu enchaine hybrid_search puis rerank avec rerank_top_n."""
    deps = _FakeDeps([_chunk_doc(f"c{i}", f"plat {i}") for i in range(5)])
    deps.settings.cohere_api_key = ""
    deps.settings.rerank_top_n = 2

    results = await search_menu(deps, "poulet")

    assert len(results) == 2
