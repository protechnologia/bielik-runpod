import io
import openpyxl

from api.xlsx_chunker import XlsxChunker


def make_xlsx(sheets: dict[str, list[list]]) -> bytes:
    """Helper — tworzy plik XLSX w pamięci ze słownika {nazwa_arkusza: [[nagłówki], [wiersz], ...]}"""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet_name, rows in sheets.items():
        ws = wb.create_sheet(sheet_name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── _load_sheets ───────────────────────────────────────────────────────────────

def test_load_sheets_basic():
    """Arkusz z nagłówkiem i jednym wierszem danych — powinien zostać załadowany."""
    xlsx = make_xlsx({"Arkusz1": [["A", "B"], ["1", "2"]]})
    chunker = XlsxChunker()
    sheets = chunker._load_sheets(xlsx)
    assert "Arkusz1" in sheets
    assert len(sheets["Arkusz1"]) == 1  # jeden wiersz danych (bez nagłówka)


def test_load_sheets_skips_empty():
    """Arkusz zawierający wyłącznie nagłówek (zero wierszy danych) powinien być pomijany."""
    xlsx = make_xlsx({
        "Pełny": [["A", "B"], ["1", "2"]],
        "Pusty": [["A", "B"]],
    })
    chunker = XlsxChunker()
    sheets = chunker._load_sheets(xlsx)
    assert "Pełny" in sheets
    assert "Pusty" not in sheets


def test_load_sheets_multiple():
    """Plik z dwoma arkuszami — oba powinny zostać załadowane."""
    xlsx = make_xlsx({
        "Arkusz1": [["A"], ["1"]],
        "Arkusz2": [["B"], ["2"]],
    })
    sheets = XlsxChunker()._load_sheets(xlsx)
    assert len(sheets) == 2


# ── _to_markdown ───────────────────────────────────────────────────────────────

def test_to_markdown_structure():
    """Markdown musi mieć nagłówek w pierwszej linii, separator w drugiej, dane w trzeciej."""
    xlsx = make_xlsx({"S": [["Adres", "Nazwa"], ["0x0001", "U_L1"]]})
    chunker = XlsxChunker()
    df = chunker._load_sheets(xlsx)["S"]
    rows = list(df.itertuples(index=False, name=None))
    md = chunker._to_markdown(df, rows)
    lines = md.split("\n")
    assert lines[0] == "Adres | Nazwa"
    assert lines[1] == "--- | ---"
    assert lines[2] == "0x0001 | U_L1"


# ── _chunk_sheet ───────────────────────────────────────────────────────────────

def test_chunk_sheet_single_chunk():
    """10 wierszy przy rows_per_chunk=50 — powinien powstać dokładnie 1 chunk."""
    xlsx = make_xlsx({"S": [["A", "B"]] + [["x", "y"]] * 10})
    chunker = XlsxChunker(rows_per_chunk=50)
    df = chunker._load_sheets(xlsx)["S"]
    chunks = chunker._chunk_sheet(df, "Dev / S", "Dev", "S")
    assert len(chunks) == 1
    assert chunks[0]["chunk"] == 1


def test_chunk_sheet_multiple_chunks():
    """100 wierszy przy rows_per_chunk=30 — powinny powstać 4 chunki (30+30+30+10)."""
    xlsx = make_xlsx({"S": [["A"]] + [["x"]] * 100})
    chunker = XlsxChunker(rows_per_chunk=30)
    df = chunker._load_sheets(xlsx)["S"]
    chunks = chunker._chunk_sheet(df, "Dev / S", "Dev", "S")
    assert len(chunks) == 4


def test_chunk_sheet_prefix_in_text():
    """Tekst każdego chunku musi zaczynać się od prefiksu '{source_label} / {arkusz}'."""
    xlsx = make_xlsx({"Rejestry": [["A"], ["1"]]})
    chunker = XlsxChunker()
    df = chunker._load_sheets(xlsx)["Rejestry"]
    chunks = chunker._chunk_sheet(df, "ORNO / Rejestry", "ORNO", "Rejestry")
    assert chunks[0]["text"].startswith("ORNO / Rejestry")


def test_chunk_sheet_payload_fields():
    """Każdy chunk musi zawierać pola: source_label, sheet, chunk z poprawnymi wartościami."""
    xlsx = make_xlsx({"S": [["A"], ["1"]]})
    chunker = XlsxChunker()
    df = chunker._load_sheets(xlsx)["S"]
    chunk = chunker._chunk_sheet(df, "Dev / S", "Dev", "S")[0]
    assert chunk["source_label"] == "Dev"
    assert chunk["sheet"] == "S"
    assert chunk["chunk"] == 1


# ── chunk (integracyjne) ───────────────────────────────────────────────────────

def test_chunk_returns_all_sheets():
    """Plik z dwoma arkuszami — chunki muszą pochodzić z obu arkuszy."""
    xlsx = make_xlsx({
        "Odczyt": [["A"], ["1"]],
        "Zapis":  [["B"], ["2"]],
    })
    chunks = XlsxChunker().chunk(xlsx, "ORNO OR-WE-516")
    sheets_in_chunks = {c["sheet"] for c in chunks}
    assert sheets_in_chunks == {"Odczyt", "Zapis"}


def test_chunk_header_repeated_in_every_chunk():
    """Nagłówek i separator tabeli Markdown muszą być powtórzone w każdym chunku,
    żeby każdy chunk był samowystarczalny bez znajomości poprzednich."""
    xlsx = make_xlsx({"S": [["Adres", "Nazwa"]] + [["x", "y"]] * 60})
    chunks = XlsxChunker(rows_per_chunk=30).chunk(xlsx, "Dev")
    for chunk in chunks:
        assert "Adres | Nazwa" in chunk["text"]
        assert "--- | ---" in chunk["text"]


def test_chunk_empty_file():
    """Plik bez żadnych danych (tylko nagłówki) — powinien zwrócić pustą listę."""
    xlsx = make_xlsx({"Pusty": [["A", "B"]]})
    chunks = XlsxChunker().chunk(xlsx, "Dev")
    assert chunks == []
