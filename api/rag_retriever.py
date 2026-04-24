from pydantic import BaseModel
from schemas import RagChunk
from qdrant_store import QdrantStore
from ollama_client import OllamaClient


class RagResult(BaseModel):
    """
    Wynik wyszukiwania RAG.

    prompt: oryginalny prompt użytkownika poprzedzony fragmentami z Qdrant
            w formacie "Kontekst:\n[Fragment 1]\n...\n\nPytanie: <prompt>".
    chunks: fragmenty użyte do budowy kontekstu, zwracane w odpowiedzi API.
    """

    prompt: str
    chunks: list[RagChunk]


class RagRetriever:
    """Wyszukuje fragmenty z Qdrant pasujące do zapytania i buduje z nich kontekst RAG."""

    def __init__(self, store: QdrantStore, ollama: OllamaClient):
        """
        Args:
            store:  klient Qdrant do wyszukiwania wektorowego
            ollama: klient Ollama do embeddingu zapytania
        """
        self.store = store
        self.ollama = ollama

    async def retrieve(
        self,
        prompt: str,
        collection: str,
        top_k: int,
        score_threshold: float,
    ) -> RagResult | None:
        """
        Wyszukuje fragmenty pasujące do promptu i zwraca gotowy kontekst RAG.
        Zwraca None jeśli żaden fragment nie przekroczył progu score_threshold.

        Args:
            prompt: zapytanie użytkownika
            collection: nazwa kolekcji Qdrant
            top_k: maksymalna liczba zwracanych fragmentów
            score_threshold: minimalny próg podobieństwa wektorowego

        Returns:
            RagResult z kontekstem i listą chunków, lub None jeśli brak trafień.
        """
        self.store.ensure_collection(collection)
        vector = await self.ollama.embed(prompt)
        hits = self.store.search(collection, vector, top_k, score_threshold)

        if not hits:
            return None

        context = "\n\n".join(
            f"[Fragment {j+1}]\n{hit['payload']['text']}" for j, hit in enumerate(hits)
        )
        chunks = [
            RagChunk(
                index        = j + 1,
                score        = round(hit["score"], 4),
                source_label = hit["payload"].get("source_label"),
                sheet        = hit["payload"].get("sheet"),
                text         = hit["payload"]["text"],
            )
            for j, hit in enumerate(hits)
        ]

        return RagResult(
            prompt = f"Kontekst:\n{context}\n\nPytanie: {prompt}",
            chunks = chunks,
        )
