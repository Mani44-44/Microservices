from typing import Optional

from pydantic import BaseModel, Field


class DocumentIn(BaseModel):
    content: str
    category: str
    source: str
    metadata: dict = Field(default_factory=dict)


class DocumentOut(BaseModel):
    document_id: str
    chunks_created: int


class SearchIn(BaseModel):
    query: str
    category: Optional[str] = None
    top_k: int = 5


class SearchResult(BaseModel):
    chunk_id: str
    content: str
    category: str
    source: str
    score: float
    metadata: dict


class SearchOut(BaseModel):
    results: list[SearchResult]
