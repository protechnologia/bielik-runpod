import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from api.qdrant_store import QdrantStore


def make_store() -> QdrantStore:
    # Patchujemy nazwę w module qdrant_store (nie w qdrant_client), bo qdrant_store.py
    # zaimportował QdrantClient przez "from qdrant_client import QdrantClient" —
    # podmiana w źródle nie zmieni już przypisanej nazwy lokalnej.
    with patch("api.qdrant_store.QdrantClient"):
        store = QdrantStore(path="/tmp/qdrant", vector_size=768)
    return store


def make_point(label: str) -> MagicMock:
    """Zwraca mock punktu Qdrant z polem source_label w payload."""
    p = MagicMock()
    p.payload = {"source_label": label}
    return p


# ── ensure_collection ─────────────────────────────────────────────────────────

def test_ensure_collection_creates_when_missing():
    """Kolekcja nie istnieje — create_collection powinno być wywołane."""
    store = make_store()
    store.client.collection_exists.return_value = False
    store.ensure_collection("documents")
    store.client.create_collection.assert_called_once()

def test_ensure_collection_skips_when_exists():
    """Kolekcja już istnieje — create_collection nie powinno być wywołane."""
    store = make_store()
    store.client.collection_exists.return_value = True
    store.ensure_collection("documents")
    store.client.create_collection.assert_not_called()

def test_ensure_collection_uses_correct_name():
    """create_collection jest wołane z podaną nazwą kolekcji."""
    store = make_store()
    store.client.collection_exists.return_value = False
    store.ensure_collection("moja-kolekcja")
    args, kwargs = store.client.create_collection.call_args
    assert kwargs.get("collection_name") == "moja-kolekcja"


# ── scroll_source_labels ──────────────────────────────────────────────────────

def test_scroll_source_labels_returns_sorted_unique():
    """Zwraca posortowaną listę unikalnych source_label."""
    store = make_store()
    store.client.scroll.return_value = (
        [make_point("ORNO OR-WE-520"), make_point("EASTRON SDM630"), make_point("ORNO OR-WE-520")],
        None,
    )
    result = store.scroll_source_labels("documents")
    assert result == ["EASTRON SDM630", "ORNO OR-WE-520"]

def test_scroll_source_labels_handles_pagination():
    """Paginacja — scroll wołany dwukrotnie gdy pierwszy offset != None."""
    store = make_store()
    store.client.scroll.side_effect = [
        ([make_point("ORNO OR-WE-520")], "page2"),
        ([make_point("EASTRON SDM630")], None),
    ]
    result = store.scroll_source_labels("documents")
    assert store.client.scroll.call_count == 2
    assert result == ["EASTRON SDM630", "ORNO OR-WE-520"]

def test_scroll_source_labels_skips_missing_payload():
    """Punkt bez source_label w payload jest pomijany."""
    store = make_store()
    p = MagicMock()
    p.payload = {}
    store.client.scroll.return_value = ([p], None)
    result = store.scroll_source_labels("documents")
    assert result == []


# ── list_collections ──────────────────────────────────────────────────────────

def test_list_collections_returns_name_and_count():
    """list_collections zwraca listę słowników z name i vectors_count."""
    store = make_store()
    col = MagicMock()
    col.name = "documents"
    store.client.get_collections.return_value.collections = [col]
    info = MagicMock()
    info.vectors_count = 42
    store.client.get_collection.return_value = info

    result = store.list_collections()
    assert result == [{"name": "documents", "vectors_count": 42}]

def test_list_collections_empty():
    """Brak kolekcji — zwraca pustą listę."""
    store = make_store()
    store.client.get_collections.return_value.collections = []
    assert store.list_collections() == []


# ── upsert ────────────────────────────────────────────────────────────────────

def test_upsert_calls_client_upsert():
    """upsert przekazuje punkty do client.upsert z poprawną nazwą kolekcji."""
    store = make_store()
    points = [{"id": "abc", "vector": [0.1, 0.2], "payload": {"text": "x"}}]
    store.upsert("documents", points)
    store.client.upsert.assert_called_once()
    args, kwargs = store.client.upsert.call_args
    assert kwargs.get("collection_name") == "documents"


# ── search ────────────────────────────────────────────────────────────────────

def test_search_returns_score_and_payload():
    """search zwraca listę słowników z polami score i payload."""
    store = make_store()
    hit = MagicMock()
    hit.score = 0.87
    hit.payload = {"text": "Fragment"}
    store.client.search.return_value = [hit]
    result = store.search("documents", [0.1, 0.2], top_k=3, score_threshold=0.3)
    assert result == [{"score": 0.87, "payload": {"text": "Fragment"}}]

def test_search_with_source_label_passes_filter():
    """search z source_label ustawia query_filter w wywołaniu client.search."""
    store = make_store()
    store.client.search.return_value = []
    store.search("documents", [0.1], top_k=3, score_threshold=0.3, source_label="ORNO")
    args, kwargs = store.client.search.call_args
    assert kwargs.get("query_filter") is not None

def test_search_without_source_label_no_filter():
    """search bez source_label przekazuje query_filter=None."""
    store = make_store()
    store.client.search.return_value = []
    store.search("documents", [0.1], top_k=3, score_threshold=0.3, source_label=None)
    args, kwargs = store.client.search.call_args
    assert kwargs.get("query_filter") is None


# ── delete_collection ─────────────────────────────────────────────────────────

def test_delete_collection_calls_client():
    """delete_collection przekazuje nazwę kolekcji do client.delete_collection."""
    store = make_store()
    store.delete_collection("documents")
    store.client.delete_collection.assert_called_once_with("documents")
