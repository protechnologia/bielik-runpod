from api.ollama_client import OllamaClient
from api.config import ROUTER_SYSTEM_PROMPT


class QueryRouter:
    """
    Identyfikuje urządzenie, którego dotyczy pytanie użytkownika.

    Używa modelu językowego (ROUTER_MODEL) jako klasyfikatora:
    podaje mu listę dostępnych urządzeń i pytanie użytkownika, a model
    zwraca nazwę urządzenia lub "brak". Wynik jest dopasowywany do listy
    source_label z Qdranta i używany jako filtr wyszukiwania.

    Jeśli router nie rozpozna urządzenia, retriever przeszukuje całą kolekcję.
    """

    def __init__(self, ollama: OllamaClient):
        """
        Args:
            ollama: Klient Ollamy skonfigurowany z ROUTER_MODEL.
        """
        self.ollama = ollama

    async def route(self, prompt: str, source_labels: list[str]) -> str | None:
        """
        Identyfikuje, którego urządzenia dotyczy pytanie.

        Buduje wiadomość z listą urządzeń i pytaniem, wysyła do modelu routera,
        a odpowiedź dopasowuje do source_labels (exact, potem substring).

        Args:
            prompt:        Pytanie użytkownika, np. "napięcie L1 rejestr OR-WE-520".
            source_labels: Lista urządzeń dostępnych w kolekcji,
                           np. ["EASTRON SDM630", "ORNO OR-WE-520"].

        Returns:
            Dopasowany source_label z listy lub None jeśli nie rozpoznano.

        Przykłady:
            route("napięcie L1 OR-WE-520", ["ORNO OR-WE-520", "EASTRON SDM630"])
                → "ORNO OR-WE-520"
            route("jak działa pompa ciepła?", ["ORNO OR-WE-520", "EASTRON SDM630"])
                → None
        """
        if not source_labels:
            return None

        labels_str = "\n".join(f"- {label}" for label in source_labels)
        user_message = f"Dostępne urządzenia:\n{labels_str}\n\nPytanie: {prompt}"

        data = await self.ollama.generate(
            prompt=user_message,
            max_tokens=20,    # wystarczy na nazwę urządzenia
            temperature=0.0,  # deterministyczny — zadanie klasyfikacyjne
            system=ROUTER_SYSTEM_PROMPT,
        )

        answer = data["response"].strip()

        if not answer or answer.lower() == "brak":
            return None

        answer_lower = answer.lower()

        # exact match (case-insensitive)
        for label in source_labels:
            if label.lower() == answer_lower:
                return label

        # substring match — model mógł skrócić lub wydłużyć nazwę
        for label in source_labels:
            if label.lower() in answer_lower or answer_lower in label.lower():
                return label

        return None
