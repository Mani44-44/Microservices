"""Deterministic mock embedder + simple fixed-size chunker.

No LLM/embedding API keys exist in this project (see phase3 spec). This mock
hashes the input text and uses it to seed a PRNG that generates a 1536-dim
L2-normalized vector. Same text -> same vector (so caching and de-dup logic
stays testable), but similar text is NOT expected to embed to similar
vectors -- it's a hash, not a real embedding. Known limitation: semantic
search quality here is effectively random; swap in a real embedding API when
one becomes available.
"""
import hashlib
import random

EMBEDDING_DIM = 1536
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def embed_text(text: str) -> list[float]:
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16)
    rng = random.Random(seed)
    vec = [rng.uniform(-1.0, 1.0) for _ in range(EMBEDDING_DIM)]
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec]


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Fixed-size character chunking with overlap. Keeps it simple per spec --
    no tokenizer dependency, characters instead of tokens."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    step = chunk_size - overlap
    while start < len(text):
        chunks.append(text[start : start + chunk_size])
        if start + chunk_size >= len(text):
            break
        start += step
    return chunks
