"""
Bielik test API – endpoint do mierzenia czasu generowania + RAG przez Qdrant.
"""
import os
from functools import lru_cache
from config import MODEL, ROUTER_MODEL, EMBED_MODEL, VECTOR_SIZE, DEFAULT_COLLECTION
from fastapi import Depends, FastAPI, File, Form, UploadFile
from ollama_client import OllamaClient
from qdrant_store import QdrantStore
from bm25_reranker import Bm25Reranker
from rag_retriever import RagRetriever
from query_router import QueryRouter
from ask_pipeline import AskPipeline
from xlsx_chunker import DEFAULT_ROWS_PER_CHUNK
from xlsx_ingester import XlsxIngester
from schemas import AskRequest, AskResponse, IngestXlsxResponse, InspectXlsxResponse, PullRequest

# Zmienne zależne od środowiska — różnią się między RunPodem, lokalnie i testami.
OLLAMA_URL  = os.getenv("OLLAMA_URL", "http://localhost:11434")
QDRANT_PATH = os.getenv("QDRANT_PATH", "/root/data/qdrant")

app = FastAPI(title="Bielik test API")


# ── Fabryki zależności ────────────────────────────────────────────────────────

@lru_cache
def get_store() -> QdrantStore:
    return QdrantStore(path=QDRANT_PATH, vector_size=VECTOR_SIZE)


@lru_cache
def get_ollama() -> OllamaClient:
    return OllamaClient(base_url=OLLAMA_URL, model=MODEL, embed_model=EMBED_MODEL)


@lru_cache
def get_router_ollama() -> OllamaClient:
    return OllamaClient(base_url=OLLAMA_URL, model=ROUTER_MODEL, embed_model=EMBED_MODEL)


@lru_cache
def get_bm25() -> Bm25Reranker:
    return Bm25Reranker()


@lru_cache
def get_rag_retriever() -> RagRetriever:
    return RagRetriever(get_store(), get_ollama(), get_bm25())


@lru_cache
def get_query_router() -> QueryRouter:
    return QueryRouter(get_router_ollama())


@lru_cache
def get_xlsx_ingester() -> XlsxIngester:
    return XlsxIngester(get_store(), get_ollama())


@lru_cache
def get_ask_pipeline() -> AskPipeline:
    return AskPipeline(get_ollama(), get_rag_retriever(), get_query_router())


# ── Endpointy ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health(
    ollama: OllamaClient = Depends(get_ollama),
    store: QdrantStore   = Depends(get_store),
):
    """
    Zwraca status Ollamy, modeli i kolekcji Qdrant.

    Przykład odpowiedzi gdy wszystko działa:
        {
            "status": "ok",
            "ollama": {
                "reachable": true,
                "model": "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0",
                "model_ready": true,
                "embed_model": "nomic-embed-text",
                "embed_ready": true,
                "available_models": [
                    "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0",
                    "nomic-embed-text:latest"
                ]
            },
            "qdrant": {
                "collections": ["documents"]
            }
        }

    Przykład odpowiedzi gdy Ollama jest nieosiągalna:
        {
            "status": "error",
            "detail": "All connection attempts failed"
        }
    """
    try:
        return {
            "status": "ok",
            "ollama": await ollama.check(),
            "qdrant": {
                "collections": [c["name"] for c in store.list_collections()],
            },
        }
    except Exception as e:
        # Każdy wyjątek zwraca 200 z "error" — health check nigdy nie rzuca wyjątku.
        return {"status": "error", "detail": str(e)}


@app.post("/pull")
async def pull_model(
    req: PullRequest      = PullRequest(),
    ollama: OllamaClient  = Depends(get_ollama),
):
    """
    Pobiera model przez Ollama. Bez podania modelu pobiera domyślny model generowania.

    Przykład requestu:
        {"model": "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0"}

    Przykład odpowiedzi:
        {"status": "pulled", "model": "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0"}
    """
    return await ollama.pull_model(req.model)


@app.post("/ingest/xlsx", response_model=IngestXlsxResponse)
async def ingest_xlsx(
    file: UploadFile               = File(...),
    source_label: str              = Form(...),
    collection: str                = Form(DEFAULT_COLLECTION),
    rows_per_chunk: int            = Form(DEFAULT_ROWS_PER_CHUNK),
    ingester: XlsxIngester         = Depends(get_xlsx_ingester),
):
    """
    Przyjmuje plik XLSX, dzieli na chunki i zapisuje do Qdrant.
    Każdy arkusz traktowany jest jako osobne urządzenie/kategoria.
    Prefiks każdego chunku: '{source_label} / {nazwa_arkusza}'.

    Request (multipart/form-data):
        file:           plik XLSX
        source_label:   "ORNO OR-WE-516"
        collection:     "documents"
        rows_per_chunk: 20

    Przykład odpowiedzi:
        {
            "filename": "liczniki.xlsx",
            "sheets": 2,
            "chunks": 8,
            "ingested": 8,
            "collection": "documents"
        }
    """
    return await ingester.ingest(file, source_label, collection, rows_per_chunk)


@app.post("/inspect/xlsx", response_model=InspectXlsxResponse)
async def inspect_xlsx(
    file: UploadFile        = File(...),
    source_label: str       = Form(...),
    rows_per_chunk: int     = Form(DEFAULT_ROWS_PER_CHUNK),
    ingester: XlsxIngester  = Depends(get_xlsx_ingester),
):
    """
    Przyjmuje plik XLSX i zwraca listę chunków bez zapisywania do Qdrant.
    Służy do testowania jakości chunkingu przed właściwym ingestion.

    Request (multipart/form-data):
        file:           plik XLSX
        source_label:   "ORNO OR-WE-516"
        rows_per_chunk: 20

    Przykład odpowiedzi:
        {
            "filename": "liczniki.xlsx",
            "sheets": 2,
            "chunks": 4,
            "items": [
                {
                    "index": 1,
                    "sheet": "Rejestry odczytu",
                    "chunk": 1,
                    "text": "ORNO OR-WE-516 / Rejestry odczytu\n\nAdres | Nazwa | ...",
                    "char_count": 312,
                    "word_count": 48
                }
            ]
        }
    """
    return await ingester.inspect(file, source_label, rows_per_chunk)


@app.post("/ask", response_model=AskResponse)
async def ask(
    req: AskRequest,
    pipeline: AskPipeline = Depends(get_ask_pipeline),
):
    """
    Wysyła prompt do modelu i zwraca odpowiedź wraz z metrykami czasu generowania.
    Przy rag=true wyszukuje najpierw kontekst w Qdrant i dokłada go do promptu.
    Przy bm25_candidates > 0 pobiera więcej kandydatów z Qdrant i reankuje je przez BM25
    przed obcięciem do rag_top_k — poprawia precyzję dla zapytań zawierających nazwy techniczne.
    Przy query_router=true identyfikuje urządzenie z pytania i filtruje Qdrant po source_label;
    jeśli urządzenie nie zostanie rozpoznane, przeszukuje całą kolekcję.

    Przykład requestu (rag=true, z BM25 i query routerem):
        {
            "prompt": "Jakie jest napięcie znamionowe licznika ORNO OR-WE-516?",
            "rag": true,
            "collection": "documents",
            "rag_top_k": 3,
            "rag_score_threshold": 0.3,
            "bm25_candidates": 20,
            "query_router": true
        }

    Przykład odpowiedzi:
        {
            "answer": "Napięcie znamionowe licznika ORNO OR-WE-516 wynosi 3x230/400V.",
            "model": "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0",
            "time_total_s": 14.2,
            "time_to_first_token_s": 1.5,
            "tokens_generated": 104,
            "tokens_per_second": 7.3,
            "rag_chunks_used": 2,
            "rag_chunks": [
                {
                    "index": 1,
                    "score": 0.8731,
                    "source_label": "ORNO OR-WE-516",
                    "sheet": "Rejestry odczytu",
                    "text": "ORNO OR-WE-516 / Rejestry odczytu\n\nAdres | Nazwa | ..."
                }
            ]
        }
    """
    return await pipeline.run(req)


@app.get("/models")
async def list_models(ollama: OllamaClient = Depends(get_ollama)):
    """
    Zwraca listę modeli załadowanych w Ollama.

    Przykład odpowiedzi:
        {
            "models": [
                {
                    "name": "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0",
                    "size": 11800000000,
                    "digest": "sha256:..."
                },
                {
                    "name": "nomic-embed-text:latest",
                    "size": 274000000,
                    "digest": "sha256:..."
                }
            ]
        }
    """
    return await ollama.list_models()


@app.get("/collections")
async def list_collections(store: QdrantStore = Depends(get_store)):
    """
    Zwraca listę kolekcji Qdrant z liczbą zapisanych wektorów.

    Przykład odpowiedzi:
        [
            {"name": "documents", "vectors_count": 42}
        ]
    """
    return store.list_collections()


@app.delete("/collections/{collection}")
async def delete_collection(
    collection: str,
    store: QdrantStore = Depends(get_store),
):
    """
    Usuwa kolekcję wraz ze wszystkimi wektorami i metadanymi.

    Operacja nieodwracalna — dane trzeba wgrać ponownie przez /ingest/xlsx.

    Przykład odpowiedzi:
        {"deleted": "documents"}
    """
    store.delete_collection(collection)
    return {"deleted": collection}
