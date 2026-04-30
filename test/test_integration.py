import sys
import asyncio
import os
import uuid
import pytest
from pathlib import Path
from io import BytesIO
from unittest.mock import MagicMock, AsyncMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

import openpyxl
from api.ollama_client import OllamaClient
from api.qdrant_store import QdrantStore
from api.xlsx_ingester import XlsxIngester
from api.rag_retriever import RagRetriever
from api.config import VECTOR_SIZE, EMBED_MODEL

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")


def run(coro):
    return asyncio.run(coro)


def col() -> str:
    """Unikalna nazwa kolekcji dla każdego testu."""
    return f"test_{uuid.uuid4().hex[:8]}"


def make_ollama() -> OllamaClient:
    return OllamaClient(base_url=OLLAMA_URL, model="bielik-11b", embed_model=EMBED_MODEL)


def make_store(tmp_path) -> QdrantStore:
    return QdrantStore(path=str(tmp_path / "qdrant"), vector_size=VECTOR_SIZE)


def make_xlsx(rows: list[dict]) -> bytes:
    """Tworzy w pamięci plik XLSX z jednym arkuszem i zwraca bajty."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Dane techniczne"
    ws.append(list(rows[0].keys()))
    for row in rows:
        ws.append(list(row.values()))
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_upload_file(content: bytes, filename: str = "test.xlsx") -> MagicMock:
    f = MagicMock()
    f.filename = filename
    f.read = AsyncMock(return_value=content)
    return f


# ── OllamaClient ──────────────────────────────────────────────────────────────

def test_embed_returns_768_dimensions():
    """embed() zwraca wektor o 768 wymiarach (nomic-embed-text)."""
    ollama = make_ollama()
    vector = run(ollama.embed("Napięcie znamionowe licznika energii elektrycznej."))
    assert len(vector) == VECTOR_SIZE
    assert all(isinstance(v, float) for v in vector)


def test_check_embed_ready():
    """check() raportuje reachable=True i embed_ready=True gdy nomic-embed-text jest załadowany."""
    ollama = make_ollama()
    result = run(ollama.check())
    assert result["reachable"] is True
    assert result["embed_ready"] is True


# ── QdrantStore ───────────────────────────────────────────────────────────────

def test_qdrant_create_and_list_collection(tmp_path):
    """ensure_collection tworzy kolekcję widoczną w list_collections."""
    store = make_store(tmp_path)
    name = col()
    try:
        store.ensure_collection(name)
        names = [c["name"] for c in store.list_collections()]
        assert name in names
    finally:
        store.delete_collection(name)


def test_qdrant_upsert_and_search(tmp_path):
    """Punkt wstawiony przez upsert jest zwracany przez search z tym samym wektorem."""
    store = make_store(tmp_path)
    ollama = make_ollama()
    name = col()
    text = "Napięcie znamionowe: 3x230/400V, częstotliwość: 50Hz."
    try:
        store.ensure_collection(name)
        vector = run(ollama.embed(text))
        store.upsert(name, [{
            "id": str(uuid.uuid4()),
            "vector": vector,
            "payload": {"text": text, "source_label": "ORNO OR-WE-520", "sheet": "Dane"},
        }])
        hits = store.search(name, vector, top_k=1, score_threshold=0.5)
        assert len(hits) == 1
        assert hits[0]["payload"]["text"] == text
    finally:
        store.delete_collection(name)


def test_qdrant_scroll_source_labels(tmp_path):
    """scroll_source_labels zwraca etykietę wstawioną przez upsert."""
    store = make_store(tmp_path)
    ollama = make_ollama()
    name = col()
    try:
        store.ensure_collection(name)
        vector = run(ollama.embed("test"))
        store.upsert(name, [{
            "id": str(uuid.uuid4()),
            "vector": vector,
            "payload": {"text": "test", "source_label": "EASTRON SDM630", "sheet": "S"},
        }])
        labels = store.scroll_source_labels(name)
        assert "EASTRON SDM630" in labels
    finally:
        store.delete_collection(name)


# ── XlsxIngester ─────────────────────────────────────────────────────────────

def test_ingest_vectors_count_matches_chunks(tmp_path):
    """Po ingest() vectors_count w Qdrant jest równy liczbie zwróconych chunków."""
    store = make_store(tmp_path)
    ollama = make_ollama()
    ingester = XlsxIngester(store=store, ollama=ollama)
    name = col()
    xlsx_bytes = make_xlsx([
        {"Parametr": "Napięcie", "Wartość": "3x230/400V"},
        {"Parametr": "Prąd", "Wartość": "5A"},
        {"Parametr": "Częstotliwość", "Wartość": "50Hz"},
    ])
    try:
        result = run(ingester.ingest(
            make_upload_file(xlsx_bytes),
            source_label="ORNO OR-WE-520",
            collection=name,
            rows_per_chunk=50,
        ))
        assert result.ingested > 0
        col_info = next(c for c in store.list_collections() if c["name"] == name)
        assert col_info["vectors_count"] == result.ingested
    finally:
        store.delete_collection(name)


def test_ingest_source_label_visible_in_scroll(tmp_path):
    """Po ingest() scroll_source_labels zwraca wstawiony source_label."""
    store = make_store(tmp_path)
    ollama = make_ollama()
    ingester = XlsxIngester(store=store, ollama=ollama)
    name = col()
    xlsx_bytes = make_xlsx([{"Parametr": "Napięcie", "Wartość": "230V"}])
    try:
        run(ingester.ingest(
            make_upload_file(xlsx_bytes),
            source_label="EASTRON SDM630",
            collection=name,
            rows_per_chunk=50,
        ))
        assert "EASTRON SDM630" in store.scroll_source_labels(name)
    finally:
        store.delete_collection(name)


# ── RagRetriever ──────────────────────────────────────────────────────────────

def test_retrieve_returns_chunk_after_ingest(tmp_path):
    """retrieve() zwraca co najmniej jeden chunk po ingestion powiązanego dokumentu."""
    store = make_store(tmp_path)
    ollama = make_ollama()
    ingester = XlsxIngester(store=store, ollama=ollama)
    retriever = RagRetriever(store=store, ollama=ollama)
    name = col()
    xlsx_bytes = make_xlsx([
        {"Parametr": "Napięcie znamionowe", "Wartość": "3x230/400V"},
        {"Parametr": "Prąd znamionowy", "Wartość": "5A"},
        {"Parametr": "Klasa dokładności", "Wartość": "1"},
    ])
    try:
        run(ingester.ingest(
            make_upload_file(xlsx_bytes),
            source_label="ORNO OR-WE-520",
            collection=name,
            rows_per_chunk=50,
        ))
        result = run(retriever.retrieve(
            prompt="Jakie jest napięcie znamionowe?",
            collection=name,
            top_k=3,
            score_threshold=0.3,
        ))
        assert result is not None
        assert len(result.chunks) >= 1
    finally:
        store.delete_collection(name)


def test_retrieve_wrong_source_label_returns_none(tmp_path):
    """retrieve() z source_label_filter na nieistniejącą etykietę zwraca None."""
    store = make_store(tmp_path)
    ollama = make_ollama()
    ingester = XlsxIngester(store=store, ollama=ollama)
    retriever = RagRetriever(store=store, ollama=ollama)
    name = col()
    xlsx_bytes = make_xlsx([{"Parametr": "Napięcie", "Wartość": "230V"}])
    try:
        run(ingester.ingest(
            make_upload_file(xlsx_bytes),
            source_label="ORNO OR-WE-520",
            collection=name,
            rows_per_chunk=50,
        ))
        result = run(retriever.retrieve(
            prompt="napięcie",
            collection=name,
            top_k=3,
            score_threshold=0.0,
            source_label_filter="NIEISTNIEJĄCE URZĄDZENIE XYZ",
        ))
        assert result is None
    finally:
        store.delete_collection(name)
