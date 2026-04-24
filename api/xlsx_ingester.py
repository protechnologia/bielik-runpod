import uuid
from dataclasses   import dataclass
from fastapi       import HTTPException, UploadFile
from xlsx_chunker  import XlsxChunker
from qdrant_store  import QdrantStore
from ollama_client import OllamaClient
from schemas       import IngestXlsxResponse, InspectXlsxResponse, ChunkInfo


@dataclass
class ParsedXlsx:
    """
    Wynik parsowania pliku XLSX.

    chunks:      dane operacyjne — każdy chunk niesie pole 'sheet' jako metadaną zapisywaną
                 do Qdrant, żeby przy wyszukiwaniu wiadomo było z którego arkusza pochodzi fragment.
    sheet_names: agregat dla odpowiedzi API — unikalne nazwy arkuszy w kolejności wystąpienia,
                 używane wyłącznie do policzenia pola 'sheets' w IngestXlsxResponse/InspectXlsxResponse.

    Przykład dla pliku z dwoma arkuszami po 20 wierszy każdy (rows_per_chunk=20):
        ParsedXlsx(
            chunks=[
                {"sheet": "Rejestry odczytu", "chunk": 1, "text": "...", ...},
                {"sheet": "Parametry",        "chunk": 1, "text": "...", ...},
            ],
            sheet_names=["Rejestry odczytu", "Parametry"],
        )
    """

    chunks:      list[dict]
    sheet_names: list[str]


class XlsxIngester:
    """Waliduje, chunkuje i indeksuje pliki XLSX w Qdrant."""

    def __init__( self, store: QdrantStore, ollama: OllamaClient ):
        """
        Args:
            store: klient Qdrant do zapisu wektorów
            ollama: klient Ollama do embeddingu chunków
        """
        self.store = store
        self.ollama = ollama

    async def parse( self, file: UploadFile, source_label: str, rows_per_chunk: int ) -> ParsedXlsx:
        """
        Waliduje plik i dzieli go na chunki.

        Args:
            file: przesłany plik XLSX
            source_label: etykieta źródła dołączana do każdego chunku
            rows_per_chunk: liczba wierszy na chunk

        Returns:
            ParsedXlsx z listą chunków i unikalnymi nazwami arkuszy w kolejności.

        Raises:
            HTTPException 400: zły format pliku
            HTTPException 422: błąd parsowania lub pusty plik
        """
        if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
            raise HTTPException(status_code=400, detail="Plik musi być w formacie XLSX.")

        file_bytes = await file.read()

        try:
            chunks = XlsxChunker(rows_per_chunk).chunk(file_bytes, source_label)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Nie można przetworzyć XLSX: {e}")

        if not chunks:
            raise HTTPException(status_code=422, detail="Plik XLSX nie zawiera danych.")

        # dict.fromkeys usuwa duplikaty zachowując kolejność — set tego nie gwarantuje
        sheet_names = list(dict.fromkeys(c["sheet"] for c in chunks))
        return ParsedXlsx(chunks=chunks, sheet_names=sheet_names)

    async def ingest( self, file: UploadFile, source_label: str, collection: str, rows_per_chunk: int ) -> IngestXlsxResponse:
        """
        Pełny pipeline ingestion: walidacja → chunkowanie → embedding → upsert do Qdrant.

        Args:
            file:           przesłany plik XLSX
            source_label:   etykieta źródła dołączana do każdego chunku
            collection:     nazwa kolekcji Qdrant
            rows_per_chunk: liczba wierszy na chunk

        Returns:
            Podsumowanie operacji ingestion, np.:
                IngestXlsxResponse(
                    filename="liczniki.xlsx",
                    sheets=2,
                    chunks=8,
                    ingested=8,
                    collection="documents",
                )
        """
        parsed = await self.parse(file, source_label, rows_per_chunk)
        self.store.ensure_collection(collection)

        points = []
        for chunk in parsed.chunks:
            vector = await self.ollama.embed(chunk["text"])
            points.append({
                "id":     str(uuid.uuid4()),
                "vector": vector,
                "payload": {
                    "text":         chunk["text"],
                    "source_label": chunk["source_label"],
                    "sheet":        chunk["sheet"],
                    "chunk":        chunk["chunk"],
                    "source":       file.filename,
                },
            })

        self.store.upsert(collection, points)

        return IngestXlsxResponse(
            filename   = file.filename,
            sheets     = len(parsed.sheet_names),
            chunks     = len(parsed.chunks),
            ingested   = len(points),
            collection = collection,
        )

    async def inspect(
        self,
        file: UploadFile,
        source_label: str,
        rows_per_chunk: int,
    ) -> InspectXlsxResponse:
        """
        Waliduje plik i zwraca listę chunków bez zapisywania do Qdrant.

        Args:
            file:           przesłany plik XLSX
            source_label:   etykieta źródła dołączana do każdego chunku
            rows_per_chunk: liczba wierszy na chunk

        Returns:
            Szczegółowa lista chunków z metrykami tekstu, np.:
                InspectXlsxResponse(
                    filename="liczniki.xlsx",
                    sheets=2,
                    chunks=4,
                    items=[
                        ChunkInfo(index=1, sheet="Rejestry odczytu", chunk=1,
                                  text="...", char_count=312, word_count=48),
                        ...
                    ],
                )
        """
        parsed = await self.parse(file, source_label, rows_per_chunk)
        items = [
            ChunkInfo(
                index      = i + 1,
                sheet      = c["sheet"],
                chunk      = c["chunk"],
                text       = c["text"],
                char_count = len(c["text"]),
                word_count = len(c["text"].split()),
            )
            for i, c in enumerate(parsed.chunks)
        ]
        return InspectXlsxResponse(
            filename = file.filename,
            sheets   = len(parsed.sheet_names),
            chunks   = len(items),
            items    = items,
        )
