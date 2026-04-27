import re
import unicodedata
from rank_bm25 import BM25Okapi


class Bm25Reranker:
    """Reankuje kandydatów metodą BM25 względem zapytania."""

    @staticmethod
    def _normalize(text: str) -> str:
        # Zamienia małe litery i usuwa diakrytyki: "Hasło" → "haslo", "żółw" → "zolw".
        # "ł" wymaga jawnej zamiany — jako prekomponowany znak (U+0142) nie rozkłada się
        # przez NFKD na bazę + combining mark, w przeciwieństwie do ą, ę, ó, ś itp.
        text = text.lower().replace("ł", "l")
        nfkd = unicodedata.normalize("NFKD", text)
        return "".join(c for c in nfkd if not unicodedata.combining(c))

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        # Tokenizuje po normalizacji, dla każdego słowa (rozdzielonego spacją):
        # 1. Wyodrębnia segmenty alfanumeryczne i dodaje je osobno.
        # 2. Jeśli segmentów jest więcej niż jeden, dodaje też ich konkatenację.
        # 3. Segmenty mieszające litery i cyfry rozbija na części, zachowując też całość.
        # "OR-WE-520" → ["or", "we", "520", "or-we-520", "orwe520"]
        # "SDM630"    → ["sdm630", "sdm", "630"]
        # "we520"     → ["we520", "we", "520"]
        # Tokeny 1-znakowe są pomijane ("c" z "003C", "v" z jednostek itp.).
        normalized = Bm25Reranker._normalize(text)
        tokens = []
        for word in normalized.split():
            segments = re.findall(r"[a-z0-9]+", word)
            for seg in segments:
                if len(seg) > 1:
                    tokens.append(seg)
                parts = re.findall(r"[a-z]+|[0-9]+", seg)
                if len(parts) > 1:
                    tokens.extend(p for p in parts if len(p) > 1)
            if len(segments) > 1:
                tokens.append(word)
                tokens.append("".join(segments))
        return tokens

    def combine_scores( self, cosine_ranked: list[int], bm25_ranked: list[int], k: int = 60 ) -> list[tuple[int, float]]:
        """
        Łączy rankingi cosinusowy i BM25 metodą RRF (Reciprocal Rank Fusion).

        Działa na rankach, nie surowych scorach — odporna na różnice w skali między
        cosinusem (0–1) a BM25 (dowolna wartość dodatnia).

        Args:
            cosine_ranked: indeksy chunków posortowane malejąco po podobieństwie cosinusowym,
                           np. [2, 0, 1, 3] (chunk 2 najlepszy cosinusowo)
            bm25_ranked:   indeksy chunków posortowane malejąco po BM25; może być podzbiorem
                           cosine_ranked, np. [0, 2] (tylko kandydaci BM25)
            k:             stała wygładzająca RRF (domyślnie 60, standard w hybrid search)

        Returns:
            Lista par (chunk_idx, rrf_score) posortowana malejąco.
            Przykład dla cosine_ranked=[2,0,1], bm25_ranked=[0,2]:
                [(0, 0.0322), (2, 0.0313), (1, 0.0161)]
        """
        cosine_rank = {idx: rank for rank, idx in enumerate(cosine_ranked)}
        bm25_rank   = {idx: rank for rank, idx in enumerate(bm25_ranked)}
        all_indices = set(cosine_ranked) | set(bm25_ranked)
        rrf = {}
        for idx in all_indices:
            score = 0.0
            if idx in cosine_rank:
                score += 1.0 / (k + cosine_rank[idx])
            if idx in bm25_rank:
                score += 1.0 / (k + bm25_rank[idx])
            rrf[idx] = score
        return sorted(rrf.items(), key=lambda x: x[1], reverse=True)

    def rerank(self, query: str, candidates: list[str]) -> list[tuple[int, float]]:
        """
        Reankuje kandydatów względem query i zwraca posortowane wyniki.

        Normalizuje diakrytyki (ą→a, ł→l, ó→o itp.) i tokenizuje przez podział na znaki
        niealfanumeryczne, dzięki czemu "OR-WE-520" i "520" są porównywalne, a zapytania
        bez polskich znaków (np. "haslo") trafiają w teksty z polskimi znakami ("hasło").

        Args:
            query:      zapytanie użytkownika, np. "napięcie znamionowe licznika ORNO OR-WE-516"
            candidates: teksty kandydatów do reankowania, np.:
                            ["ORNO OR-WE-516 / Dane techniczne\n\nNapięcie: 3x230/400V ...",
                             "ORNO OR-WE-520 / Rejestry odczytu\n\nAdres | Nazwa | ...",
                             "ORNO OR-WE-516 / Instrukcja montażu\n\nKrok 1: ..."]

        Returns:
            Lista par (oryginalny_indeks, bm25_score) posortowana malejąco po score.
            Przykład dla 3 kandydatów:
                [(1, 4.21), (0, 2.87), (2, 0.0)]
        """
        tokenized = [self._tokenize(doc) for doc in candidates]
        bm25      = BM25Okapi(tokenized)
        scores    = bm25.get_scores(self._tokenize(query))
        ranked    = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [(idx, float(score)) for idx, score in ranked]
