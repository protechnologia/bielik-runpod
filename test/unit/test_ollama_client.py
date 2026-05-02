import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from api.ollama_client import OllamaClient


def make_client() -> OllamaClient:
    return OllamaClient(
        base_url="http://localhost:11434",
        model="bielik-11b",
        embed_model="nomic-embed-text",
    )


def mock_http(status: int, body: dict, method: str = "post") -> MagicMock:
    """Zwraca spatchowany httpx.AsyncClient zwracający podany status i body."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    resp.text = str(body)

    mock_client = AsyncMock()
    setattr(mock_client, method, AsyncMock(return_value=resp))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def run(coro):
    return asyncio.run(coro)


# ── embed ─────────────────────────────────────────────────────────────────────

def test_embed_returns_first_embedding():
    """embed zwraca wewnętrzną listę embeddings[0] z odpowiedzi Ollamy."""
    body = {"embeddings": [[0.1, 0.2, 0.3]]}
    with patch("httpx.AsyncClient", return_value=mock_http(200, body)):
        result = run(make_client().embed("test"))
    assert result == [0.1, 0.2, 0.3]

def test_embed_raises_502_on_error():
    """embed rzuca HTTPException(502) gdy Ollama zwróci status != 200."""
    with patch("httpx.AsyncClient", return_value=mock_http(500, {})):
        with pytest.raises(HTTPException) as exc_info:
            run(make_client().embed("test"))
    assert exc_info.value.status_code == 502


# ── generate ──────────────────────────────────────────────────────────────────

def test_generate_extracts_response_content():
    """generate dodaje klucz 'response' z message.content odpowiedzi Ollamy."""
    body = {"message": {"role": "assistant", "content": "Napięcie wynosi 230V."}, "model": "bielik-11b"}
    with patch("httpx.AsyncClient", return_value=mock_http(200, body)):
        data = run(make_client().generate("pytanie", max_tokens=100, temperature=0.1))
    assert data["response"] == "Napięcie wynosi 230V."

def test_generate_adds_wall_time():
    """generate dodaje klucz '_wall_time' z czasem wykonania requestu."""
    body = {"message": {"content": ""}, "model": "bielik-11b"}
    with patch("httpx.AsyncClient", return_value=mock_http(200, body)):
        data = run(make_client().generate("pytanie", max_tokens=100, temperature=0.1))
    assert "_wall_time" in data
    assert isinstance(data["_wall_time"], float)

def test_generate_raises_502_on_error():
    """generate rzuca HTTPException(502) gdy Ollama zwróci status != 200."""
    with patch("httpx.AsyncClient", return_value=mock_http(503, {})):
        with pytest.raises(HTTPException) as exc_info:
            run(make_client().generate("pytanie", max_tokens=100, temperature=0.1))
    assert exc_info.value.status_code == 502

def test_generate_missing_message_returns_empty_response():
    """Brak klucza 'message' w odpowiedzi — response to pusty string."""
    body = {"model": "bielik-11b"}
    with patch("httpx.AsyncClient", return_value=mock_http(200, body)):
        data = run(make_client().generate("pytanie", max_tokens=100, temperature=0.1))
    assert data["response"] == ""


# ── check ─────────────────────────────────────────────────────────────────────

# ── list_models ───────────────────────────────────────────────────────────────

def test_list_models_returns_response():
    """list_models zwraca surową odpowiedź Ollamy."""
    body = {"models": [{"name": "bielik-11b:Q8_0"}]}
    with patch("httpx.AsyncClient", return_value=mock_http(200, body, method="get")):
        result = run(make_client().list_models())
    assert result == body

def test_list_models_raises_502_on_error():
    """list_models rzuca HTTPException(502) gdy Ollama zwróci status != 200."""
    with patch("httpx.AsyncClient", return_value=mock_http(503, {}, method="get")):
        with pytest.raises(HTTPException) as exc_info:
            run(make_client().list_models())
    assert exc_info.value.status_code == 502


# ── pull_model ────────────────────────────────────────────────────────────────

def test_pull_model_returns_status():
    """pull_model zwraca {'status': 'pulled', 'model': ...} po sukcesie."""
    with patch("httpx.AsyncClient", return_value=mock_http(200, {})):
        result = run(make_client().pull_model("nomic-embed-text"))
    assert result == {"status": "pulled", "model": "nomic-embed-text"}

def test_pull_model_uses_default_when_none():
    """pull_model bez argumentu używa self.model."""
    with patch("httpx.AsyncClient", return_value=mock_http(200, {})):
        result = run(make_client().pull_model())
    assert result["model"] == "bielik-11b"

def test_pull_model_raises_502_on_error():
    """pull_model rzuca HTTPException(502) gdy Ollama zwróci status != 200."""
    with patch("httpx.AsyncClient", return_value=mock_http(500, {})):
        with pytest.raises(HTTPException) as exc_info:
            run(make_client().pull_model("nomic-embed-text"))
    assert exc_info.value.status_code == 502


# ── check ─────────────────────────────────────────────────────────────────────

def test_check_model_ready_when_present():
    """check zwraca model_ready=True gdy model jest na liście załadowanych."""
    body = {"models": [{"name": "bielik-11b:Q8_0"}, {"name": "nomic-embed-text:latest"}]}
    with patch("httpx.AsyncClient", return_value=mock_http(200, body, method="get")):
        result = run(make_client().check())
    assert result["model_ready"] is True
    assert result["embed_ready"] is True

def test_check_model_not_ready_when_absent():
    """check zwraca model_ready=False gdy modelu nie ma na liście."""
    body = {"models": [{"name": "llama3:latest"}]}
    with patch("httpx.AsyncClient", return_value=mock_http(200, body, method="get")):
        result = run(make_client().check())
    assert result["model_ready"] is False
    assert result["embed_ready"] is False

def test_check_returns_available_models():
    """check zwraca listę nazw załadowanych modeli."""
    body = {"models": [{"name": "bielik-11b:Q8_0"}, {"name": "nomic-embed-text:latest"}]}
    with patch("httpx.AsyncClient", return_value=mock_http(200, body, method="get")):
        result = run(make_client().check())
    assert result["available_models"] == ["bielik-11b:Q8_0", "nomic-embed-text:latest"]
