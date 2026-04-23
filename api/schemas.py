"""
Modele Pydantic — schematy requestów i odpowiedzi API.
"""
from pydantic import BaseModel
from config import DEFAULT_COLLECTION


class AskRequest(BaseModel):
    prompt: str
    max_tokens: int = 512
    temperature: float = 0.1
    rag: bool = False
    collection: str = DEFAULT_COLLECTION
    rag_top_k: int = 3
    rag_score_threshold: float = 0.3


class RagChunk(BaseModel):
    index: int
    score: float
    source_label: str | None
    sheet: str | None
    text: str


class AskResponse(BaseModel):
    answer: str
    model: str
    time_total_s: float
    time_to_first_token_s: float | None
    tokens_generated: int | None
    tokens_per_second: float | None
    rag_chunks_used: int | None = None
    rag_chunks: list[RagChunk] | None = None


class IngestXlsxResponse(BaseModel):
    filename: str
    sheets: int
    chunks: int
    ingested: int
    collection: str


class ChunkInfo(BaseModel):
    index: int
    sheet: str
    chunk: int
    text: str
    char_count: int
    word_count: int


class InspectXlsxResponse(BaseModel):
    filename: str
    sheets: int
    chunks: int
    items: list[ChunkInfo]


class PullRequest(BaseModel):
    model: str | None = None
