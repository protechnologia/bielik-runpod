import io
import pandas as pd

DEFAULT_ROWS_PER_CHUNK = 50


class XlsxChunker:
    def __init__(self, rows_per_chunk: int = DEFAULT_ROWS_PER_CHUNK):
        self.rows_per_chunk = rows_per_chunk

    def _load_sheets(self, file_bytes: bytes) -> dict:
        """Wczytuje wszystkie arkusze z XLSX, usuwa puste wiersze, pomija arkusze bez danych."""
        sheets = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, dtype=str)
        return {
            name: df.dropna(how="all").fillna("")
            for name, df in sheets.items()
            if not df.dropna(how="all").empty
        }

    def _to_markdown(self, df, rows: list) -> str:
        """Zamienia listę krotek wierszy na tabelę Markdown z nagłówkiem i separatorem."""
        header = " | ".join(df.columns)
        separator = " | ".join("---" for _ in df.columns)
        md_rows = [" | ".join(str(v) for v in row) for row in rows]
        return "\n".join([header, separator] + md_rows)

    def _chunk_sheet(self, df, prefix: str, source_label: str, sheet_name: str) -> list[dict]:
        """Dzieli arkusz na chunki po rows_per_chunk wierszy. Każdy chunk zawiera pełny nagłówek tabeli."""
        rows = list(df.itertuples(index=False, name=None))
        chunks = []
        for chunk_idx, start in enumerate(range(0, len(rows), self.rows_per_chunk)):
            batch = rows[start : start + self.rows_per_chunk]
            chunks.append({
                "text": f"{prefix}\n\n{self._to_markdown(df, batch)}",
                "source_label": source_label,
                "sheet": sheet_name,
                "chunk": chunk_idx + 1,
            })
        return chunks

    def chunk(self, file_bytes: bytes, source_label: str) -> list[dict]:
        """Główna metoda — przetwarza cały plik XLSX i zwraca listę chunków ze wszystkich arkuszy."""
        sheets = self._load_sheets(file_bytes)
        chunks = []
        for sheet_name, df in sheets.items():
            prefix = f"{source_label} / {sheet_name}"
            chunks.extend(self._chunk_sheet(df, prefix, source_label, sheet_name))
        return chunks
