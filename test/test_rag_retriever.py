import sys
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from api.rag_retriever import RagRetriever, RagResult


def make_hit(text: str, score: float = 0.9, source_label: str = "Dev", sheet: str = "S") -> dict:
    return {"score": score, "payload": {"text": text, "source_label": source_label, "sheet": sheet}}


def make_retriever(hits: list[dict], bm25=None) -> RagRetriever:
    store = MagicMock()
    store.ensure_collection = MagicMock()
    store.search = MagicMock(return_value=hits)
    ollama = AsyncMock()
    ollama.embed = AsyncMock(return_value=[0.1] * 8)
    return RagRetriever(store=store, ollama=ollama, bm25=bm25)


def run(coro):
    return asyncio.run(coro)


# ── brak trafień ──────────────────────────────────────────────────────────────

def test_retrieve_returns_none_when_no_hits():
    """Brak wyników z Qdrant — retrieve zwraca None."""
    retriever = make_retriever(hits=[])
    result = run(retriever.retrieve("pytanie", "documents", top_k=3, score_threshold=0.3))
    assert result is None


# ── ścieżka bez BM25 ─────────────────────────────────────────────────────────

def test_retrieve_returns_rag_result():
    """Przy trafieniach retrieve zwraca RagResult."""
    retriever = make_retriever(hits=[make_hit("Fragment A")])
    result = run(retriever.retrieve("pytanie", "documents", top_k=3, score_threshold=0.3))
    assert isinstance(result, RagResult)

def test_retrieve_context_format():
    """Prompt zawiera prefiks 'Kontekst:' i 'Pytanie:' z oryginalnym zapytaniem."""
    retriever = make_retriever(hits=[make_hit("Fragment A")])
    result = run(retriever.retrieve("moje pytanie", "documents", top_k=3, score_threshold=0.3))
    assert result.prompt.startswith("Kontekst:")
    assert "Pytanie: moje pytanie" in result.prompt

def test_retrieve_fragment_labels_in_context():
    """Każdy fragment w kontekście oznaczony jest jako '[Fragment N]'."""
    hits = [make_hit("Tekst 1"), make_hit("Tekst 2")]
    retriever = make_retriever(hits=hits)
    result = run(retriever.retrieve("pytanie", "documents", top_k=3, score_threshold=0.3))
    assert "[Fragment 1]" in result.prompt
    assert "[Fragment 2]" in result.prompt

def test_retrieve_chunks_count_matches_hits():
    """Liczba chunków w wyniku odpowiada liczbie trafień."""
    hits = [make_hit("A"), make_hit("B"), make_hit("C")]
    retriever = make_retriever(hits=hits)
    result = run(retriever.retrieve("pytanie", "documents", top_k=3, score_threshold=0.3))
    assert len(result.chunks) == 3

def test_retrieve_chunk_fields():
    """Chunk zawiera poprawne pola: index, score, source_label, sheet, text."""
    retriever = make_retriever(hits=[make_hit("Treść", score=0.87, source_label="ORNO", sheet="Rejestry")])
    result = run(retriever.retrieve("pytanie", "documents", top_k=3, score_threshold=0.3))
    chunk = result.chunks[0]
    assert chunk.index == 1
    assert chunk.score == 0.87
    assert chunk.source_label == "ORNO"
    assert chunk.sheet == "Rejestry"
    assert chunk.text == "Treść"


# ── ścieżka z BM25 ───────────────────────────────────────────────────────────

def test_retrieve_with_bm25_limits_to_top_k():
    """Ścieżka BM25: z 4 kandydatów zwracane jest top_k=2 po rerankingu."""
    hits = [make_hit(f"Fragment {i}") for i in range(4)]
    bm25 = MagicMock()
    bm25.rerank.return_value = [(0, 2.1), (2, 1.5), (1, 0.8), (3, 0.0)]
    bm25.combine_scores.return_value = [(0, 0.03), (2, 0.02), (1, 0.01), (3, 0.005)]
    retriever = make_retriever(hits=hits, bm25=bm25)
    result = run(retriever.retrieve("pytanie", "documents", top_k=2, score_threshold=0.3, bm25_candidates=4))
    assert len(result.chunks) == 2

def test_retrieve_bm25_disabled_when_candidates_zero():
    """bm25_candidates=0 — BM25 nie jest używany nawet jeśli reranker jest ustawiony."""
    hits = [make_hit("Fragment")]
    bm25 = MagicMock()
    retriever = make_retriever(hits=hits, bm25=bm25)
    run(retriever.retrieve("pytanie", "documents", top_k=3, score_threshold=0.3, bm25_candidates=0))
    bm25.rerank.assert_not_called()

def test_retrieve_bm25_disabled_when_bm25_is_none():
    """bm25=None — BM25 nie jest używany nawet jeśli bm25_candidates > 0."""
    hits = [make_hit("Fragment")]
    retriever = make_retriever(hits=hits, bm25=None)
    result = run(retriever.retrieve("pytanie", "documents", top_k=3, score_threshold=0.3, bm25_candidates=10))
    assert len(result.chunks) == 1
