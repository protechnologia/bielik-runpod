from config import MODEL, SYSTEM_PROMPT, RAG_SYSTEM_PROMPT
from ollama_client import OllamaClient
from rag_retriever import RagRetriever
from schemas import AskRequest, AskResponse


class AskPipeline:
    """Orkiestruje pełny przepływ zapytania: RAG → generowanie → metryki → AskResponse."""

    def __init__(self, ollama: OllamaClient, rag_retriever: RagRetriever):
        """
        Args:
            ollama: klient Ollama do generowania odpowiedzi
            rag_retriever: retriever do wyszukiwania kontekstu z Qdrant
        """
        self.ollama = ollama
        self.rag_retriever = rag_retriever

    async def run(self, req: AskRequest) -> AskResponse:
        """
        Wykonuje pełny pipeline zapytania.

        Args:
            req: parametry zapytania — prompt, flagi RAG, limity tokenów itp.

        Returns:
            Odpowiedź modelu z metrykami czasu generowania i opcjonalnymi chunkami RAG, np.:
                AskResponse(
                    answer="Napięcie znamionowe wynosi 3x230/400V.",
                    model="SpeakLeash/bielik-11b-v3.0-instruct:Q8_0",
                    time_total_s=14.2,
                    time_to_first_token_s=1.5,
                    tokens_generated=104,
                    tokens_per_second=7.3,
                    rag_chunks_used=2,
                    rag_chunks=[RagChunk(index=1, score=0.8731, ...)],
                )
        """
        # domyślnie: czysty prompt użytkownika, bez kontekstu
        prompt          = req.prompt
        system          = SYSTEM_PROMPT
        rag_chunks_used = None
        rag_chunks      = None

        # RAG: wyszukaj pasujące fragmenty i przepisz prompt — jeśli nie ma trafień, zostaje oryginał
        if req.rag:
            result = await self.rag_retriever.retrieve(
                req.prompt, req.collection, req.rag_top_k, req.rag_score_threshold
            )
            if result:
                prompt = result.prompt
                system = RAG_SYSTEM_PROMPT
                rag_chunks_used = len(result.chunks)
                rag_chunks = result.chunks

        # generowanie — blokuje do końca odpowiedzi (stream=False)
        data = await self.ollama.generate(prompt, req.max_tokens, req.temperature, system)

        # metryki z odpowiedzi Ollamy — czasy w nanosekundach, przeliczamy na sekundy i TPS
        ns             = 1e9
        prompt_eval_ns = data.get("prompt_eval_duration", 0)
        eval_ns        = data.get("eval_duration", 0)
        eval_count     = data.get("eval_count")
        tps = (eval_count / (eval_ns / ns)) if eval_count and eval_ns else None

        return AskResponse(
            answer                = data.get("response", ""),
            model                 = data.get("model", MODEL),
            time_total_s          = round(data["_wall_time"], 3),
            time_to_first_token_s = round(prompt_eval_ns / ns, 3) if prompt_eval_ns else None,
            tokens_generated      = eval_count,
            tokens_per_second     = round(tps, 1) if tps else None,
            rag_chunks_used       = rag_chunks_used,
            rag_chunks            = rag_chunks,
        )
