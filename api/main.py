"""
Bielik test API – endpoint do mierzenia czasu generowania + RAG przez Qdrant.
"""
import os
import time
import uuid
from xlsx_chunker import XlsxChunker, DEFAULT_ROWS_PER_CHUNK
from ollama_client import OllamaClient
import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from qdrant_store import QdrantStore

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL = os.getenv("MODEL", "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
QDRANT_PATH = os.getenv("QDRANT_PATH", "/root/data/qdrant")

# nomic-embed-text produkuje wektory 768-wymiarowe
VECTOR_SIZE = 768
DEFAULT_COLLECTION = "documents"

# Prompt używany przy zwykłych zapytaniach (rag=false).
# Nie ogranicza wiedzy modelu — odpowiada na podstawie własnych danych treningowych.
SYSTEM_PROMPT = (
    "Jesteś pomocnym asystentem języka polskiego. "
    "Zawsze odpowiadaj po polsku, chyba że użytkownik wyraźnie poprosi o inny język. "
    "Odpowiadaj zwięźle i konkretnie, zgodnie z poleceniem użytkownika. "
    "Jeśli pytanie dotyczy aktualnych danych jak dzisiejsza data lub pogoda, "
    "poinformuj że nie masz dostępu do takich informacji."
)

# Prompt używany gdy RAG jest aktywny (rag=true).
# Ogranicza model wyłącznie do kontekstu z Qdrant — zapobiega halucynacjom.
# Każdy chunk zaczyna się od prefiksu "{source_label} / {arkusz}", np. "ORNO OR-WE-516 / Rejestry odczytu".
# Instrukcja nakazuje modelowi zawsze cytować to źródło w odpowiedzi.
RAG_SYSTEM_PROMPT = (
    "Jesteś pomocnym asystentem języka polskiego. "
    "Odpowiadaj wyłącznie na podstawie podanego kontekstu. "
    "Każdy fragment kontekstu zaczyna się od nazwy urządzenia lub dokumentu którego dotyczy — "
    "zawsze podawaj tę nazwę w odpowiedzi jako źródło informacji. "
    "Jeśli odpowiedź nie wynika z kontekstu, powiedz wprost że nie wiesz. "
    "Zawsze odpowiadaj po polsku."
)

app = FastAPI(title="Bielik test API")

# Qdrant – lokalny tryb embedded, dane persystują na Volume
store = QdrantStore(path=QDRANT_PATH, vector_size=VECTOR_SIZE)

# Klient Ollama – embed i generate
ollama = OllamaClient(base_url=OLLAMA_URL, model=MODEL, embed_model=EMBED_MODEL)


# ── Modele Pydantic ────────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    prompt: str
    max_tokens: int = 512
    temperature: float = 0.1
    rag: bool = False
    collection: str = DEFAULT_COLLECTION
    rag_top_k: int = 3
    rag_score_threshold: float = 0.3


class RagChunk(BaseModel):
    index: int
    score: float
    source_label: str | None
    sheet: str | None
    text: str


class AskResponse(BaseModel):
    answer: str
    model: str
    time_total_s: float
    time_to_first_token_s: float | None
    tokens_generated: int | None
    tokens_per_second: float | None
    rag_chunks_used: int | None = None
    rag_chunks: list[RagChunk] | None = None


class IngestXlsxResponse(BaseModel):
    filename: str
    sheets: int
    chunks: int
    ingested: int
    collection: str


class ChunkInfo(BaseModel):
    index: int
    sheet: str
    chunk: int
    text: str
    char_count: int
    word_count: int


class InspectXlsxResponse(BaseModel):
    filename: str
    sheets: int
    chunks: int
    items: list[ChunkInfo]


class PullRequest(BaseModel):
    model: str | None = None


# ── Endpointy ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{ollama.base_url}/api/tags")
        models = [m["name"] for m in r.json().get("models", [])]
        model_ready = any(ollama.model in m for m in models)
        embed_ready = any(ollama.embed_model in m for m in models)
        collections = [c["name"] for c in store.list_collections()]
        return {
            "status": "ok",
            "ollama": "reachable",
            "model": ollama.model,
            "model_ready": model_ready,
            "embed_model": ollama.embed_model,
            "embed_ready": embed_ready,
            "available_models": models,
            "qdrant_collections": collections,
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.post("/pull")
async def pull_model(req: PullRequest = PullRequest()):
    model = req.model or ollama.model
    async with httpx.AsyncClient(timeout=600.0) as client:
        r = await client.post(
            f"{ollama.base_url}/api/pull",
            json={"name": model, "stream": False},
        )
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=r.text)
    return {"status": "pulled", "model": model}


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
    if not file.filename.lower().endswith((".xlsx", ".xlsm")):
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
    if not file.filename.lower().endswith((".xlsx", ".xlsm")):
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
    rag_chunks_used = None
    prompt_to_send = req.prompt
    system = None

    rag_chunks = None
    if req.rag:
        store.ensure_collection(req.collection)
        query_vector = await ollama.embed(req.prompt)
        hits = store.search(
            collection=req.collection,
            vector=query_vector,
            top_k=req.rag_top_k,
            score_threshold=req.rag_score_threshold,
        )
        if hits:
            context = "\n\n".join(
                f"[Fragment {j+1}]\n{hit['payload']['text']}"
                for j, hit in enumerate(hits)
            )
            prompt_to_send = (
                f"Kontekst:\n{context}\n\n"
                f"Pytanie: {req.prompt}"
            )
            system = RAG_SYSTEM_PROMPT
            rag_chunks_used = len(hits)
            rag_chunks = [
                RagChunk(
                    index=j + 1,
                    score=round(hit["score"], 4),
                    source_label=hit["payload"].get("source_label"),
                    sheet=hit["payload"].get("sheet"),
                    text=hit["payload"]["text"],
                )
                for j, hit in enumerate(hits)
            ]

    data = await ollama.generate(prompt_to_send, req.max_tokens, req.temperature, system)

    ns = 1e9
    prompt_eval_ns = data.get("prompt_eval_duration", 0)
    eval_ns = data.get("eval_duration", 0)
    eval_count = data.get("eval_count")
    tps = (eval_count / (eval_ns / ns)) if eval_count and eval_ns else None

    return AskResponse(
        answer=data.get("response", ""),
        model=data.get("model", MODEL),
        time_total_s=round(data["_wall_time"], 3),
        time_to_first_token_s=round(prompt_eval_ns / ns, 3) if prompt_eval_ns else None,
        tokens_generated=eval_count,
        tokens_per_second=round(tps, 1) if tps else None,
        rag_chunks_used=rag_chunks_used,
        rag_chunks=rag_chunks,
    )


@app.get("/models")
async def list_models():
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(f"{ollama.base_url}/api/tags")
    return r.json()


@app.get("/collections")
async def list_collections():
    """Zwraca kolekcje Qdrant z liczbą wektorów."""
    return store.list_collections()


@app.delete("/collections/{collection}")
async def delete_collection(collection: str):
    """Usuwa kolekcję wraz ze wszystkimi wektorami."""
    store.delete_collection(collection)
    return {"deleted": collection}
