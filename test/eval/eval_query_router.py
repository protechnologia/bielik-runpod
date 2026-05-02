#!/usr/bin/env python3
"""
eval_query_router.py — ewaluacja jakości Query Routera na golden secie.

Dla każdego prompta w golden secie odpytuje QueryRouter z listą unikalnych
source_label i sprawdza, czy zwrócony label zgadza się z oczekiwanym.
Raportuje Accuracy oraz rozkład trafień / fallbacków / błędów.

Użycie:
    python test/eval_query_router.py data/golden_set.json [--ollama-url URL] [--verbose]

Przykłady:
    python test/eval_query_router.py data/golden_set.json
    python test/eval_query_router.py data/golden_set.json --verbose
    python test/eval_query_router.py data/golden_set.json --ollama-url http://localhost:11434
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "api"))

from fastapi import HTTPException

from api.ollama_client import OllamaClient
from api.query_router import QueryRouter
from api.config import ROUTER_MODEL, EMBED_MODEL


async def run(golden_path: Path, ollama_url: str, router_model: str = ROUTER_MODEL, verbose: bool = False) -> None:
    # Wejście:
    #   golden_path  = Path("data/golden_set.json")
    #   ollama_url   = "http://localhost:11434"
    #   router_model = "SpeakLeash/bielik-1.5b-v3.0-instruct:Q8_0"  (lub inny do porównania)
    #   verbose      = False  # True: drukuje wynik dla każdego prompta

    # 1. wczytaj golden set — pomiń wpisy bez promptów i source_label
    entries = json.loads(golden_path.read_text(encoding="utf-8"))
    entries = [e for e in entries if e.get("prompts") and e.get("source_label")]

    if not entries:
        print("Brak wpisów z promptami i source_label w golden secie.")
        return

    source_labels = sorted(set(e["source_label"] for e in entries))

    # 2. zainicjuj router
    router_client = OllamaClient(base_url=ollama_url, model=router_model, embed_model=EMBED_MODEL)
    router = QueryRouter(router_client)

    print(f"Model routera:  {router_model}")
    print(f"Urządzenia:     {source_labels}")
    print(f"Chunków:        {len(entries)}")
    print(f"Par (prompt, chunk): {sum(len(e['prompts']) for e in entries)}")

    # 3. odpytaj router dla każdego prompta i zbierz wyniki
    results: list[tuple[str, str, str | None]] = []  # (prompt, correct_label, routed_label)
    total = sum(len(e["prompts"]) for e in entries)
    done = 0

    for entry in entries:
        correct_label = entry["source_label"]
        for prompt in entry["prompts"]:
            done += 1
            print(f"  [{done}/{total}] \"{prompt}\"")
            try:
                routed = await router.route(prompt, source_labels)
            except HTTPException as e:
                print(f"\nBłąd Ollamy: {e.detail}")
                print(f"Sprawdź czy model jest pobrany: ollama pull {router_model}")
                return
            results.append((prompt, correct_label, routed))

    # 4. raportuj wyniki
    if verbose:
        _print_verbose_results(results)

    _print_summary(results, source_labels, router_model)

    first_prompt = entries[0]["prompts"][0]
    _print_first_llm_prompt(first_prompt, source_labels)


def _print_first_llm_prompt(prompt: str, source_labels: list[str]) -> None:
    from api.config import ROUTER_SYSTEM_PROMPT
    labels_str = "\n".join(f"- {label}" for label in source_labels)
    user_message = f"Dostępne urządzenia:\n{labels_str}\n\nPytanie: {prompt}"
    print(f"\n{'─' * 50}")
    print("  PROMPT DO LLM (pierwsze zapytanie)")
    print(f"{'─' * 50}")
    print("[SYSTEM]")
    print(ROUTER_SYSTEM_PROMPT)
    print("\n[USER]")
    print(user_message)
    print(f"{'─' * 50}")


def _print_verbose_results(results: list[tuple[str, str, str | None]]) -> None:
    GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"
    print()
    for prompt, correct_label, routed in results:
        if routed is None:
            icon   = f"{YELLOW}?{RESET}"
            status = f"{YELLOW}brak → fallback{RESET}"
        elif routed == correct_label:
            icon   = f"{GREEN}✓{RESET}"
            status = f"{GREEN}{routed}{RESET}"
        else:
            icon   = f"{RED}✗{RESET}"
            status = f"{RED}{routed} (oczekiwano: {correct_label}){RESET}"
        print(f"  {icon} \"{prompt}\"")
        print(f"       router: {status}")


def _print_summary(results: list[tuple[str, str, str | None]], source_labels: list[str], router_model: str) -> None:
    total    = len(results)
    correct  = sum(1 for _, cl, rl in results if rl == cl)
    fallback = sum(1 for _, _, rl in results if rl is None)
    wrong    = sum(1 for _, cl, rl in results if rl is not None and rl != cl)

    print(f"\n{'═' * 50}")
    print(f"  Model routera:       {router_model}")
    print(f"  Urządzenia:          {len(source_labels)}  ({', '.join(source_labels)})")
    print(f"  Par (prompt, chunk): {total}")
    print(f"  Trafień:             {correct}/{total}  ({correct / total:.1%})")
    print(f"  Fallback (brak):     {fallback}/{total}  ({fallback / total:.1%})")
    print(f"  Błędów:              {wrong}/{total}  ({wrong / total:.1%})")
    print(f"  Accuracy:            {correct / total:.3f}")
    print(f"{'═' * 50}")


def main() -> None:
    # Parsuje argumenty CLI i uruchamia run() przez asyncio.
    #
    # Przykłady:
    #   python test/eval_query_router.py data/golden_set.json
    #   python test/eval_query_router.py data/golden_set.json --verbose
    #   python test/eval_query_router.py data/golden_set.json --router-model SpeakLeash/bielik-4.5b-v3.0-instruct:Q8_0
    #   python test/eval_query_router.py data/golden_set.json --ollama-url http://localhost:11434 --verbose
    parser = argparse.ArgumentParser(description="Ewaluacja Query Routera na golden secie.")
    parser.add_argument("golden", metavar="FILE", help="Ścieżka do golden_set.json")
    parser.add_argument("--verbose", action="store_true", help="Pokaż wynik dla każdego prompta")
    parser.add_argument(
        "--router-model",
        default=ROUTER_MODEL,
        metavar="MODEL",
        help=f"Nazwa modelu routera w Ollama (domyślnie: {ROUTER_MODEL})",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_URL", "http://localhost:11434"),
        metavar="URL",
        help="URL Ollamy (domyślnie: $OLLAMA_URL lub http://localhost:11434)",
    )
    args = parser.parse_args()
    asyncio.run(run(Path(args.golden), args.ollama_url, args.router_model, args.verbose))


if __name__ == "__main__":
    main()
