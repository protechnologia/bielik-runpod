"""
Bielik test API – endpoint do mierzenia czasu generowania + RAG przez Qdrant.
"""
import os
import uuid
from config import MODEL, EMBED_MODEL, VECTOR_SIZE, DEFAULT_COLLECTION, SYSTEM_PROMPT, RAG_SYSTEM_PROMPT
from qdrant_store import QdrantStore
from rag_retriever import RagRetriever
from ollama_client import OllamaClient
from xlsx_chunker import XlsxChunker, DEFAULT_ROWS_PER_CHUNK
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from schemas import  AskRequest, AskResponse, IngestXlsxResponse, ChunkInfo, InspectXlsxResponse, PullRequest

# Zmienne zależne od środowiska — różnią się między RunPodem, lokalnie i testami.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
QDRANT_PATH = os.getenv("QDRANT_PATH", "/root/data/qdrant")

app = FastAPI(title="Bielik test API")

# lokalny tryb embedded, dane persystują na Volume
store = QdrantStore(path=QDRANT_PATH, vector_size=VECTOR_SIZE)
# embed i generacja tekstu
ollama = OllamaClient(base_url=OLLAMA_URL, model=MODEL, embed_model=EMBED_MODEL)
# wyszukiwanie kontekstu do promptu
rag_retriever = RagRetriever(store, ollama)


# ── Endpointy ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
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
async def pull_model(req: PullRequest = PullRequest()):
    return await ollama.pull_model(req.model)


@app.post("/ingest/xlsx", response_model=IngestXlsxResponse)
async def ingest_xlsx(
    file: UploadFile = File(...),
    source_label: str = Form(...),
    collection: str = Form(DEFAULT_COLLECTION),
    rows_per_chunk: int = Form(DEFAULT_ROWS_PER_CHUNK),
):
    """
    Przyjmuje plik XLSX, dzieli na chunki i zapisuje do Qdrant.
    Każdy arkusz traktowany jest jako osobne urządzenie/kategoria.
    Prefiks każdego chunku: '{source_label} / {nazwa_arkusza}'.
    """
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Plik musi być w formacie XLSX.")

    file_bytes = await file.read()

    try:
        chunks = XlsxChunker(rows_per_chunk).chunk(file_bytes, source_label)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Nie można przetworzyć XLSX: {e}")

    if not chunks:
        raise HTTPException(status_code=422, detail="Plik XLSX nie zawiera danych.")

    sheet_names = list(dict.fromkeys(c["sheet"] for c in chunks))
    store.ensure_collection(collection)

    points = []
    for chunk in chunks:
        vector = await ollama.embed(chunk["text"])
        points.append({
            "id": str(uuid.uuid4()),
            "vector": vector,
            "payload": {
                "text": chunk["text"],
                "source_label": chunk["source_label"],
                "sheet": chunk["sheet"],
                "chunk": chunk["chunk"],
                "source": file.filename,
            },
        })

    store.upsert(collection, points)

    return IngestXlsxResponse(
        filename=file.filename,
        sheets=len(sheet_names),
        chunks=len(chunks),
        ingested=len(points),
        collection=collection,
    )


@app.post("/inspect/xlsx", response_model=InspectXlsxResponse)
async def inspect_xlsx(
    file: UploadFile = File(...),
    source_label: str = Form(...),
    rows_per_chunk: int = Form(DEFAULT_ROWS_PER_CHUNK),
):
    """
    Przyjmuje plik XLSX i zwraca listę chunków bez zapisywania do Qdrant.
    Służy do testowania jakości chunkingu przed właściwym ingestion.
    """
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Plik musi być w formacie XLSX.")

    file_bytes = await file.read()

    try:
        chunks = XlsxChunker(rows_per_chunk).chunk(file_bytes, source_label)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Nie można przetworzyć XLSX: {e}")

    if not chunks:
        raise HTTPException(status_code=422, detail="Plik XLSX nie zawiera danych.")

    sheet_names = list(dict.fromkeys(c["sheet"] for c in chunks))
    items = [
        ChunkInfo(
            index=i + 1,
            sheet=c["sheet"],
            chunk=c["chunk"],
            text=c["text"],
            char_count=len(c["text"]),
            word_count=len(c["text"].split()),
        )
        for i, c in enumerate(chunks)
    ]

    return InspectXlsxResponse(
        filename=file.filename,
        sheets=len(sheet_names),
        chunks=len(items),
        items=items,
    )


# Przykładowa odpowiedź z RAG:
# {
#   "answer": "Napięcie znamionowe licznika ORNO OR-WE-516 wynosi 3x230/400V.",
#   "model": "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0",
#   "time_total_s": 14.2,
#   "time_to_first_token_s": 1.5,
#   "tokens_generated": 104,
#   "tokens_per_second": 7.3,
#   "rag_chunks_used": 2,
#   "rag_chunks": [
#     {
#       "index": 1,
#       "score": 0.8731,
#       "source_label": "ORNO OR-WE-516",
#       "sheet": "Rejestry odczytu",
#       "text": "ORNO OR-WE-516 / Rejestry odczytu\n\nAdres | Nazwa | ..."
#     }
#   ]
# }
@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest):
    """
    Wysyła prompt do modelu i zwraca odpowiedź wraz z metrykami czasu generowania.
    Przy rag=true wyszukuje najpierw kontekst w Qdrant i dokłada go do promptu.
    """
    prompt_to_send  = req.prompt
    system          = SYSTEM_PROMPT
    rag_chunks_used = None
    rag_chunks      = None

    if req.rag:
        result = await rag_retriever.retrieve( req.prompt, req.collection, req.rag_top_k, req.rag_score_threshold )
        if result:
            prompt_to_send  = result.prompt
            system          = RAG_SYSTEM_PROMPT
            rag_chunks_used = len(result.chunks)
            rag_chunks      = result.chunks

    data = await ollama.generate( prompt_to_send, req.max_tokens, req.temperature, system )

    ns             = 1e9
    prompt_eval_ns = data.get("prompt_eval_duration", 0)
    eval_ns        = data.get("eval_duration", 0)
    eval_count     = data.get("eval_count")
    tps            = (eval_count / (eval_ns / ns)) if eval_count and eval_ns else None

    return AskResponse(
        answer               = data.get("response", ""),
        model                = data.get("model", MODEL),
        time_total_s         = round(data["_wall_time"], 3),
        time_to_first_token_s= round(prompt_eval_ns / ns, 3) if prompt_eval_ns else None,
        tokens_generated     = eval_count,
        tokens_per_second    = round(tps, 1) if tps else None,
        rag_chunks_used      = rag_chunks_used,
        rag_chunks           = rag_chunks,
    )


@app.get("/models")
async def list_models():
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
async def list_collections():
    """
    Zwraca listę kolekcji Qdrant z liczbą zapisanych wektorów.

    Przykład odpowiedzi:
        [
            {"name": "documents", "vectors_count": 42}
        ]
    """
    return store.list_collections()


@app.delete("/collections/{collection}")
async def delete_collection(collection: str):
    """
    Usuwa kolekcję wraz ze wszystkimi wektorami i metadanymi.

    Operacja nieodwracalna — dane trzeba wgrać ponownie przez /ingest/xlsx.

    Przykład odpowiedzi:
        {"deleted": "documents"}
    """
    store.delete_collection(collection)
    return {"deleted": collection}