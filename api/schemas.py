"""
Modele Pydantic — schematy requestów i odpowiedzi API.
"""
from pydantic import BaseModel
from config import DEFAULT_COLLECTION


class AskRequest(BaseModel):
    """
    Parametry zapytania do modelu.

    Używany: POST /ask (request body)

    Przykład (rag=true, z BM25 i query routerem):
        {
            "prompt": "Jakie jest napięcie znamionowe licznika ORNO OR-WE-516?",
            "max_tokens": 512,
            "temperature": 0.1,
            "rag": true,
            "collection": "documents",
            "rag_top_k": 3,
            "rag_score_threshold": 0.3,
            "bm25_candidates": 20,
            "query_router": true
        }

    prompt:            treść pytania użytkownika.
    max_tokens:        maksymalna liczba tokenów do wygenerowania (num_predict w Ollama).
    temperature:       temperatura próbkowania; niższe wartości = bardziej deterministyczne odpowiedzi.
    rag:               jeśli true, przed generowaniem wyszukuje kontekst w Qdrant.
    collection:        nazwa kolekcji Qdrant do przeszukania.
    rag_top_k:         liczba fragmentów RAG dołączanych do kontekstu modelu.
    rag_score_threshold: minimalny próg podobieństwa wektorowego; fragmenty poniżej progu są odrzucane.
    bm25_candidates:   liczba kandydatów pobieranych z Qdrant przed rerankingiem BM25;
                       0 wyłącza BM25 i zwraca bezpośrednio top rag_top_k z Qdrant.
    query_router:      jeśli true i rag=true, przed wyszukiwaniem identyfikuje urządzenie
                       i filtruje Qdrant po source_label; false = przeszukuje całą kolekcję.
    trim_to_sentence:  jeśli true i odpowiedź została ucięta przez limit tokenów
                       (done_reason == "length"), przycina ją do ostatniego pełnego zdania.
    """

    prompt: str
    max_tokens: int = 512
    temperature: float = 0.1
    rag: bool = False
    collection: str = DEFAULT_COLLECTION
    rag_top_k: int = 3
    rag_score_threshold: float = 0.3
    bm25_candidates: int = 20
    query_router: bool = False
    trim_to_sentence: bool = True


class RagChunk(BaseModel):
    """
    Pojedynczy fragment z Qdrant użyty do budowy kontekstu RAG.

    Używany: pole rag_chunks w AskResponse, pole chunks w RagResult (rag_retriever.py)

    Przykład:
        {
            "index": 1,
            "score": 0.8731,
            "source_label": "ORNO OR-WE-516",
            "sheet": "Rejestry odczytu",
            "text": "ORNO OR-WE-516 / Rejestry odczytu\n\nAdres | Nazwa | ..."
        }
    """

    index: int
    score: float
    source_label: str | None
    sheet: str | None
    text: str


class AskResponse(BaseModel):
    """
    Odpowiedź modelu z metrykami czasu generowania i opcjonalnymi chunkami RAG.

    Używany: POST /ask (response body)

    Przykład:
        {
            "answer": "Napięcie znamionowe wynosi 3x230/400V.",
            "model": "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0",
            "time_total_s": 14.2,
            "time_to_first_token_s": 1.5,
            "tokens_generated": 104,
            "tokens_per_second": 7.3,
            "truncated": false,
            "rag_chunks_used": 2,
            "rag_chunks": [...]
        }

    answer:               treść odpowiedzi wygenerowana przez model.
    model:                nazwa modelu użytego do generowania.
    time_total_s:         całkowity czas żądania do Ollamy w sekundach.
    time_to_first_token_s: czas od wysłania żądania do wygenerowania pierwszego tokenu
                           (prompt_eval_duration z Ollamy); None jeśli niedostępne.
    tokens_generated:     liczba wygenerowanych tokenów (eval_count z Ollamy); None jeśli niedostępne.
    tokens_per_second:    prędkość generowania tokenów; None jeśli niedostępne.
    truncated:            true jeśli Ollama urwała generowanie po osiągnięciu limitu tokenów
                          (done_reason == "length"); false przy naturalnym zakończeniu lub braku info.
    rag_chunks_used:      liczba fragmentów RAG dołączonych do kontekstu; None jeśli rag=false.
    rag_chunks:           szczegóły fragmentów RAG użytych do budowy kontekstu; None jeśli rag=false.
    """

    answer: str
    model: str
    time_total_s: float
    time_to_first_token_s: float | None
    tokens_generated: int | None
    tokens_per_second: float | None
    truncated: bool = False
    rag_chunks_used: int | None = None
    rag_chunks: list[RagChunk] | None = None


class IngestXlsxResponse(BaseModel):
    """
    Podsumowanie operacji ingestion pliku XLSX do Qdrant.

    Używany: POST /ingest/xlsx (response body)

    Przykład:
        {
            "filename": "liczniki.xlsx",
            "sheets": 2,
            "chunks": 8,
            "ingested": 8,
            "collection": "documents"
        }
    """

    filename: str
    sheets: int
    chunks: int
    ingested: int
    collection: str


class ChunkInfo(BaseModel):
    """
    Szczegóły pojedynczego chunku z metrykami tekstu.

    Używany: pole items w InspectXlsxResponse

    Przykład:
        {
            "index": 1,
            "sheet": "Rejestry odczytu",
            "chunk": 1,
            "text": "ORNO OR-WE-516 / Rejestry odczytu\n\nAdres | Nazwa | ...",
            "char_count": 312,
            "word_count": 48
        }
    """

    index: int
    sheet: str
    chunk: int
    text: str
    char_count: int
    word_count: int


class InspectXlsxResponse(BaseModel):
    """
    Wynik podglądu chunków pliku XLSX bez zapisu do Qdrant.

    Używany: POST /inspect/xlsx (response body)

    Przykład:
        {
            "filename": "liczniki.xlsx",
            "sheets": 2,
            "chunks": 4,
            "items": [
                {
                    "index": 1,
                    "sheet": "Rejestry odczytu",
                    "chunk": 1,
                    "text": "ORNO OR-WE-516 / Rejestry odczytu\n\n...",
                    "char_count": 312,
                    "word_count": 48
                }
            ]
        }
    """

    filename: str
    sheets: int
    chunks: int
    items: list[ChunkInfo]


class PullRequest(BaseModel):
    """
    Parametry żądania pobrania modelu.

    Używany: POST /pull (request body)

    Przykład:
        {"model": "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0"}

    Bez podania modelu pobierany jest domyślny model generowania (z config.MODEL).
    """

    model: str | None = None
