#!/usr/bin/env python3
"""
cli_xlsx_chunker.py — testowanie XlsxChunker z linii poleceń.

Użycie:
    python cli_xlsx_chunker.py <plik.xlsx> <source_label> [--rows-per-chunk N]

Przykład:
    python cli_xlsx_chunker.py rejestry.xlsx "ORNO OR-WE-516" --rows-per-chunk 30
"""

import argparse
import sys
from pathlib import Path

# xlsx_chunker.py znajduje się w katalogu api/ względem korzenia repo
API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(API_DIR))

try:
    from xlsx_chunker import XlsxChunker, DEFAULT_ROWS_PER_CHUNK
except ImportError:
    print(f"Błąd: nie można zaimportować xlsx_chunker z {API_DIR}. Upewnij się, że plik xlsx_chunker.py istnieje w katalogu api/.")
    sys.exit(1)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Testowanie XlsxChunker — podgląd chunków bez zapisu do Qdrant.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("file", help="Ścieżka do pliku XLSX")
    parser.add_argument("source_label", help="Etykieta źródła (np. 'ORNO OR-WE-516')")
    parser.add_argument(
        "--rows-per-chunk",
        type=int,
        default=DEFAULT_ROWS_PER_CHUNK,
        metavar="N",
        help=f"Liczba wierszy na chunk (domyślnie: {DEFAULT_ROWS_PER_CHUNK})",
    )
    return parser.parse_args()


def format_separator(char="─", width=72):
    return char * width


def main():
    args = parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Błąd: plik '{path}' nie istnieje.")
        sys.exit(1)
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        print(f"Błąd: plik musi być w formacie XLSX (podano: '{path.suffix}').")
        sys.exit(1)

    file_bytes = path.read_bytes()

    chunker = XlsxChunker(rows_per_chunk=args.rows_per_chunk)
    try:
        chunks = chunker.chunk(file_bytes, args.source_label)
    except Exception as e:
        print(f"Błąd podczas przetwarzania XLSX: {e}")
        sys.exit(1)

    if not chunks:
        print("Plik XLSX nie zawiera danych.")
        sys.exit(0)

    sheet_names = list(dict.fromkeys(c["sheet"] for c in chunks))

    # ── Nagłówek ──────────────────────────────────────────────────────────────
    print()
    print(format_separator("═"))
    print(f"  Plik:          {path.name}")
    print(f"  Source label:  {args.source_label}")
    print(f"  Rows per chunk:{args.rows_per_chunk}")
    print(f"  Arkusze:       {len(sheet_names)}  ({', '.join(sheet_names)})")
    print(f"  Chunków łącznie: {len(chunks)}")
    print(format_separator("═"))

    # ── Chunki ────────────────────────────────────────────────────────────────
    for i, chunk in enumerate(chunks):
        text = chunk["text"]
        char_count = len(text)
        word_count = len(text.split())

        print()
        print(format_separator())
        print(f"  Chunk #{i + 1}  |  Arkusz: {chunk['sheet']}  |  Część: {chunk['chunk']}")
        print(f"  Znaki: {char_count}   Słowa: {word_count}")
        print(format_separator())
        print(text)

    # ── Podsumowanie ─────────────────────────────────────────────────────────
    print()
    print(format_separator("═"))
    total_chars = sum(len(c["text"]) for c in chunks)
    total_words = sum(len(c["text"].split()) for c in chunks)
    print(f"  Podsumowanie: {len(chunks)} chunków | {total_chars} znaków | {total_words} słów")
    print(format_separator("═"))
    print()


if __name__ == "__main__":
    main()