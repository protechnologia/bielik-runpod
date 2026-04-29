import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.bm25_reranker import Bm25Reranker


# ── _normalize ────────────────────────────────────────────────────────────────

def test_normalize_lowercase():
    assert Bm25Reranker._normalize("ABC") == "abc"

def test_normalize_removes_diacritics():
    assert Bm25Reranker._normalize("ąęóśźżć") == "aeoSzzc".lower()

def test_normalize_l_with_stroke():
    """ł nie rozkłada się przez NFKD — wymaga jawnej zamiany na l."""
    assert Bm25Reranker._normalize("łódź") == "lodz"

def test_normalize_mixed():
    assert Bm25Reranker._normalize("Hasło") == "haslo"


# ── _tokenize ─────────────────────────────────────────────────────────────────

def test_tokenize_hyphenated_code():
    """OR-WE-520 → segmenty + konkatenacja + całe słowo."""
    tokens = Bm25Reranker._tokenize("OR-WE-520")
    assert "or" in tokens
    assert "we" in tokens
    assert "520" in tokens
    assert "or-we-520" in tokens
    assert "orwe520" in tokens

def test_tokenize_alphanumeric():
    """SDM630 → całość + rozdzielone segmenty."""
    tokens = Bm25Reranker._tokenize("SDM630")
    assert "sdm630" in tokens
    assert "sdm" in tokens
    assert "630" in tokens

def test_tokenize_skips_single_chars():
    """Tokeny 1-znakowe są pomijane."""
    tokens = Bm25Reranker._tokenize("003C")
    assert "c" not in tokens

def test_tokenize_diacritics_normalized():
    """Diakrytyki są usuwane przed tokenizacją."""
    tokens = Bm25Reranker._tokenize("hasło")
    assert "haslo" in tokens
    assert "hasło" not in tokens


# ── combine_scores ────────────────────────────────────────────────────────────

def test_combine_scores_order():
    """Wyniki posortowane malejąco po RRF score."""
    reranker = Bm25Reranker()
    result = reranker.combine_scores(cosine_ranked=[2, 0, 1], bm25_ranked=[0, 2])
    scores = [score for _, score in result]
    assert scores == sorted(scores, reverse=True)

def test_combine_scores_both_lists_boost():
    """Chunk obecny w obu rankingach powinien mieć wyższy score niż ten tylko w jednym."""
    reranker = Bm25Reranker()
    result = reranker.combine_scores(cosine_ranked=[0, 1], bm25_ranked=[0])
    result_dict = dict(result)
    # chunk 0 jest w obu listach, chunk 1 tylko w cosine — 0 powinien wygrać
    assert result_dict[0] > result_dict[1]

def test_combine_scores_empty_bm25():
    """Gdy bm25_ranked jest pusty, ranking opiera się wyłącznie na cosine."""
    reranker = Bm25Reranker()
    result = reranker.combine_scores(cosine_ranked=[2, 0, 1], bm25_ranked=[])
    indices = [idx for idx, _ in result]
    assert indices == [2, 0, 1]

def test_combine_scores_rrf_formula():
    """Weryfikacja wartości RRF dla prostego przypadku."""
    reranker = Bm25Reranker()
    k = 60
    result = reranker.combine_scores(cosine_ranked=[0], bm25_ranked=[0], k=k)
    expected = 1 / (k + 0) + 1 / (k + 0)
    assert abs(result[0][1] - expected) < 1e-9


# ── rerank ────────────────────────────────────────────────────────────────────

def test_rerank_best_match_first():
    """Kandydat z największą liczbą pasujących tokenów powinien być na pozycji 0."""
    reranker = Bm25Reranker()
    candidates = [
        "ORNO OR-WE-520 / Dane techniczne",
        "EASTRON SDM630 / Rejestry",
        "ORNO OR-WE-520 / napięcie znamionowe OR-WE-520",
    ]
    result = reranker.rerank("napięcie OR-WE-520", candidates)
    best_idx = result[0][0]
    assert best_idx == 2

def test_rerank_returns_all_candidates():
    """rerank zwraca wynik dla każdego kandydata."""
    reranker = Bm25Reranker()
    candidates = ["licznik energii", "napięcie fazowe", "prąd znamionowy"]
    result = reranker.rerank("napięcie", candidates)
    assert len(result) == 3

def test_rerank_diacritics_insensitive():
    """Zapytanie bez polskich znaków trafia w tekst z polskimi znakami."""
    reranker = Bm25Reranker()
    candidates = ["hasło dostępu", "coś innego"]
    result = reranker.rerank("haslo", candidates)
    assert result[0][0] == 0
