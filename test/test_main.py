import sys
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from io import BytesIO

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from fastapi.testclient import TestClient
from api.main import (
    app,
    get_ask_pipeline,
    get_ollama,
    get_store,
    get_xlsx_ingester,
)
from api.schemas import AskResponse, IngestXlsxResponse, InspectXlsxResponse, ChunkInfo


def make_ask_response(**kwargs) -> AskResponse:
    defaults = dict(
        answer="Napięcie wynosi 230V.",
        model="bielik-11b",
        time_total_s=1.0,
        time_to_first_token_s=0.1,
        tokens_generated=10,
        tokens_per_second=10.0,
        rag_chunks_used=0,
        rag_chunks=[],
    )
    return AskResponse(**{**defaults, **kwargs})


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


# ── /health ───────────────────────────────────────────────────────────────────

def test_health_ok():
    """Zwraca status=ok gdy ollama i store działają."""
    mock_ollama = AsyncMock()
    mock_ollama.check = AsyncMock(return_value={"reachable": True})
    mock_store = MagicMock()
    mock_store.list_collections.return_value = [{"name": "documents", "vectors_count": 5}]

    app.dependency_overrides[get_ollama] = lambda: mock_ollama
    app.dependency_overrides[get_store]  = lambda: mock_store

    response = TestClient(app).get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["qdrant"]["collections"] == ["documents"]


def test_health_error_when_ollama_raises():
    """Zwraca status=error (nie 500) gdy ollama rzuca wyjątek."""
    mock_ollama = AsyncMock()
    mock_ollama.check = AsyncMock(side_effect=ConnectionError("timeout"))
    mock_store = MagicMock()
    mock_store.list_collections.return_value = []

    app.dependency_overrides[get_ollama] = lambda: mock_ollama
    app.dependency_overrides[get_store]  = lambda: mock_store

    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "error"


# ── /pull ─────────────────────────────────────────────────────────────────────

def test_pull_model():
    """Przekazuje model do ollama.pull_model i zwraca wynik."""
    mock_ollama = AsyncMock()
    mock_ollama.pull_model = AsyncMock(return_value={"status": "pulled", "model": "bielik-11b"})
    app.dependency_overrides[get_ollama] = lambda: mock_ollama

    response = TestClient(app).post("/pull", json={"model": "bielik-11b"})
    assert response.status_code == 200
    assert response.json()["status"] == "pulled"
    mock_ollama.pull_model.assert_awaited_once_with("bielik-11b")


# ── /ingest/xlsx ──────────────────────────────────────────────────────────────

def test_ingest_xlsx_returns_response_model():
    """Zwraca 200 z polem ingested gdy ingester działa."""
    mock_ingester = AsyncMock()
    mock_ingester.ingest = AsyncMock(return_value=IngestXlsxResponse(
        filename="test.xlsx", sheets=1, chunks=2, ingested=2, collection="documents",
    ))
    app.dependency_overrides[get_xlsx_ingester] = lambda: mock_ingester

    response = TestClient(app).post(
        "/ingest/xlsx",
        data={"source_label": "ORNO OR-WE-516", "collection": "documents"},
        files={"file": ("test.xlsx", BytesIO(b"fake"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200
    assert response.json()["ingested"] == 2


def test_ingest_xlsx_missing_source_label_returns_422():
    """Brak wymaganego pola source_label zwraca 422."""
    response = TestClient(app).post(
        "/ingest/xlsx",
        data={"collection": "documents"},
        files={"file": ("test.xlsx", BytesIO(b"fake"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 422


# ── /inspect/xlsx ─────────────────────────────────────────────────────────────

def test_inspect_xlsx_returns_chunks():
    """Zwraca 200 z listą chunków."""
    mock_ingester = AsyncMock()
    mock_ingester.inspect = AsyncMock(return_value=InspectXlsxResponse(
        filename="test.xlsx",
        sheets=1,
        chunks=1,
        items=[ChunkInfo(index=1, sheet="Dane", chunk=1, text="fragment", char_count=8, word_count=1)],
    ))
    app.dependency_overrides[get_xlsx_ingester] = lambda: mock_ingester

    response = TestClient(app).post(
        "/inspect/xlsx",
        data={"source_label": "ORNO OR-WE-516"},
        files={"file": ("test.xlsx", BytesIO(b"fake"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1


# ── /ask ──────────────────────────────────────────────────────────────────────

def test_ask_returns_answer():
    """Zwraca 200 z polem answer."""
    mock_pipeline = AsyncMock()
    mock_pipeline.run = AsyncMock(return_value=make_ask_response())
    app.dependency_overrides[get_ask_pipeline] = lambda: mock_pipeline

    response = TestClient(app).post("/ask", json={"prompt": "Jakie napięcie?"})
    assert response.status_code == 200
    assert response.json()["answer"] == "Napięcie wynosi 230V."


def test_ask_missing_prompt_returns_422():
    """Brak wymaganego pola prompt zwraca 422."""
    response = TestClient(app).post("/ask", json={})
    assert response.status_code == 422


# ── /models ───────────────────────────────────────────────────────────────────

def test_list_models():
    """Zwraca 200 z listą modeli z Ollamy."""
    mock_ollama = AsyncMock()
    mock_ollama.list_models = AsyncMock(return_value={"models": [{"name": "bielik-11b"}]})
    app.dependency_overrides[get_ollama] = lambda: mock_ollama

    response = TestClient(app).get("/models")
    assert response.status_code == 200
    assert response.json()["models"][0]["name"] == "bielik-11b"


# ── /collections ──────────────────────────────────────────────────────────────

def test_list_collections():
    """Zwraca 200 z listą kolekcji."""
    mock_store = MagicMock()
    mock_store.list_collections.return_value = [{"name": "documents", "vectors_count": 10}]
    app.dependency_overrides[get_store] = lambda: mock_store

    response = TestClient(app).get("/collections")
    assert response.status_code == 200
    assert response.json()[0]["name"] == "documents"


def test_delete_collection():
    """Zwraca 200 z polem deleted równym nazwie kolekcji."""
    mock_store = MagicMock()
    app.dependency_overrides[get_store] = lambda: mock_store

    response = TestClient(app).delete("/collections/documents")
    assert response.status_code == 200
    assert response.json() == {"deleted": "documents"}
    mock_store.delete_collection.assert_called_once_with("documents")
