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

    async def list_models(self) -> dict:
        """
        Zwraca listę modeli załadowanych w Ollama.

        Wywołuje /api/tags i zwraca surową odpowiedź Ollamy.
        Rzuca HTTPException(502) jeśli Ollama zwróci błąd.

        Response z Ollama:
            {
                "models": [
                    {
                        "name": "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0",
                        "size": 11800000000,
                        "digest": "sha256:...",
                        ...
                    },
                    {
                        "name": "nomic-embed-text:latest",
                        ...
                    }
                ]
            }
        """
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{self.base_url}/api/tags")
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Ollama error: {resp.text}")
        return resp.json()

    async def pull_model(self, model: str | None = None) -> dict:
        """
        Pobiera model przez Ollama. Jeśli model nie zostanie podany,
        używa domyślnego modelu generowania (self.model).

        Rzuca HTTPException(502) jeśli Ollama zwróci błąd.

        Request do Ollama:
            POST /api/pull
            {
                "name": "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0",
                "stream": false
            }

        Response z Ollama:
            {"status": "success"}
        """
        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(
                f"{self.base_url}/api/pull",
                json={"name": model or self.model, "stream": False},
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=resp.text)
        return {"status": "pulled", "model": model or self.model}

    async def check(self) -> dict:
        """
        Sprawdza osiągalność Ollamy i status załadowanych modeli.

        Używane przez endpoint /health. Odpytuje /api/tags bezpośrednio,
        z pominięciem list_models() — żeby health check testował połączenie
        niezależnie od reszty kodu klienta.

        Rzuca wyjątek (httpx.ConnectError, httpx.TimeoutException itp.)
        jeśli Ollama jest nieosiągalna — health endpoint łapie go przez
        ogólny except i zwraca {"status": "error"}.

        Przykład zwracanego słownika:
            {
                "reachable": true,
                "model": "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0",
                "model_ready": true,
                "embed_model": "nomic-embed-text",
                "embed_ready": true,
                "available_models": [
                    "SpeakLeash/bielik-11b-v3.0-instruct:Q8_0",
                    "nomic-embed-text:latest"
                ]
            }
        """
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{self.base_url}/api/tags")
        models = [m["name"] for m in r.json().get("models", [])]
        return {
            "reachable": True,
            "model": self.model,
            "model_ready": any(self.model in m for m in models),
            "embed_model": self.embed_model,
            "embed_ready": any(self.embed_model in m for m in models),
            "available_models": models,
        }