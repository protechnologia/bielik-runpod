import time
import httpx
from fastapi import HTTPException


class OllamaClient:
    """
    Klient HTTP do komunikacji z Ollama API.

    Odpowiada wyłącznie za transport — wysyłanie requestów i parsowanie odpowiedzi.
    Nie zawiera logiki aplikacji (promptów systemowych, strategii RAG itp.).
    """

    def __init__(self, base_url: str, model: str, embed_model: str):
        """
        Args:
            base_url:    Adres serwera Ollama, np. 'http://localhost:11434'.
            model:       Nazwa modelu do generowania odpowiedzi, np. 'bielik-11b-v3.0'.
            embed_model: Nazwa modelu do embeddingu, np. 'nomic-embed-text'.
        """
        self.base_url = base_url
        self.model = model
        self.embed_model = embed_model

    async def embed(self, text: str) -> list[float]:
        """
        Zwraca wektor embeddingu dla podanego tekstu.

        Wywołuje /api/embed na modelu embed_model.
        Rzuca HTTPException(502) jeśli Ollama zwróci błąd.

        Request do Ollama:
            POST /api/embed
            {
                "model": "nomic-embed-text",
                "input": "Jakie jest napięcie znamionowe?"
            }

        Response z Ollama:
            {
                "embeddings": [[0.1, 0.2, ..., 0.768]]
            }

        Metoda zwraca tylko wewnętrzną listę: [0.1, 0.2, ..., 0.768]
        """
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
        """
        Generuje odpowiedź modelu na podstawie promptu.

        Wywołuje /api/chat na modelu model w trybie niestrumieniowym.
        Zwraca surowy słownik z odpowiedzi Ollamy, wzbogacony o:
          _wall_time — czas całego requestu w sekundach,
          response   — treść odpowiedzi wyciągnięta z message.content.

        Args:
            prompt:      Treść wiadomości użytkownika.
            max_tokens:  Maksymalna liczba tokenów do wygenerowania.
            temperature: Temperatura próbkowania.
            system:      Opcjonalny prompt systemowy. Jeśli None, wysyłany jest pusty string.

        Rzuca HTTPException(502) jeśli Ollama zwróci błąd.

        Request do Ollama:
            POST /api/chat
            {
                "model": "bielik-11b-v3.0",
                "messages": [
                    {"role": "system", "content": "Jesteś pomocnym asystentem..."},
                    {"role": "user",   "content": "Jakie jest napięcie znamionowe?"}
                ],
                "stream": false,
                "options": {"num_predict": 512, "temperature": 0.1}
            }

        Response z Ollama (fragment — pełna spec: https://github.com/ollama/ollama/blob/main/docs/api.md):
            {
                "model": "bielik-11b-v3.0",
                "message": {"role": "assistant", "content": "Napięcie wynosi..."},
                "prompt_eval_duration": 1500000000,
                "eval_duration": 8000000000,
                "eval_count": 104,
                ...
                "_wall_time": 14.2,   <- dodawane przez tę metodę
                "response": "Napięcie wynosi..."  <- dodawane przez tę metodę
            }
        """
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