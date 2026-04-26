#!/usr/bin/env python3
"""
eval_embedder.py — ewaluacja jakości embeddera na golden secie.

Embeduje teksty wszystkich chunków (corpus), następnie dla każdego prompta
oblicza podobieństwo cosinusowe do wszystkich chunków i sprawdza, czy właściwy
chunk trafia do top-k. Mierzy Recall@1 … Recall@k i MRR.

Użycie:
    python test/eval_embedder.py data/golden_set.json [--k N] [--ollama-url URL] [--verbose]

Przykład:
    python test/eval_embedder.py data/golden_set.json --k 3
"""

import argparse
import asyncio
import json
import math
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.ollama_client import OllamaClient
from api.config import EMBED_MODEL

def cosine(a: list[float], b: list[float]) -> float:
    # Podobieństwo cosinusowe między dwoma wektorami embeddingu.
    #
    # Przykłady:
    #   cosine([1.0, 0.0], [1.0, 0.0])  → 1.0  (identyczne)
    #   cosine([1.0, 0.0], [0.0, 1.0])  → 0.0  (prostopadłe)
    #   cosine([1.0, 0.0], [-1.0, 0.0]) → -1.0 (przeciwne)
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def embed_corpus(client: OllamaClient, entries: list[dict]) -> list[list[float]]:
    # Embeduje teksty wszystkich chunków i zwraca listę wektorów (corpus).
    #
    # Wejście:
    #   [{"chunk_id": 0, "text": "ORNO OR-WE-520 / Rejestry odczytu\n..."}, ...]
    #
    # Wyjście:
    #   [[0.12, -0.04, 0.87, ...], [0.03, 0.91, -0.22, ...]]  # 768 wartości na chunk
    vecs: list[list[float]] = []
    for i, entry in enumerate(entries):
        vec = await client.embed(entry["text"])
        vecs.append(vec)
        print(f"  [{i + 1}/{len(entries)}] chunk_id={entry['chunk_id']}")
    return vecs


async def compute_ranks(
    client: OllamaClient,
    entries: list[dict],
    corpus_vecs: list[list[float]],
) -> list[tuple[int, int, list[int], list[float]]]:
    # Dla każdej pary (prompt, chunk) oblicza rank właściwego chunku oraz pełny ranking z cosinusami.
    #
    # Wejście:
    #   entries     = [{"chunk_id": 0, "prompts": ["napięcie L1", ...], ...}, ...]
    #   corpus_vecs = [[0.12, -0.04, ...], [0.03, 0.91, ...]]
    #
    # Wyjście:
    #   [(rank, correct_idx, ranked, scores), ...]
    #   np. [(2, 1, [0, 1, 2], [0.812, 0.724, 0.651]), ...]
    #   — rank 2, poprawny chunk 1, ranking chunków malejąco z ich cosinusami
    results: list[tuple[int, int, list[int], list[float]]] = []
    for i, entry in enumerate(entries):
        for prompt in entry["prompts"]:
            query_vec = await client.embed(prompt)
            scores = [cosine(query_vec, cv) for cv in corpus_vecs]

            # sorted(range(len(scores)), ...) sortuje indeksy [0, 1, 2, ...] według scores[j] malejąco.
            # Gdybyśmy sortowali same scores, stracilibyśmy informację który wynik należy do którego chunku.
            # Przykład: scores=[0.3, 0.9, 0.5] → ranked=[1, 2, 0]
            # (chunk 1 na 1. miejscu z score 0.9, chunk 2 na 2. z 0.5, chunk 0 na 3. z 0.3)
            ranked = sorted(range(len(scores)), key=lambda j: scores[j], reverse=True)

            # ranked.index(i) zwraca pozycję właściwego chunku (i) na posortowanej liście (0-based).
            # +1 zamienia na rank 1-based (1 = trafienie na pierwszym miejscu).
            # Przykład: i=2, ranked=[1, 2, 0] → ranked.index(2)=1 → rank=2
            results.append((ranked.index(i) + 1, i, ranked, scores))
    return results


async def run(golden_path: Path, k: int, ollama_url: str, verbose: bool = False) -> None:
    # Embeduje corpus i wszystkie prompty, oblicza Recall@1…k i MRR, drukuje raport.
    #
    # Wejście:
    #   golden_path = Path("data/golden_set.json")
    #   k           = 3
    #   ollama_url  = "http://localhost:11434"
    #   verbose     = False  # True: drukuje wynik dla każdego prompta (poprawny chunk vs wybrany)
    #
    # Wyjście (stdout, 3 chunki, 15 par, verbose=True):
    #   ✓ [1] "napięcie L1 rejestr orno 520"
    #        #1 chunk_id=0  (cosine: 0.8321) ← poprawny
    #        #2 chunk_id=2  (cosine: 0.7102)
    #        #3 chunk_id=1  (cosine: 0.6891)
    #   ✗ [2] "reset licznika we520"
    #        #1 chunk_id=0  (cosine: 0.8120)
    #        #2 chunk_id=1  (cosine: 0.7240) ← poprawny
    #        #3 chunk_id=2  (cosine: 0.6510)
    #   ...
    #   ══════════════════════════════════════════════════
    #     Chunków:             3
    #     Par (prompt, chunk): 15
    #     Recall@1:            0.533  (8/15)
    #     Recall@2:            0.733  (11/15)
    #     Recall@3:            1.000  (15/15)
    #     MRR:                 0.722
    #   ══════════════════════════════════════════════════
    entries = json.loads(golden_path.read_text(encoding="utf-8"))
    entries = [e for e in entries if e.get("prompts")]

    if not entries:
        print("Brak wpisów z promptami w golden secie.")
        return

    client = OllamaClient(base_url=ollama_url, model="", embed_model=EMBED_MODEL)

    print(f"Embedowanie {len(entries)} chunków...")
    corpus_vecs = await embed_corpus(client, entries)

    print("\nEwaluacja...")
    results = await compute_ranks(client, entries, corpus_vecs)

    if verbose:
        print()
        prompt_idx = 0
        for entry in entries:
            for prompt in entry["prompts"]:
                rank, correct_i, ranked, scores = results[prompt_idx]
                hit = "✓" if rank == 1 else "✗"
                print(f"  {hit} [{rank}] \"{prompt}\"")
                for pos, chunk_i in enumerate(ranked[:k], start=1):
                    marker = " ← poprawny" if chunk_i == correct_i else ""
                    print(f"       #{pos} chunk_id={entries[chunk_i]['chunk_id']}  (cosine: {scores[chunk_i]:.4f}){marker}")
                prompt_idx += 1

    ranks = [r for r, _, _, _ in results]
    total = len(ranks)
    mrr = sum(1.0 / r for r in ranks) / total

    print(f"\n{'═' * 50}")
    print(f"  Chunków:             {len(entries)}")
    print(f"  Par (prompt, chunk): {total}")
    for ki in range(1, k + 1):
        hits = sum(1 for r in ranks if r <= ki)
        print(f"  Recall@{ki}:            {hits / total:.3f}  ({hits}/{total})")
    print(f"  MRR:                 {mrr:.3f}")
    print(f"{'═' * 50}")


def main() -> None:
    # Parsuje argumenty CLI i uruchamia run() przez asyncio.
    #
    # Przykład:
    #   python test/eval_embedder.py data/golden_set.json
    #   python test/eval_embedder.py data/golden_set.json --k 5 --ollama-url http://localhost:11434
    parser = argparse.ArgumentParser(description="Ewaluacja embeddera na golden secie.")
    parser.add_argument("golden", metavar="FILE", help="Ścieżka do golden_set.json")
    parser.add_argument("--k", type=int, default=3, metavar="N", help="Górne k dla Recall@1…k (domyślnie: 3)")
    parser.add_argument("--verbose", action="store_true", help="Pokaż wynik dla każdego prompta")
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_URL", "http://localhost:11434"),
        metavar="URL",
        help="URL Ollamy (domyślnie: $OLLAMA_URL lub http://localhost:11434)",
    )
    args = parser.parse_args()
    asyncio.run(run(Path(args.golden), args.k, args.ollama_url, args.verbose))


if __name__ == "__main__":
    main()