"""
Bielik test API – prosty endpoint do mierzenia czasu generowania.
"""
import os
import time
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL = os.getenv("MODEL", "SpeakLeash/bielik-1.5b-v3.0-instruct:Q8_0")

SYSTEM_PROMPT = (
    "Jesteś pomocnym asystentem języka polskiego. "
    "Zawsze odpowiadaj po polsku, chyba że użytkownik wyraźnie poprosi o inny język. "
    "Odpowiadaj zwięźle i konkretnie, zgodnie z poleceniem użytkownika. "
    "Jeśli pytanie dotyczy aktualnych danych jak dzisiejsza data lub pogoda, "
    "poinformuj że nie masz dostępu do takich informacji."
)

app = FastAPI(title="Bielik test API")


class AskRequest(BaseModel):
    prompt: str
    max_tokens: int = 512
    temperature: float = 0.1


class AskResponse(BaseModel):
    answer: str
    model: str
    time_total_s: float
    time_to_first_token_s: float | None
    tokens_generated: int | None
    tokens_per_second: float | None


class PullRequest(BaseModel):
    model: str | None = None


async def ollama_generate(prompt: str, max_tokens: int, temperature: float) -> dict:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
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


@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
        models = [m["name"] for m in r.json().get("models", [])]
        model_ready = any(MODEL in m for m in models)
        return {
            "status": "ok",
            "ollama": "reachable",
            "model": MODEL,
            "model_ready": model_ready,
            "available_models": models,
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


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest):
    data = await ollama_generate(req.prompt, req.max_tokens, req.temperature)

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
    )


@app.get("/models")
async def list_models():
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(f"{OLLAMA_URL}/api/tags")
    return r.json()