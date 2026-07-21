"""Tests de l'ingestion et du remplacement versionne des chunks."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.ingestion.ingest import delete_document, ingest_document, touch_document
from src.models import DriveFileMeta


class _FakeCollection:
    """Collection MongoDB minimaliste en memoire."""

    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self.docs:
            if self._matches(doc, query):
                return doc
        return None

    async def insert_one(self, doc: dict[str, Any]) -> SimpleNamespace:
        doc = dict(doc)
        doc.setdefault("_id", f"id-{len(self.docs)}")
        self.docs.append(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    async def insert_many(self, docs: list[dict[str, Any]]) -> SimpleNamespace:
        # insert_one copie chaque document : les identifiants doivent etre
        # releves sur son resultat, pas sur les dictionnaires d'entree.
        results = [await self.insert_one(doc) for doc in docs]
        return SimpleNamespace(inserted_ids=[r.inserted_id for r in results])

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any]
    ) -> SimpleNamespace:
        doc = await self.find_one(query)
        if doc is not None:
            for key, value in update.get("$set", {}).items():
                if "." in key:
                    head, tail = key.split(".", 1)
                    doc.setdefault(head, {})[tail] = value
                else:
                    doc[key] = value
        return SimpleNamespace(modified_count=1 if doc else 0)

    async def delete_many(self, query: dict[str, Any]) -> SimpleNamespace:
        before = len(self.docs)
        self.docs = [d for d in self.docs if not self._matches(d, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))

    @staticmethod
    def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
        for key, expected in query.items():
            value: Any = doc
            for part in key.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if isinstance(expected, dict) and "$lt" in expected:
                if not (isinstance(value, int) and value < expected["$lt"]):
                    return False
            elif value != expected:
                return False
        return True


class _FakeDeps:
    def __init__(self) -> None:
        self.documents = _FakeCollection()
        self.chunks = _FakeCollection()
        self.settings = SimpleNamespace(
            embedding_model="text-embedding-3-small",
            embedding_dimension=1536,
            chunk_max_tokens=512,
        )


def _meta(file_id: str = "f1") -> DriveFileMeta:
    return DriveFileMeta(
        file_id=file_id,
        name="menu.md",
        mime_type="text/markdown",
        modified_time=datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    """Remplace Docling et OpenAI par des doublures."""
    from src.ingestion.chunker import TextChunk

    def fake_chunk_file(path: Path, max_tokens: int = 512) -> list[TextChunk]:
        return [
            TextChunk(
                content="Poulet Yassa - 8 500 FCFA", chunk_index=0, token_count=6
            ),
            TextChunk(
                content="Poisson Braise - 11 000 FCFA", chunk_index=1, token_count=6
            ),
        ]

    async def fake_embed_texts(deps: Any, texts: list[str], batch_size: int = 100):
        return [[0.1] * 1536 for _ in texts]

    monkeypatch.setattr("src.ingestion.ingest.chunk_file", fake_chunk_file)
    monkeypatch.setattr("src.ingestion.ingest.embed_texts", fake_embed_texts)


@pytest.mark.unit
async def test_ingest_creates_document_and_chunks(patched, tmp_path: Path) -> None:
    """Une premiere ingestion cree le document et ses chunks."""
    deps = _FakeDeps()
    path = tmp_path / "menu.md"
    path.write_text("x", encoding="utf-8")

    written = await ingest_document(deps, path, _meta(), content_hash="h1")

    assert written == 2
    assert len(deps.documents.docs) == 1
    assert deps.documents.docs[0]["version"] == 1
    assert deps.documents.docs[0]["chunk_count"] == 2
    assert len(deps.chunks.docs) == 2
    assert all(c["version"] == 1 for c in deps.chunks.docs)


@pytest.mark.unit
async def test_reingest_replaces_chunks_and_bumps_version(
    patched, tmp_path: Path
) -> None:
    """Une re-ingestion incremente la version et ne laisse que les nouveaux chunks."""
    deps = _FakeDeps()
    path = tmp_path / "menu.md"
    path.write_text("x", encoding="utf-8")

    await ingest_document(deps, path, _meta(), content_hash="h1")
    await ingest_document(deps, path, _meta(), content_hash="h2")

    assert len(deps.documents.docs) == 1
    assert deps.documents.docs[0]["version"] == 2
    assert deps.documents.docs[0]["drive"]["content_hash"] == "h2"
    assert len(deps.chunks.docs) == 2
    assert all(c["version"] == 2 for c in deps.chunks.docs)


@pytest.mark.unit
async def test_delete_document_removes_document_and_chunks(
    patched, tmp_path: Path
) -> None:
    """La suppression retire le document et tous ses chunks."""
    deps = _FakeDeps()
    path = tmp_path / "menu.md"
    path.write_text("x", encoding="utf-8")
    await ingest_document(deps, path, _meta(), content_hash="h1")

    await delete_document(deps, "f1")

    assert deps.documents.docs == []
    assert deps.chunks.docs == []


@pytest.mark.unit
async def test_touch_document_updates_timestamps_only(patched, tmp_path: Path) -> None:
    """touch_document met a jour les horodatages sans toucher aux chunks."""
    deps = _FakeDeps()
    path = tmp_path / "menu.md"
    path.write_text("x", encoding="utf-8")
    await ingest_document(deps, path, _meta(), content_hash="h1")
    new_time = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)

    await touch_document(deps, "f1", new_time)

    assert deps.documents.docs[0]["drive"]["modified_time"] == new_time
    assert deps.documents.docs[0]["version"] == 1
    assert len(deps.chunks.docs) == 2
