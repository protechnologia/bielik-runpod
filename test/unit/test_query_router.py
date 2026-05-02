import asyncio
from unittest.mock import AsyncMock

from api.query_router import QueryRouter


LABELS = ["ORNO OR-WE-520", "EASTRON SDM630", "NIBE F1255"]


def make_router(response: str) -> QueryRouter:
    ollama = AsyncMock()
    ollama.generate = AsyncMock(return_value={"response": response})
    return QueryRouter(ollama)


def run(coro):
    return asyncio.run(coro)


# ── brak source_labels ────────────────────────────────────────────────────────

def test_empty_labels_returns_none():
    """Pusta lista urządzeń — router zwraca None bez wywołania modelu."""
    ollama = AsyncMock()
    router = QueryRouter(ollama)
    result = run(router.route("napięcie L1", []))
    assert result is None
    ollama.generate.assert_not_called()


# ── odpowiedź "brak" ──────────────────────────────────────────────────────────

def test_brak_returns_none():
    result = run(make_router("brak").route("jak działa pompa ciepła?", LABELS))
    assert result is None

def test_brak_case_insensitive():
    result = run(make_router("BRAK").route("coś niezwiązanego", LABELS))
    assert result is None

def test_empty_response_returns_none():
    result = run(make_router("").route("napięcie", LABELS))
    assert result is None

def test_whitespace_response_returns_none():
    result = run(make_router("   ").route("napięcie", LABELS))
    assert result is None


# ── exact match ───────────────────────────────────────────────────────────────

def test_exact_match():
    result = run(make_router("ORNO OR-WE-520").route("napięcie L1", LABELS))
    assert result == "ORNO OR-WE-520"

def test_exact_match_case_insensitive():
    result = run(make_router("orno or-we-520").route("napięcie L1", LABELS))
    assert result == "ORNO OR-WE-520"


# ── substring match ───────────────────────────────────────────────────────────

def test_substring_model_shortened():
    """Model zwrócił skróconą nazwę — dopasowanie przez substring."""
    result = run(make_router("OR-WE-520").route("rejestr mocy OR-WE-520", LABELS))
    assert result == "ORNO OR-WE-520"

def test_substring_model_extended():
    """Model dodał słowo — label zawarty w odpowiedzi modelu."""
    result = run(make_router("urządzenie EASTRON SDM630 v2").route("moc SDM630", LABELS))
    assert result == "EASTRON SDM630"

def test_no_match_returns_none():
    """Odpowiedź modelu nie pasuje do żadnego labela."""
    result = run(make_router("Siemens SENTRON").route("prąd fazowy", LABELS))
    assert result is None
