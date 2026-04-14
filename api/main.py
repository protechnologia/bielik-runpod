"""
Bielik test API – endpoint do mierzenia czasu generowania + RAG przez Qdrant.
"""
import os
import time
import uuid
import tempfile
import httpx
from docling.document_converter import DocumentConverter
from docling.chunking import HierarchicalChunker
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL = os.getenv("MODEL", "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
QDRANT_PATH = os.getenv("QDRANT_PATH", "/root/data/qdrant")

# nomic-embed-text produkuje wektory 768-wymiarowe
VECTOR_SIZE = 768
DEFAULT_COLLECTION = "documents"

SYSTEM_PROMPT = (
    "Jesteś pomocnym asystentem języka polskiego. "
    "Zawsze odpowiadaj po polsku, chyba że użytkownik wyraźnie poprosi o inny język. "
    "Odpowiadaj zwięźle i konkretnie, zgodnie z poleceniem użytkownika. "
    "Jeśli pytanie dotyczy aktualnych danych jak dzisiejsza data lub pogoda, "
    "poinformuj że nie masz dostępu do takich informacji."
)

RAG_SYSTEM_PROMPT = (
    "Jesteś pomocnym asystentem języka polskiego. "
    "Odpowiadaj wyłącznie na podstawie podanego kontekstu. "
    "Jeśli odpowiedź nie wynika z kontekstu, powiedz wprost że nie wiesz. "
    "Zawsze odpowiadaj po polsku."
)

app = FastAPI(title="Bielik test API")

# Qdrant – lokalny tryb embedded, dane persystują na Volume
qdrant = QdrantClient(path=QDRANT_PATH)

# Docling – konwerter PDF, model TableFormer ładowany raz przy starcie
pdf_converter = DocumentConverter()
pdf_chunker = HierarchicalChunker()


def ensure_collection(collection: str = DEFAULT_COLLECTION):
    if not qdrant.collection_exists(collection):
        qdrant.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


# ── Modele Pydantic ────────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    prompt: str
    max_tokens: int = 512
    temperature: float = 0.1
    rag: bool = False
    collection: str = DEFAULT_COLLECTION
    rag_top_k: int = 3


class AskResponse(BaseModel):
    answer: str
    model: str
    time_total_s: float
    time_to_first_token_s: float | None
    tokens_generated: int | None
    tokens_per_second: float | None
    rag_chunks_used: int | None = None


class IngestRequest(BaseModel):
    texts: list[str]
    collection: str = DEFAULT_COLLECTION
    metadata: list[dict] | None = None


class IngestResponse(BaseModel):
    ingested: int
    collection: str


class IngestPdfResponse(BaseModel):
    filename: str
    pages: int
    chunks: int
    ingested: int
    collection: str


class PullRequest(BaseModel):
    model: str | None = None


# ── Helpery Ollama ─────────────────────────────────────────────────────────────

async def ollama_embed(text: str) -> list[float]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": text},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Embed error: {resp.text}")
    return resp.json()["embeddings"][0]


async def ollama_generate(
    prompt: str,
    max_tokens: int,
    temperature: float,
    system_override: str | None = None,
) -> dict:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_override or SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "temperature": temperature,
        },
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        t0 = time.perf_counter()
        resp = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
        elapsed = time.perf_counter() - t0

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Ollama error: {resp.text}")

    data = resp.json()
    data["_wall_time"] = elapsed
    data["response"] = data.get("message", {}).get("content", "")
    return data


# ── Endpointy ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
        models = [m["name"] for m in r.json().get("models", [])]
        model_ready = any(MODEL in m for m in models)
        embed_ready = any(EMBED_MODEL in m for m in models)
        collections = [c.name for c in qdrant.get_collections().collections]
        return {
            "status": "ok",
            "ollama": "reachable",
            "model": MODEL,
            "model_ready": model_ready,
            "embed_model": EMBED_MODEL,
            "embed_ready": embed_ready,
            "available_models": models,
            "qdrant_collections": collections,
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.post("/pull")
async def pull_model(req: PullRequest = PullRequest()):
    model = req.model or MODEL
    async with httpx.AsyncClient(timeout=600.0) as client:
        r = await client.post(
            f"{OLLAMA_URL}/api/pull",
            json={"name": model, "stream": False},
        )
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=r.text)
    return {"status": "pulled", "model": model}


@app.post("/ingest", response_model=IngestResponse)
async def ingest(req: IngestRequest):
    """Dodaje teksty do kolekcji Qdrant (chunki dokumentów)."""
    ensure_collection(req.collection)

    points = []
    for i, text in enumerate(req.texts):
        vector = await ollama_embed(text)
        meta = (req.metadata[i] if req.metadata and i < len(req.metadata) else {})
        points.append(
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={"text": text, **meta},
            )
        )

    qdrant.upsert(collection_name=req.collection, points=points)
    return IngestResponse(ingested=len(points), collection=req.collection)


@app.post("/ingest/pdf", response_model=IngestPdfResponse)
async def ingest_pdf(
    file: UploadFile = File(...),
    collection: str = Form(DEFAULT_COLLECTION),
):
    """Przyjmuje plik PDF, dzieli na chunki i zapisuje do Qdrant."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Plik musi być w formacie PDF.")

    pdf_bytes = await file.read()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        result = pdf_converter.convert(tmp_path)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Nie można przetworzyć PDF: {e}")
    finally:
        os.unlink(tmp_path)

    num_pages = len(result.document.pages)
    all_chunks = list(pdf_chunker.chunk(result.document))
    ensure_collection(collection)

    points = []
    for i, chunk in enumerate(all_chunks):
        vector = await ollama_embed(chunk.text)
        points.append(PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "text": chunk.text,
                "source": file.filename,
                "chunk": i + 1,
                "total_chunks": len(all_chunks),
            },
        ))

    qdrant.upsert(collection_name=collection, points=points)

    return IngestPdfResponse(
        filename=file.filename,
        pages=num_pages,
        chunks=len(all_chunks),
        ingested=len(points),
        collection=collection,
    )


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest):
    rag_chunks_used = None
    prompt_to_send = req.prompt
    system = None

    if req.rag:
        ensure_collection(req.collection)
        query_vector = await ollama_embed(req.prompt)
        hits = qdrant.search(
            collection_name=req.collection,
            query_vector=query_vector,
            limit=req.rag_top_k,
            score_threshold=0.3,
        )
        if hits:
            context = "\n\n".join(
                f"[Fragment {j+1}]\n{hit.payload['text']}"
                for j, hit in enumerate(hits)
            )
            prompt_to_send = (
                f"Kontekst:\n{context}\n\n"
                f"Pytanie: {req.prompt}"
            )
            system = RAG_SYSTEM_PROMPT
            rag_chunks_used = len(hits)

    data = await ollama_generate(prompt_to_send, req.max_tokens, req.temperature, system)

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
    )


@app.get("/models")
async def list_models():
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(f"{OLLAMA_URL}/api/tags")
    return r.json()


@app.get("/collections")
async def list_collections():
    """Zwraca kolekcje Qdrant z liczbą wektorów."""
    result = []
    for c in qdrant.get_collections().collections:
        info = qdrant.get_collection(c.name)
        result.append({
            "name": c.name,
            "vectors_count": info.vectors_count,
        })
    return result


@app.delete("/collections/{collection}")
async def delete_collection(collection: str):
    """Usuwa kolekcję wraz ze wszystkimi wektorami."""
    qdrant.delete_collection(collection)
    return {"deleted": collection}
