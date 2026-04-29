import io
import sys
import asyncio
import pytest
import openpyxl
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))  # bare imports inside xlsx_ingester.py (e.g. from xlsx_chunker import ...)

from fastapi import HTTPException
from api.xlsx_ingester import XlsxIngester, ParsedXlsx


def make_xlsx(sheets: dict[str, list[list]]) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet_name, rows in sheets.items():
        ws = wb.create_sheet(sheet_name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_upload_file(content: bytes, filename: str = "test.xlsx") -> MagicMock:
    # parse() używa z UploadFile tylko dwóch rzeczy: atrybutu .filename (sprawdzenie
    # rozszerzenia) i await .read() (odczyt bajtów). Zamiast tworzyć prawdziwy UploadFile
    # — który wymaga kontekstu HTTP — tworzymy mock z dokładnie tymi dwoma rzeczami.
    # parse() woła file.filename i await file.read(), dostaje to czego oczekuje
    # i nie wie, że f nie jest prawdziwym UploadFile (duck typing).
    f = MagicMock()
    f.filename = filename
    f.read = AsyncMock(return_value=content)
    return f


def make_ingester() -> XlsxIngester:
    store = MagicMock()
    ollama = AsyncMock()
    ollama.embed = AsyncMock(return_value=[0.1] * 8)
    return XlsxIngester(store=store, ollama=ollama)


def run(coro):
    return asyncio.run(coro)


# ── parse — walidacja rozszerzenia ────────────────────────────────────────────

def test_parse_rejects_non_xlsx():
    """Plik z rozszerzeniem innym niż .xlsx/.xlsm — HTTPException 400."""
    ingester = make_ingester()
    f = make_upload_file(b"", filename="dane.csv")
    with pytest.raises(HTTPException) as exc_info:
        run(ingester.parse(f, "Dev", 50))
    assert exc_info.value.status_code == 400

def test_parse_rejects_missing_filename():
    """Brak nazwy pliku (pusty string) — HTTPException 400."""
    ingester = make_ingester()
    f = make_upload_file(b"", filename="")
    with pytest.raises(HTTPException) as exc_info:
        run(ingester.parse(f, "Dev", 50))
    assert exc_info.value.status_code == 400

def test_parse_accepts_xlsm_extension():
    """Rozszerzenie .xlsm jest akceptowane tak samo jak .xlsx."""
    ingester = make_ingester()
    xlsx = make_xlsx({"S": [["A"], ["1"]]})
    f = make_upload_file(xlsx, filename="dane.xlsm")
    result = run(ingester.parse(f, "Dev", 50))
    assert isinstance(result, ParsedXlsx)


# ── parse — walidacja zawartości ──────────────────────────────────────────────

def test_parse_rejects_empty_file():
    """Plik bez wierszy danych (tylko nagłówki) — HTTPException 422."""
    ingester = make_ingester()
    xlsx = make_xlsx({"S": [["A", "B"]]})
    f = make_upload_file(xlsx)
    with pytest.raises(HTTPException) as exc_info:
        run(ingester.parse(f, "Dev", 50))
    assert exc_info.value.status_code == 422

def test_parse_rejects_invalid_bytes():
    """Losowe bajty zamiast XLSX — HTTPException 422."""
    ingester = make_ingester()
    f = make_upload_file(b"not an xlsx file")
    with pytest.raises(HTTPException) as exc_info:
        run(ingester.parse(f, "Dev", 50))
    assert exc_info.value.status_code == 422


# ── parse — poprawny wynik ────────────────────────────────────────────────────

def test_parse_returns_parsed_xlsx():
    """Poprawny plik — zwraca ParsedXlsx z chunkiami i nazwami arkuszy."""
    ingester = make_ingester()
    xlsx = make_xlsx({"Rejestry": [["A", "B"], ["1", "2"]]})
    f = make_upload_file(xlsx)
    result = run(ingester.parse(f, "ORNO OR-WE-520", 50))
    assert isinstance(result, ParsedXlsx)
    assert len(result.chunks) == 1
    assert result.sheet_names == ["Rejestry"]

def test_parse_sheet_names_unique_and_ordered():
    """sheet_names zachowuje kolejność i nie zawiera duplikatów."""
    ingester = make_ingester()
    xlsx = make_xlsx({
        "Odczyt": [["A"], ["1"], ["2"]],
        "Zapis":  [["B"], ["3"]],
    })
    f = make_upload_file(xlsx)
    result = run(ingester.parse(f, "Dev", 50))
    assert result.sheet_names == ["Odczyt", "Zapis"]


# ── inspect ───────────────────────────────────────────────────────────────────

def test_inspect_returns_correct_chunk_count():
    """60 wierszy przy rows_per_chunk=30 — inspect zwraca 2 chunki."""
    ingester = make_ingester()
    xlsx = make_xlsx({"S": [["A"]] + [["x"]] * 60})
    f = make_upload_file(xlsx)
    result = run(ingester.inspect(f, "Dev", 30))
    assert result.chunks == 2
    assert len(result.items) == 2

def test_inspect_chunk_info_fields():
    """ChunkInfo zawiera poprawne pola: sheet, chunk, char_count, word_count."""
    ingester = make_ingester()
    xlsx = make_xlsx({"Rejestry": [["Adres", "Nazwa"], ["0x0001", "U_L1"]]})
    f = make_upload_file(xlsx)
    result = run(ingester.inspect(f, "ORNO", 50))
    item = result.items[0]
    assert item.sheet == "Rejestry"
    assert item.chunk == 1
    assert item.char_count == len(item.text)
    assert item.word_count == len(item.text.split())


# ── ingest ────────────────────────────────────────────────────────────────────

def test_ingest_calls_ensure_collection():
    """ingest wywołuje ensure_collection z podaną nazwą kolekcji."""
    ingester = make_ingester()
    xlsx = make_xlsx({"S": [["A"], ["1"]]})
    f = make_upload_file(xlsx)
    run(ingester.ingest(f, "Dev", "documents", 50))
    ingester.store.ensure_collection.assert_called_once_with("documents")

def test_ingest_calls_embed_for_each_chunk():
    """ingest wywołuje ollama.embed dokładnie raz dla każdego chunku."""
    ingester = make_ingester()
    xlsx = make_xlsx({"S": [["A"]] + [["x"]] * 60})
    f = make_upload_file(xlsx)
    run(ingester.ingest(f, "Dev", "documents", 30))
    assert ingester.ollama.embed.call_count == 2

def test_ingest_returns_response_summary():
    """ingest zwraca IngestXlsxResponse z poprawnymi polami podsumowania."""
    ingester = make_ingester()
    xlsx = make_xlsx({
        "Odczyt": [["A"], ["1"]],
        "Zapis":  [["B"], ["2"]],
    })
    f = make_upload_file(xlsx, filename="liczniki.xlsx")
    result = run(ingester.ingest(f, "ORNO", "documents", 50))
    assert result.filename == "liczniki.xlsx"
    assert result.sheets == 2
    assert result.chunks == 2
    assert result.ingested == 2
    assert result.collection == "documents"
