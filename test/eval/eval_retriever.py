#!/usr/bin/env python3
"""
eval_retriever.py — ewaluacja jakości retrievera (embedder + opcjonalny BM25 + opcjonalny query router) na golden secie.

Embeduje teksty wszystkich chunków (corpus), następnie dla każdego prompta oblicza
podobieństwo cosinusowe do wszystkich chunków i opcjonalnie reankuje kandydatów przez BM25.
Jeśli włączony query router, najpierw identyfikuje urządzenie z pytania i zawęża corpus
do chunków z pasującym source_label — analogicznie do działania w /ask.
Sprawdza, czy właściwy chunk trafia do top-k. Mierzy Recall@1 … Recall@k i MRR.

Użycie:
    python test/eval_retriever.py data/golden_set.json [--k N] [--bm25-candidates N] [--query-router] [--ollama-url URL] [--verbose]

Przykłady:
    python test/eval_retriever.py data/golden_set.json --k 3
    python test/eval_retriever.py data/golden_set.json --k 3 --bm25-candidates 0          # sam embedder
    python test/eval_retriever.py data/golden_set.json --k 3 --bm25-candidates 20         # embedder + BM25
    python test/eval_retriever.py data/golden_set.json --k 3 --query-router               # embedder + query router
    python test/eval_retriever.py data/golden_set.json --k 3 --bm25-candidates 20 --query-router --verbose
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
from api.query_router import QueryRouter
from api.config import EMBED_MODEL, ROUTER_MODEL


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
    #   [{"chunk_id": 0, "source_label": "ORNO OR-WE-520", "text": "ORNO OR-WE-520 / Rejestry odczytu\n..."}, ...]
    #
    # Wyjście:
    #   [[0.12, -0.04, 0.87, ...], [0.03, 0.91, -0.22, ...]]  # 768 wartości na chunk
    vecs: list[list[float]] = []
    for i, entry in enumerate(entries):
        vec = await client.embed(entry["text"])
        vecs.append(vec)
        print(f"  [{i + 1}/{len(entries)}] chunk_id={entry['chunk_id']}")
    return vecs


async def route_prompts(
    router: QueryRouter,
    entries: list[dict],
    source_labels: list[str],
) -> tuple[list[str | None], list[list[int]]]:
    # Odpytuje router dla każdego prompta i zwraca decyzje oraz indeksy do przeszukania.
    #
    # Wejście:
    #   router        = QueryRouter(...)
    #   entries       = [{"source_label": "ORNO OR-WE-520", "prompts": [...], ...}, ...]
    #   source_labels = ["EASTRON SDM630", "ORNO OR-WE-520"]
    #
    # Wyjście:
    #   routed_labels          = ["ORNO OR-WE-520", None, "EASTRON SDM630", ...]  # per prompt
    #   search_indices_per_prompt = [[0,1,2], [0,1,2,3,4,5], [3,4,5], ...]       # per prompt
    total = sum(len(e["prompts"]) for e in entries)
    done = 0
    routed_labels: list[str | None] = []
    search_indices_per_prompt: list[list[int]] = []

    for entry in entries:
        for prompt in entry["prompts"]:
            done += 1
            print(f"  [{done}/{total}] chunk_id={entry['chunk_id']}  \"{prompt}\"")
            routed = await router.route(prompt, source_labels)
            routed_labels.append(routed)
            if routed is not None:
                search_indices_per_prompt.append(
                    [j for j, e in enumerate(entries) if e.get("source_label") == routed]
                )
            else:
                search_indices_per_prompt.append(list(range(len(entries))))

    return routed_labels, search_indices_per_prompt


async def compute_ranks(
    client: OllamaClient,
    entries: list[dict],
    corpus_vecs: list[list[float]],
    bm25_candidates: int,
    search_indices_per_prompt: list[list[int]],
) -> list[tuple]:
    # Dla każdej pary (prompt, chunk) oblicza rank właściwego chunku oraz pełny ranking.
    #
    # Gdy bm25_candidates > 0: bierze top bm25_candidates kandydatów cosinusowych,
    # reankuje przez BM25, łączy oba rankingi przez RRF. Gdy 0: używa samego cosinusa.
    # search_indices_per_prompt określa, które chunki brać pod uwagę dla każdego prompta
    # (pełny corpus lub podzbiór po filtracji routerem — compute_ranks nie wie skąd pochodzi).
    #
    # Wejście:
    #   entries                   = [{"chunk_id": 0, "prompts": [...], "text": "..."}, ...]
    #   corpus_vecs               = [[0.12, -0.04, ...], [0.03, 0.91, ...]]
    #   bm25_candidates           = 20  (lub 0 — wyłączony)
    #   search_indices_per_prompt = [[0,1,2,3,4,5], [3,4,5], ...]  # jeden wpis na prompt
    #
    # Wyjście (krotka 8 elementów):
    #   (rank, correct_idx, ranked, cosine_scores, bm25_scores, rrf_scores, cosine_ranks, bm25_ranks)
    #   cosine_ranks: {chunk_idx: rank_1based} — pozycja w rankingu cosinusowym (przed RRF)
    #   bm25_ranks:   {chunk_idx: rank_1based} — pozycja w rankingu BM25 (tylko kandydaci)
    reranker = Bm25Reranker() if bm25_candidates > 0 else None
    results: list[tuple] = []
    total = sum(len(e["prompts"]) for e in entries)
    done  = 0
    prompt_idx = 0

    for i, entry in enumerate(entries):
        for prompt in entry["prompts"]:
            done += 1
            print(f"  [{done}/{total}] chunk_id={entry['chunk_id']}  \"{prompt}\"")

            search_indices = search_indices_per_prompt[prompt_idx]
            prompt_idx += 1

            # właściwy chunk poza search_indices → miss (błąd routera lub brak danych)
            if i not in search_indices:
                results.append((len(entries) + 1, i, search_indices, [0.0] * len(entries), {}, {}, {}, {}))
                continue

            query_vec = await client.embed(prompt)

            # scores jako pełna lista — 0.0 dla chunków spoza search_indices
            scores = [0.0] * len(entries)
            for j in search_indices:
                scores[j] = cosine(query_vec, corpus_vecs[j])

            ranked       = sorted(search_indices, key=lambda j: scores[j], reverse=True)
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


def _print_verbose_results(
    entries: list[dict],
    results: list[tuple],
    routed_labels: list[str | None],
    k: int,
    bm25_candidates: int,
    query_router: bool,
) -> None:
    GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"
    print()
    prompt_idx = 0
    for entry in entries:
        for prompt in entry["prompts"]:
            rank, correct_i, ranked, scores, bm25_scores, rrf_scores, cosine_ranks, bm25_ranks = results[prompt_idx]
            hit = f"{GREEN}✓{RESET}" if rank <= k else f"{RED}✗{RESET}"
            print(f"  {hit} [{rank}] \"{prompt}\"")

            if query_router:
                routed = routed_labels[prompt_idx]
                correct_label = entry.get("source_label", "?")
                if routed is None:
                    router_str = f"{YELLOW}router: brak → pełny corpus{RESET}"
                elif routed == correct_label:
                    router_str = f"{GREEN}router: {routed}{RESET}"
                else:
                    router_str = f"{RED}router: {routed} (oczekiwano: {correct_label}){RESET}"
                print(f"         {router_str}")

            if bm25_candidates > 0:
                query_tokens = Bm25Reranker._tokenize(prompt)
                print(f"         tokens: [{', '.join(query_tokens)}]")
            else:
                query_tokens = []
            query_token_set = set(query_tokens)

            top_chunks = ranked[:k]
            if not top_chunks:
                prompt_idx += 1
                continue

            max_cosine = max((scores[i] for i in top_chunks), default=None)
            max_bm25   = max((bm25_scores[i] for i in top_chunks if i in bm25_scores), default=None)
            max_rrf    = max((rrf_scores[i]  for i in top_chunks if i in rrf_scores),  default=None)
            for pos, chunk_i in enumerate(top_chunks, start=1):
                correct    = chunk_i == correct_i
                prefix     = f"     {GREEN}►{RESET} " if correct else "       "
                cos_val    = scores[chunk_i]
                cos_s      = f"{cos_val:.4f}"
                cos_s      = f"{GREEN}{cos_s}{RESET}" if cos_val == max_cosine else cos_s
                cos_rank   = cosine_ranks.get(chunk_i)
                cosine_str = f"cosine: {cos_s} [rank_cos={cos_rank}]"
                bm25_val   = bm25_scores.get(chunk_i)
                bm25_rank  = bm25_ranks.get(chunk_i)
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


def _print_summary(
    entries: list[dict],
    results: list[tuple],
    routed_labels: list[str | None],
    k: int,
    bm25_candidates: int,
    query_router: bool,
) -> None:
    ranks = [r for r, *_ in results]
    total = len(ranks)
    mrr   = sum(1.0 / r for r in ranks if r <= len(entries)) / total

    print(f"\n{'═' * 50}")
    print(f"  Chunków:             {len(entries)}")
    print(f"  Par (prompt, chunk): {total}")
    if bm25_candidates > 0:
        print(f"  BM25 kandydaci:      {bm25_candidates}")
    if query_router:
        prompt_idx = 0
        router_correct = router_fallback = router_wrong = 0
        for entry in entries:
            for _ in entry["prompts"]:
                routed = routed_labels[prompt_idx]
                correct_label = entry.get("source_label")
                if routed is None:
                    router_fallback += 1
                elif routed == correct_label:
                    router_correct += 1
                else:
                    router_wrong += 1
                prompt_idx += 1
        print(f"  Router trafień:      {router_correct}/{total}")
        print(f"  Router fallback:     {router_fallback}/{total}")
        print(f"  Router błędów:       {router_wrong}/{total}")
    for ki in range(1, k + 1):
        hits = sum(1 for r in ranks if r <= ki)
        print(f"  Recall@{ki}:            {hits / total:.3f}  ({hits}/{total})")
    print(f"  MRR:                 {mrr:.3f}")
    print(f"{'═' * 50}")


async def setup_router(
    entries: list[dict],
    ollama_url: str,
) -> tuple[list[str | None], list[list[int]]]:
    # Odpytuje query router dla wszystkich promptów i zwraca decyzje oraz indeksy do przeszukania.
    #
    # Wejście:
    #   entries    = [{"source_label": "ORNO OR-WE-520", "prompts": [...], ...}, ...]
    #   ollama_url = "http://localhost:11434"
    #
    # Wyjście:
    #   routed_labels             = ["ORNO OR-WE-520", None, "EASTRON SDM630", ...]  # per prompt
    #   search_indices_per_prompt = [[0,1,2], [0,1,2,3,4,5], [3,4,5], ...]          # per prompt
    source_labels = sorted(set(e["source_label"] for e in entries if e.get("source_label")))
    router_client = OllamaClient(base_url=ollama_url, model=ROUTER_MODEL, embed_model=EMBED_MODEL)
    router = QueryRouter(router_client)
    print(f"\nQuery router ({ROUTER_MODEL}) — urządzenia: {source_labels}")
    print("Routing promptów...")
    return await route_prompts(router, entries, source_labels)


async def run(
    golden_path: Path, k: int, bm25_candidates: int, ollama_url: str, query_router: bool = False, verbose: bool = False ) -> None:
    # Embeduje corpus i wszystkie prompty, oblicza Recall@1…k i MRR, drukuje raport.
    #
    # Wejście:
    #   golden_path     = Path("data/golden_set.json")
    #   k               = 3
    #   bm25_candidates = 20  (lub 0 — sam embedder)
    #   ollama_url      = "http://localhost:11434"
    #   query_router    = False  # True: identyfikuje urządzenie przed wyszukiwaniem
    #   verbose         = False  # True: drukuje wynik dla każdego prompta

    # 1. wczytaj golden set — pomiń wpisy bez promptów
    entries = json.loads(golden_path.read_text(encoding="utf-8"))
    entries = [e for e in entries if e.get("prompts")]

    if not entries:
        print("Brak wpisów z promptami w golden secie.")
        return

    # 2. embeduj cały corpus (jeden wektor na chunk)
    client = OllamaClient(base_url=ollama_url, model="", embed_model=EMBED_MODEL)
    print(f"Embedowanie {len(entries)} chunków...")
    corpus_vecs = await embed_corpus(client, entries)

    # 3. query router: dla każdego prompta ustal, które chunki przeszukiwać
    #    bez routera — pełny corpus dla każdego prompta
    total_prompts = sum(len(e["prompts"]) for e in entries)
    if query_router:
        routed_labels, search_indices_per_prompt = await setup_router(entries, ollama_url)
    else:
        routed_labels             = [None] * total_prompts
        search_indices_per_prompt = [list(range(len(entries)))] * total_prompts

    # 4. oblicz rankingi (cosine + opcjonalny BM25) dla każdego prompta
    parts = []
    if query_router:
        parts.append("query router")
    if bm25_candidates > 0:
        parts.append(f"BM25 (kandydaci: {bm25_candidates})")
    mode = "embedder + " + " + ".join(parts) if parts else "sam embedder"
    print(f"\nEwaluacja ({mode})...")
    results = await compute_ranks(client, entries, corpus_vecs, bm25_candidates, search_indices_per_prompt)

    # 5. raportuj wyniki
    if verbose:
        _print_verbose_results(entries, results, routed_labels, k, bm25_candidates, query_router)
    _print_summary(entries, results, routed_labels, k, bm25_candidates, query_router)


def main() -> None:
    # Parsuje argumenty CLI i uruchamia run() przez asyncio.
    #
    # Przykłady:
    #   python test/eval_retriever.py data/golden_set.json
    #   python test/eval_retriever.py data/golden_set.json --k 5 --bm25-candidates 0
    #   python test/eval_retriever.py data/golden_set.json --k 3 --bm25-candidates 20 --verbose
    #   python test/eval_retriever.py data/golden_set.json --k 3 --query-router
    #   python test/eval_retriever.py data/golden_set.json --k 3 --bm25-candidates 20 --query-router --verbose
    parser = argparse.ArgumentParser(description="Ewaluacja retrievera (embedder + BM25 + query router) na golden secie.")
    parser.add_argument("golden", metavar="FILE", help="Ścieżka do golden_set.json")
    parser.add_argument("--k", type=int, default=3, metavar="N", help="Górne k dla Recall@1…k (domyślnie: 3)")
    parser.add_argument("--bm25-candidates", type=int, default=20, metavar="N",
                        help="Liczba kandydatów z embeddera przed rerankingiem BM25; 0 wyłącza BM25 (domyślnie: 20)")
    parser.add_argument("--query-router", action="store_true",
                        help=f"Włącz query router ({ROUTER_MODEL}) — identyfikuje urządzenie i zawęża corpus")
    parser.add_argument("--verbose", action="store_true", help="Pokaż wynik dla każdego prompta")
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_URL", "http://localhost:11434"),
        metavar="URL",
        help="URL Ollamy (domyślnie: $OLLAMA_URL lub http://localhost:11434)",
    )
    args = parser.parse_args()
    asyncio.run(run(Path(args.golden), args.k, args.bm25_candidates, args.ollama_url, args.query_router, args.verbose))


if __name__ == "__main__":
    main()
