import sys
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from api.ask_pipeline import AskPipeline
from api.rag_retriever import RagResult
from api.schemas import AskRequest


def make_generate_data(
    answer: str = "Odpowiedź.",
    wall_time: float = 5.0,
    eval_count: int | None = 50,
    eval_ns: int = 5_000_000_000,
    prompt_eval_ns: int = 500_000_000,
) -> dict:
    return {
        "response": answer,
        "model": "bielik-11b",
        "_wall_time": wall_time,
        "eval_count": eval_count,
        "eval_duration": eval_ns,
        "prompt_eval_duration": prompt_eval_ns,
    }


def make_pipeline(generate_data: dict, rag_result=None, route_result=None) -> AskPipeline:
    ollama = AsyncMock()
    ollama.generate = AsyncMock(return_value=generate_data)

    rag_retriever = AsyncMock()
    rag_retriever.store = MagicMock()
    rag_retriever.store.scroll_source_labels = MagicMock(return_value=["ORNO OR-WE-520"])
    rag_retriever.retrieve = AsyncMock(return_value=rag_result)

    query_router = AsyncMock()
    query_router.route = AsyncMock(return_value=route_result)

    return AskPipeline(ollama=ollama, rag_retriever=rag_retriever, query_router=query_router)


def run(coro):
    return asyncio.run(coro)


def req(**kwargs) -> AskRequest:
    defaults = {"prompt": "Jakie napięcie?", "rag": False, "query_router": False}
    return AskRequest(**{**defaults, **kwargs})


# ── RAG wyłączony ─────────────────────────────────────────────────────────────

def test_rag_disabled_skips_retrieve():
    """rag=False — retrieve nie jest wywoływane."""
    pipeline = make_pipeline(make_generate_data())
    run(pipeline.run(req(rag=False)))
    pipeline.rag_retriever.retrieve.assert_not_called()

def test_rag_disabled_uses_original_prompt():
    """rag=False — generate dostaje oryginalny prompt użytkownika."""
    pipeline = make_pipeline(make_generate_data())
    run(pipeline.run(req(prompt="moje pytanie", rag=False)))
    call_args = pipeline.ollama.generate.call_args
    assert call_args[0][0] == "moje pytanie"


# ── RAG włączony ──────────────────────────────────────────────────────────────

def test_rag_enabled_no_results_uses_original_prompt():
    """rag=True, brak trafień — generate dostaje oryginalny prompt."""
    pipeline = make_pipeline(make_generate_data(), rag_result=None)
    run(pipeline.run(req(prompt="moje pytanie", rag=True)))
    call_args = pipeline.ollama.generate.call_args
    assert call_args[0][0] == "moje pytanie"

def test_rag_enabled_with_results_uses_rag_prompt():
    """rag=True, są trafienia — generate dostaje prompt z kontekstem RAG."""
    rag_result = RagResult(
        prompt="Kontekst:\n[Fragment 1]\nTekst\n\nPytanie: moje pytanie",
        chunks=[{"index": 1, "score": 0.9, "source_label": "Dev", "sheet": "S", "text": "Tekst"}],
    )
    pipeline = make_pipeline(make_generate_data(), rag_result=rag_result)
    run(pipeline.run(req(prompt="moje pytanie", rag=True)))
    call_args = pipeline.ollama.generate.call_args
    assert "Kontekst:" in call_args[0][0]

def test_rag_chunks_in_response():
    """rag=True z trafieniami — odpowiedź zawiera rag_chunks i rag_chunks_used."""
    rag_result = RagResult(
        prompt="Kontekst:\nTekst\n\nPytanie: pytanie",
        chunks=[{"index": 1, "score": 0.9, "source_label": "Dev", "sheet": "S", "text": "Tekst"}],
    )
    pipeline = make_pipeline(make_generate_data(), rag_result=rag_result)
    response = run(pipeline.run(req(rag=True)))
    assert response.rag_chunks_used == 1
    assert len(response.rag_chunks) == 1


# ── query router ──────────────────────────────────────────────────────────────

def test_query_router_disabled_skips_route():
    """query_router=False — route nie jest wywoływane."""
    pipeline = make_pipeline(make_generate_data())
    run(pipeline.run(req(rag=True, query_router=False)))
    pipeline.query_router.route.assert_not_called()

def test_query_router_enabled_calls_route():
    """query_router=True — route jest wywoływane z promptem i listą etykiet."""
    pipeline = make_pipeline(make_generate_data(), route_result="ORNO OR-WE-520")
    run(pipeline.run(req(rag=True, query_router=True)))
    pipeline.query_router.route.assert_called_once()


# ── metryki ───────────────────────────────────────────────────────────────────

def test_response_tps_calculated():
    """tokens_per_second = eval_count / (eval_duration / 1e9)."""
    data = make_generate_data(eval_count=50, eval_ns=5_000_000_000)
    pipeline = make_pipeline(data)
    response = run(pipeline.run(req()))
    assert response.tokens_per_second == 10.0

def test_response_tps_none_when_no_eval_count():
    """tokens_per_second jest None gdy eval_count brak."""
    data = make_generate_data(eval_count=None)
    pipeline = make_pipeline(data)
    response = run(pipeline.run(req()))
    assert response.tokens_per_second is None

def test_response_tps_none_when_eval_ns_zero():
    """tokens_per_second jest None gdy eval_duration = 0."""
    data = make_generate_data(eval_count=50, eval_ns=0)
    pipeline = make_pipeline(data)
    response = run(pipeline.run(req()))
    assert response.tokens_per_second is None

def test_response_fields():
    """Odpowiedź zawiera poprawne pola: answer, model, time_total_s."""
    data = make_generate_data(answer="Napięcie wynosi 230V.", wall_time=3.5)
    pipeline = make_pipeline(data)
    response = run(pipeline.run(req()))
    assert response.answer == "Napięcie wynosi 230V."
    assert response.model == "bielik-11b"
    assert response.time_total_s == 3.5
