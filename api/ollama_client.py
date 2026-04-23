import time
import httpx
from fastapi import HTTPException


class OllamaClient:
    def __init__(self, base_url: str, model: str, embed_model: str):
        self.base_url = base_url
        self.model = model
        self.embed_model = embed_model

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/api/embed",
                json={"model": self.embed_model, "input": text},
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Embed error: {resp.text}")
        return resp.json()["embeddings"][0]

    async def generate(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        system: str | None = None,
    ) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system or ""},
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
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            elapsed = time.perf_counter() - t0

        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Ollama error: {resp.text}")

        data = resp.json()
        data["_wall_time"] = elapsed
        data["response"] = data.get("message", {}).get("content", "")
        return data
