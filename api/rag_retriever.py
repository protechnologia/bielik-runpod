from pydantic      import BaseModel
from schemas       import RagChunk
from qdrant_store  import QdrantStore
from ollama_client import OllamaClient
from bm25_reranker import Bm25Reranker


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

    def __init__(self, store: QdrantStore, ollama: OllamaClient, bm25: Bm25Reranker | None = None):
        """
        Args:
            store:  klient Qdrant do wyszukiwania wektorowego
            ollama: klient Ollama do embeddingu zapytania
            bm25:   reranker BM25; None wyłącza reranking niezależnie od bm25_candidates
        """
        self.store  = store
        self.ollama = ollama
        self.bm25   = bm25

    async def retrieve( self, prompt: str, collection: str, top_k: int, score_threshold: float, bm25_candidates: int = 0 ) -> RagResult | None:
        """
        Wyszukuje fragmenty pasujące do promptu i zwraca gotowy kontekst RAG.
        Zwraca None jeśli żaden fragment nie przekroczył progu score_threshold.

        Gdy bm25_candidates > 0 i self.bm25 jest ustawiony, pobiera bm25_candidates
        fragmentów z Qdrant, reankuje je przez BM25 i zwraca top_k najlepszych.
        W przeciwnym razie pobiera bezpośrednio top_k fragmentów z Qdrant.

        Args:
            prompt:          zapytanie użytkownika, np. "Jakie jest napięcie znamionowe licznika ORNO OR-WE-516?"
            collection:      nazwa kolekcji Qdrant, np. "documents"
            top_k:           maksymalna liczba zwracanych fragmentów, np. 3
            score_threshold: minimalny próg podobieństwa wektorowego, np. 0.3
            bm25_candidates: liczba kandydatów pobieranych z Qdrant przed rerankingiem BM25; 0 wyłącza BM25 (domyślnie: 0), np. 20

        Returns:
            RagResult z kontekstem i listą chunków, lub None jeśli brak trafień.
            Przykład:
                RagResult(
                    prompt="Kontekst:\n[Fragment 1]\nORNO OR-WE-516 / Dane techniczne\n\n"
                           "Pytanie: Jakie jest napięcie znamionowe licznika ORNO OR-WE-516?",
                    chunks=[
                        RagChunk(index=1, score=0.8731, source_label="ORNO OR-WE-516",
                                 sheet="Dane techniczne", text="ORNO OR-WE-516 / Dane techniczne\n..."),
                    ]
                )
        """
        self.store.ensure_collection(collection)
        vector = await self.ollama.embed(prompt)

        use_bm25 = self.bm25 is not None and bm25_candidates > 0
        fetch_k  = bm25_candidates if use_bm25 else top_k
        hits     = self.store.search(collection, vector, fetch_k, score_threshold)

        if not hits:
            return None

        if use_bm25:
            texts          = [hit["payload"]["text"] for hit in hits]
            bm25_result    = self.bm25.rerank(prompt, texts)
            cosine_ranked  = list(range(len(hits)))
            bm25_ranked    = [idx for idx, score in bm25_result if score > 0]
            combined       = self.bm25.combine_scores(cosine_ranked, bm25_ranked)
            hits           = [hits[idx] for idx, _ in combined[:top_k]]

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
