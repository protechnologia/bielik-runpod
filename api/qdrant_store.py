from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct


class QdrantStore:
    """
    Warstwa dostępu do bazy wektorowej Qdrant.

    Odpowiada za zarządzanie kolekcjami i operacje na wektorach.
    Izoluje resztę aplikacji od szczegółów Qdrant — main.py nie importuje
    nic z qdrant_client, a podmiana bazy wektorowej wymaga zmian tylko tutaj.

    Operuje na zwykłych słownikach Pythona — PointStruct i ScoredPoint
    są szczegółem implementacyjnym tej klasy, niewidocznym na zewnątrz.
    """

    def __init__(self, path: str, vector_size: int):
        """
        Args:
            path:        Ścieżka do katalogu z danymi Qdrant, np. '/root/data/qdrant'.
            vector_size: Wymiarowość wektorów, musi być zgodna z modelem embeddingu
                         (nomic-embed-text → 768).
        """
        self.client = QdrantClient(path=path)
        self.vector_size = vector_size

    def ensure_collection(self, collection: str) -> None:
        """
        Tworzy kolekcję jeśli nie istnieje. Idempotentna — bezpieczna do wywołania
        wielokrotnie przed każdą operacją zapisu lub odczytu.

        Args:
            collection: Nazwa kolekcji.
        """
        if not self.client.collection_exists(collection):
            self.client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(
                    size=self.vector_size,
                    distance=Distance.COSINE,
                ),
            )

    def upsert(self, collection: str, points: list[dict]) -> None:
        """
        Zapisuje punkty do kolekcji. Jeśli punkt o danym id już istnieje,
        zostaje nadpisany (upsert).

        Args:
            collection: Nazwa kolekcji.
            points:     Lista słowników z kluczami:
                            id      — unikalny identyfikator (str UUID),
                            vector  — wektor embeddingu (list[float]),
                            payload — dowolny słownik z metadanymi.

        Przykład punktu:
            {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "vector": [0.1, 0.2, ..., 0.768],
                "payload": {
                    "text":         "ORNO OR-WE-516 / Rejestry odczytu\\n\\n...",
                                    # pełna treść chunku wysłana do embeddera;
                                    # zaczyna się od prefiksu '{source_label} / {sheet}'
                    "source_label": "ORNO OR-WE-516",
                                    # etykieta podana przez użytkownika przy ingestii,
                                    # identyfikuje urządzenie lub dokument
                    "sheet":        "Rejestry odczytu",
                                    # nazwa arkusza XLSX; jedna kolekcja może zawierać
                                    # chunki z wielu arkuszy i wielu plików
                    "chunk":        1,
                                    # numer porządkowy chunku w obrębie arkusza, od 1
                    "source":       "rejestry.xlsx"
                                    # oryginalna nazwa pliku XLSX z żądania HTTP
                }
            }
        """
        self.client.upsert(
            collection_name=collection,
            points=[
                PointStruct(
                    id=p["id"],
                    vector=p["vector"],
                    payload=p["payload"],
                )
                for p in points
            ],
        )

    def search(
        self,
        collection: str,
        vector: list[float],
        top_k: int,
        score_threshold: float,
    ) -> list[dict]:
        """
        Wyszukuje najbliższe wektory w kolekcji metodą cosine similarity.

        Args:
            collection:      Nazwa kolekcji.
            vector:          Wektor zapytania (embedding pytania użytkownika).
            top_k:           Maksymalna liczba wyników.
            score_threshold: Minimalna wartość podobieństwa (0.0–1.0).
                             Wyniki poniżej progu są odrzucane.

        Returns:
            Lista słowników posortowana malejąco po score:
                [
                    {
                        "score": 0.8731,
                        "payload": {
                            "text": "ORNO OR-WE-516 / Rejestry odczytu\\n\\n...",
                            "source_label": "ORNO OR-WE-516",
                            "sheet": "Rejestry odczytu",
                            ...
                        }
                    },
                    ...
                ]
        """
        hits = self.client.search(
            collection_name=collection,
            query_vector=vector,
            limit=top_k,
            score_threshold=score_threshold,
        )
        return [{"score": hit.score, "payload": hit.payload} for hit in hits]

    def list_collections(self) -> list[dict]:
        """
        Zwraca listę kolekcji z liczbą zapisanych wektorów.

        Returns:
            [{"name": "documents", "vectors_count": 42}, ...]
        """
        result = []
        for c in self.client.get_collections().collections:
            info = self.client.get_collection(c.name)
            result.append({
                "name": c.name,
                "vectors_count": info.vectors_count,
            })
        return result

    def delete_collection(self, collection: str) -> None:
        """
        Usuwa kolekcję wraz ze wszystkimi wektorami i metadanymi.

        Args:
            collection: Nazwa kolekcji do usunięcia.
        """
        self.client.delete_collection(collection)