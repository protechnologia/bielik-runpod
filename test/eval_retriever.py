#!/usr/bin/env python3
"""
eval_retriever.py — ewaluacja jakości retrievera (embedder + opcjonalny BM25) na golden secie.

Embeduje teksty wszystkich chunków (corpus), następnie dla każdego prompta oblicza
podobieństwo cosinusowe do wszystkich chunków i opcjonalnie reankuje kandydatów przez BM25.
Sprawdza, czy właściwy chunk trafia do top-k. Mierzy Recall@1 … Recall@k i MRR.

Użycie:
    python test/eval_retriever.py data/golden_set.json [--k N] [--bm25-candidates N] [--ollama-url URL] [--verbose]

Przykłady:
    python test/eval_retriever.py data/golden_set.json --k 3
    python test/eval_retriever.py data/golden_set.json --k 3 --bm25-candidates 0   # sam embedder
    python test/eval_retriever.py data/golden_set.json --k 3 --bm25-candidates 20  # embedder + BM25
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
from api.bm25_reranker import Bm25Reranker
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


async def compute_ranks( client: OllamaClient, entries: list[dict], corpus_vecs: list[list[float]], bm25_candidates: int ) -> list[tuple[int, int, list[int], list[float], dict[int, float], dict[int, float], dict[int, int], dict[int, int]]]:
    # Dla każdej pary (prompt, chunk) oblicza rank właściwego chunku oraz pełny ranking.
    #
    # Gdy bm25_candidates > 0: bierze top bm25_candidates kandydatów cosinusowych,
    # reankuje przez BM25, łączy oba rankingi przez RRF. Gdy 0: używa samego cosinusa.
    #
    # Wejście:
    #   entries          = [{"chunk_id": 0, "prompts": ["napięcie L1 ORNO OR-WE-516", ...], ...}, ...]
    #   corpus_vecs      = [[0.12, -0.04, ...], [0.03, 0.91, ...]]
    #   bm25_candidates  = 20  (lub 0 — wyłączony)
    #
    # Wyjście:
    #   [(rank, correct_idx, ranked, cosine_scores, bm25_scores, rrf_scores, cosine_ranks, bm25_ranks), ...]
    #   cosine_ranks: {chunk_idx: rank_1based} — pozycja w rankingu cosinusowym (przed RRF)
    #   bm25_ranks:   {chunk_idx: rank_1based} — pozycja w rankingu BM25 (tylko kandydaci)
    reranker = Bm25Reranker() if bm25_candidates > 0 else None
    results: list[tuple[int, int, list[int], list[float], dict[int, float], dict[int, float], dict[int, int], dict[int, int]]] = []
    total = sum(len(e["prompts"]) for e in entries)
    done  = 0

    for i, entry in enumerate(entries):
        for prompt in entry["prompts"]:
            done += 1
            print(f"  [{done}/{total}] chunk_id={entry['chunk_id']}  \"{prompt}\"")
            query_vec    = await client.embed(prompt)
            scores       = [cosine(query_vec, cv) for cv in corpus_vecs]
            ranked       = sorted(range(len(scores)), key=lambda j: scores[j], reverse=True)
            cosine_ranks = {idx: rank + 1 for rank, idx in enumerate(ranked)}

            bm25_scores: dict[int, float] = {}
            bm25_ranks:  dict[int, int]   = {}
            rrf_scores:  dict[int, float] = {}
            if reranker is not None:
                candidates_idx     = ranked[:bm25_candidates]
                texts              = [entries[idx]["text"] for idx in candidates_idx]
                bm25_result        = reranker.rerank(prompt, texts)
                bm25_scores        = {candidates_idx[local_idx]: score for local_idx, score in bm25_result}
                bm25_ranked_global = [candidates_idx[local_idx] for local_idx, score in bm25_result if score > 0]
                bm25_ranks         = {idx: rank + 1 for rank, idx in enumerate(bm25_ranked_global)}
                combined           = reranker.combine_scores(ranked, bm25_ranked_global)
                rrf_scores         = dict(combined)
                ranked             = [idx for idx, _ in combined]

            results.append((ranked.index(i) + 1, i, ranked, scores, bm25_scores, rrf_scores, cosine_ranks, bm25_ranks))

    return results


def _print_verbose_results( entries: list[dict], results: list[tuple[int, int, list[int], list[float], dict[int, float], dict[int, float], dict[int, int], dict[int, int]]], k: int, bm25_candidates: int ) -> None:
    GREEN, RED, RESET = "\033[92m", "\033[91m", "\033[0m"
    print()
    prompt_idx = 0
    for entry in entries:
        for prompt in entry["prompts"]:
            rank, correct_i, ranked, scores, bm25_scores, rrf_scores, cosine_ranks, bm25_ranks = results[prompt_idx]
            hit = f"{GREEN}✓{RESET}" if rank == 1 else f"{RED}✗{RESET}"
            print(f"  {hit} [{rank}] \"{prompt}\"")
            if bm25_candidates > 0:
                query_tokens = Bm25Reranker._tokenize(prompt)
                print(f"         tokens: [{', '.join(query_tokens)}]")
            else:
                query_tokens = []
            query_token_set = set(query_tokens)
            top_chunks = ranked[:k]
            max_cosine = max(scores[i] for i in top_chunks)
            max_bm25   = max((bm25_scores[i] for i in top_chunks if i in bm25_scores), default=None)
            max_rrf    = max((rrf_scores[i]  for i in top_chunks if i in rrf_scores),  default=None)
            for pos, chunk_i in enumerate(top_chunks, start=1):
                correct  = chunk_i == correct_i
                prefix   = f"     {GREEN}►{RESET} " if correct else "       "
                cos_val  = scores[chunk_i]
                cos_s    = f"{cos_val:.4f}"
                cos_s    = f"{GREEN}{cos_s}{RESET}" if cos_val == max_cosine else cos_s
                cos_rank = cosine_ranks.get(chunk_i)
                cosine_str = f"cosine: {cos_s} [rank_cos={cos_rank}]"
                bm25_val  = bm25_scores.get(chunk_i)
                bm25_rank = bm25_ranks.get(chunk_i)
                if bm25_val is not None:
                    bm25_s   = f"{bm25_val:.4f}"
                    bm25_s   = f"{GREEN}{bm25_s}{RESET}" if bm25_val == max_bm25 else bm25_s
                    bm25_str = f"  bm25: {bm25_s} [rank_bm25={bm25_rank}]"
                else:
                    bm25_str = ""
                rrf_val = rrf_scores.get(chunk_i)
                if rrf_val is not None:
                    rrf_s   = f"{rrf_val:.4f}"
                    rrf_s   = f"{GREEN}{rrf_s}{RESET}" if rrf_val == max_rrf else rrf_s
                    rrf_str = f"  rrf: {rrf_s}"
                else:
                    rrf_str = ""
                print(f"{prefix}#{pos} chunk_id={entries[chunk_i]['chunk_id']}  ({cosine_str}{bm25_str}{rrf_str})")
                if query_token_set:
                    chunk_tokens = Bm25Reranker._tokenize(entries[chunk_i]["text"])
                    chunk_counts: dict[str, int] = {}
                    for t in chunk_tokens:
                        chunk_counts[t] = chunk_counts.get(t, 0) + 1
                    matched = sorted(query_token_set & chunk_counts.keys())
                    matched_str = ", ".join(f"{t}({chunk_counts[t]})" for t in matched)
                    print(f"               matched: [{matched_str}]")
            prompt_idx += 1


async def run(golden_path: Path, k: int, bm25_candidates: int, ollama_url: str, verbose: bool = False) -> None:
    # Embeduje corpus i wszystkie prompty, oblicza Recall@1…k i MRR, drukuje raport.
    #
    # Wejście:
    #   golden_path     = Path("data/golden_set.json")
    #   k               = 3
    #   bm25_candidates = 20  (lub 0 — sam embedder)
    #   ollama_url      = "http://localhost:11434"
    #   verbose         = False  # True: drukuje wynik dla każdego prompta
    #
    # Wyjście (stdout, 3 chunki, 15 par, verbose=True):
    #   ✓ [1] "napięcie L1 rejestr ORNO OR-WE-516"
    #        #1 chunk_id=0  (cosine: 0.8321) ← poprawny
    #        #2 chunk_id=2  (cosine: 0.7102)
    #        #3 chunk_id=1  (cosine: 0.6891)
    #   ✗ [2] "reset licznika OR-WE-520"
    #        #1 chunk_id=0  (cosine: 0.8120)
    #        #2 chunk_id=1  (cosine: 0.7240) ← poprawny
    #        #3 chunk_id=2  (cosine: 0.6510)
    #   ...
    #   ══════════════════════════════════════════════════
    #     Chunków:             3
    #     Par (prompt, chunk): 15
    #     BM25 kandydaci:      20
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

    mode = f"embedder + BM25 (kandydaci: {bm25_candidates})" if bm25_candidates > 0 else "sam embedder"
    print(f"\nEwaluacja ({mode})...")
    results = await compute_ranks(client, entries, corpus_vecs, bm25_candidates)

    if verbose:
        _print_verbose_results(entries, results, k, bm25_candidates)

    ranks = [r for r, _, _, _, _, _, _, _ in results]
    total = len(ranks)
    mrr = sum(1.0 / r for r in ranks) / total

    print(f"\n{'═' * 50}")
    print(f"  Chunków:             {len(entries)}")
    print(f"  Par (prompt, chunk): {total}")
    if bm25_candidates > 0:
        print(f"  BM25 kandydaci:      {bm25_candidates}")
    for ki in range(1, k + 1):
        hits = sum(1 for r in ranks if r <= ki)
        print(f"  Recall@{ki}:            {hits / total:.3f}  ({hits}/{total})")
    print(f"  MRR:                 {mrr:.3f}")
    print(f"{'═' * 50}")


def main() -> None:
    # Parsuje argumenty CLI i uruchamia run() przez asyncio.
    #
    # Przykłady:
    #   python test/eval_retriever.py data/golden_set.json
    #   python test/eval_retriever.py data/golden_set.json --k 5 --bm25-candidates 0
    #   python test/eval_retriever.py data/golden_set.json --k 3 --bm25-candidates 20 --verbose
    parser = argparse.ArgumentParser(description="Ewaluacja retrievera (embedder + BM25) na golden secie.")
    parser.add_argument("golden", metavar="FILE", help="Ścieżka do golden_set.json")
    parser.add_argument("--k", type=int, default=3, metavar="N", help="Górne k dla Recall@1…k (domyślnie: 3)")
    parser.add_argument("--bm25-candidates", type=int, default=20, metavar="N",
                        help="Liczba kandydatów z embeddera przed rerankingiem BM25; 0 wyłącza BM25 (domyślnie: 20)")
    parser.add_argument("--verbose", action="store_true", help="Pokaż wynik dla każdego prompta")
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_URL", "http://localhost:11434"),
        metavar="URL",
        help="URL Ollamy (domyślnie: $OLLAMA_URL lub http://localhost:11434)",
    )
    args = parser.parse_args()
    asyncio.run(run(Path(args.golden), args.k, args.bm25_candidates, args.ollama_url, args.verbose))


if __name__ == "__main__":
    main()
