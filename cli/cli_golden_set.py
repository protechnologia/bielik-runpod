#!/usr/bin/env python3
"""
cli_golden_set.py — przygotowanie golden setu do ewaluacji RAG.

Ładuje jeden lub więcej plików XLSX, chunkuje je wszystkie, losuje kolejność
i zapisuje do JSON z pustym polem prompt do ręcznego lub AI wypełnienia.

Użycie:
    python cli/cli_golden_set.py <plik1.xlsx> <etykieta1> [plik2.xlsx <etykieta2> ...] --output golden.json

Przykład:
    python cli/cli_golden_set.py rejestry.xlsx "ORNO OR-WE-516" liczniki.xlsx "Licznik XYZ" --rows-per-chunk 30 --output golden.json
"""

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.xlsx_chunker import XlsxChunker, DEFAULT_ROWS_PER_CHUNK


def parse_args():
    """
    Parsuje argumenty wiersza poleceń.

    Argumenty CLI:
        pairs (str, +)         -- na przemian: plik XLSX i jego etykieta, np. "rejestry.xlsx 'ORNO OR-WE-516'"
        --rows-per-chunk (int) -- liczba wierszy danych na jeden chunk; domyślnie DEFAULT_ROWS_PER_CHUNK
        --output (str)         -- ścieżka do wyjściowego pliku JSON; domyślnie "golden_set.json"
        --seed (int)           -- ziarno losowości; jeśli pominięte, kolejność jest inna przy każdym uruchomieniu

    Zwraca:
        argparse.Namespace -- sparsowane argumenty jako obiekt z polami:
            .pairs          : list[str]        np. ["rejestry.xlsx", "ORNO OR-WE-516", "liczniki.xlsx", "Licznik XYZ"]
            .rows_per_chunk : int              np. 30
            .output         : str              np. "golden_set.json"
            .seed           : int | None       np. 42
    """
    parser = argparse.ArgumentParser(
        description="Przygotowanie golden setu do ewaluacji RAG.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "pairs",
        nargs="+",
        metavar="PLIK ETYKIETA",
        help="Pary: plik.xlsx 'etykieta' (można podać wiele par)",
    )
    parser.add_argument(
        "--rows-per-chunk",
        type=int,
        default=DEFAULT_ROWS_PER_CHUNK,
        metavar="N",
        help=f"Liczba wierszy na chunk (domyślnie: {DEFAULT_ROWS_PER_CHUNK})",
    )
    parser.add_argument(
        "--output",
        default="golden_set.json",
        metavar="FILE",
        help="Plik wyjściowy JSON (domyślnie: golden_set.json)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        metavar="N",
        help="Ziarno losowości dla powtarzalnej kolejności (domyślnie: losowe)",
    )
    return parser.parse_args()


def build_entries(
    paths: list[Path],
    labels: list[str],
    chunker: XlsxChunker,
) -> list[dict]:
    """
    Chunkuje wszystkie pliki XLSX i zwraca listę wpisów golden setu.

    Argumenty:
        paths   (list[Path])       -- ścieżki do plików XLSX, np. [Path("rejestry.xlsx")]
        labels  (list[str])        -- etykiety źródeł (1:1 z paths), np. ["ORNO OR-WE-516"]
        chunker (XlsxChunker)      -- skonfigurowana instancja chunkera

    Zwraca:
        list[dict] -- wpisy ze wszystkich plików z nadanymi chunk_id i pustą listą prompts, np.:
            [
              {"chunk_id": 0, "source_label": "ORNO OR-WE-516", "prompts": [], "text": "ORNO OR-WE-516 / Rejestry odczytu\n\n..."},
              {"chunk_id": 1, "source_label": "ORNO OR-WE-516", "prompts": [], "text": "ORNO OR-WE-516 / Rejestry zapisu\n\n..."}
            ]

    Rzuca:
        Exception -- jeśli XlsxChunker nie może przetworzyć pliku (propaguje wyjątek z chunker.chunk)
    """
    all_chunks = []
    for path, label in zip(paths, labels):
        chunks = chunker.chunk(path.read_bytes(), label)
        all_chunks.extend(chunks)
        print(f"  {path.name}: {len(chunks)} chunków")
    return [
        {"chunk_id": i, "source_label": chunk["source_label"], "prompts": [], "text": chunk["text"]}
        for i, chunk in enumerate(all_chunks)
    ]


def main():
    """
    Punkt wejścia skryptu. Wczytuje pliki XLSX, chunkuje je, losuje kolejność
    i zapisuje golden set do pliku JSON z pustym polem prompt.

    Nie przyjmuje argumentów (czyta je z wiersza poleceń przez parse_args).
    Nie zwraca wartości — kończy działanie przez sys.exit(1) przy błędzie.

    Wyjściowy plik JSON zawiera listę obiektów, np.:
        [
          {
            "chunk_id": 0,
            "source_label": "ORNO OR-WE-516",
            "prompts": [],
            "text": "ORNO OR-WE-516 / Rejestry odczytu\n\nAdres | Nazwa | ...\n--- | --- | ...\n..."
          },
          {
            "chunk_id": 1,
            "source_label": "Licznik XYZ",
            "prompts": [],
            "text": "Licznik XYZ / Dane techniczne\n\nParametr | Wartość\n--- | ---\n..."
          }
        ]

    Pole prompts należy wypełnić ręcznie lub przez AI przed uruchomieniem ewaluatora.
    """
    args = parse_args()

    if len(args.pairs) % 2 != 0:
        print("Błąd: argumenty muszą być parami: plik.xlsx 'etykieta' plik2.xlsx 'etykieta2' ...")
        sys.exit(1)

    paths = [Path(args.pairs[i]) for i in range(0, len(args.pairs), 2)]
    source_labels = [args.pairs[i] for i in range(1, len(args.pairs), 2)]

    for path in paths:
        if not path.exists():
            print(f"Błąd: plik '{path}' nie istnieje.")
            sys.exit(1)
        if path.suffix.lower() not in (".xlsx", ".xlsm"):
            print(f"Błąd: '{path}' nie jest plikiem XLSX.")
            sys.exit(1)
    chunker = XlsxChunker(rows_per_chunk=args.rows_per_chunk)

    try:
        entries = build_entries(paths, source_labels, chunker)
    except Exception as e:
        print(f"Błąd podczas przetwarzania: {e}")
        sys.exit(1)

    if not entries:
        print("Brak danych w podanych plikach.")
        sys.exit(1)

    random.seed(args.seed)
    random.shuffle(entries)

    output_path = Path(args.output)
    if output_path.exists() and output_path.stat().st_size > 0:
        answer = input(f"  Plik '{output_path}' już istnieje. Nadpisać? [y/N] ").strip().lower()
        if answer != "y":
            print("  Anulowano.")
            sys.exit(0)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    print(f"\n  Zapisano {len(entries)} chunków → {output_path}")
    print(f"  Wypełnij pole 'prompts' dla każdego wpisu, następnie uruchom ewaluator.")


if __name__ == "__main__":
    main()
