"""Deterministic lexical embedder + simple fixed-size chunker.

No embedding API is required. Each text becomes a feature-hashed
bag-of-words vector: lowercase word tokens, minus stopwords, lightly
stemmed, weighted 1 + log(tf), each hashed into one of EMBEDDING_DIM buckets
with a hash-derived sign, then L2-normalized. Texts that share words land
close together under cosine distance, so pgvector `<=>` search returns
lexically relevant chunks.

This is keyword-level similarity, not semantic understanding: synonyms with
no shared word stem ("money back" vs "refund") don't match. Swap in a real
embedding model behind embed_text() when one is available — the 1536-dim,
L2-normalized contract and the stored-vector re-embedding at startup
(see app.rag.reembed_if_stale) stay the same.

(Previous versions seeded a PRNG from a SHA-256 of the whole text, which made
retrieval effectively random: 10% top-1 on tests/test_retrieval_quality.py.)
"""
import hashlib
import math
import random
import re
from collections import Counter

EMBEDDING_DIM = 1536
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    """a about above after again all also am an and any are as at be because been
    before being below between both but by can could did do does doing down during
    each few for from further had has have having he her here hers him his how i if
    in into is it its itself just me more most my myself no nor not now of off on
    once only or other our ours out over own same she should so some such than that
    the their theirs them then there these they this those through to too under
    until up very was we were what when where which while who whom why will with
    would you your yours get got need want like""".split()
)


# Ticketing/travel vocabulary folded onto the words the policy and travel
# docs use, so common paraphrases share features. Keep entries domain-safe:
# an over-broad mapping (e.g. bring -> carry) makes unrelated questions
# ("can I bring food?") match the wrong doc instead of returning nothing.
_PHRASES = {
    "money back": "refund",
    "called off": "cancel",
    "back out": "cancel",
    "new york": "nyc",
}
_CANONICAL = {
    # refunds
    "reimburse": "refund", "reimbursed": "refund", "reimbursement": "refund",
    "repay": "refund", "repayment": "refund", "compensation": "refund",
    # cancellations
    "canceled": "cancel", "cancelled": "cancel", "canceling": "cancel",
    "cancelling": "cancel", "cancellation": "cancel", "cancellations": "cancel",
    # events
    "concert": "event", "concerts": "event", "gig": "event", "gigs": "event",
    "show": "event", "shows": "event", "performance": "event", "game": "event",
    "games": "event", "match": "event", "playoff": "championship", "playoffs": "championship",
    # tickets and transfers
    "seat": "ticket", "seats": "ticket", "give": "transfer", "send": "transfer", "gift": "transfer",
    "cost": "fee", "price": "fee", "charge": "fee",
    "card": "payment", "credit": "payment",
    # packing
    "luggage": "pack", "suitcase": "pack", "baggage": "pack", "bag": "pack", "bags": "pack",
    "gadget": "charger", "gadgets": "charger", "electronics": "charger", "device": "charger",
    # travel
    "abroad": "international", "overseas": "international", "internationally": "international",
    "trip": "travel", "flight": "travel", "journey": "travel",
    "rain": "monsoon", "rainy": "monsoon", "rainfall": "monsoon", "autumn": "fall",
    "nyc": "york", "party": "group",
    "american": "us", "americans": "us", "usa": "us", "british": "uk",
    "france": "schengen", "italy": "schengen", "germany": "schengen", "spain": "schengen",
    "greece": "schengen", "netherlands": "schengen", "europe": "schengen", "european": "schengen",
}


def _stem(token: str) -> str:
    """Tiny suffix stripper so 'refunds'/'refunded'/'refunding' share a feature."""
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


def tokenize(text: str) -> list[str]:
    text = text.lower()
    for phrase, canonical in _PHRASES.items():
        text = text.replace(phrase, canonical)
    tokens = []
    for tok in _TOKEN_RE.findall(text):
        if tok in _STOPWORDS or len(tok) < 2 and tok not in _CANONICAL:
            continue
        tokens.append(_stem(_CANONICAL.get(tok, tok)))
    return tokens


def _bucket(feature: str) -> tuple[int, float]:
    digest = hashlib.sha256(feature.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIM
    sign = 1.0 if digest[4] & 1 else -1.0
    return index, sign


def _fallback_vector(text: str) -> list[float]:
    """For texts with no usable tokens: a deterministic unit vector, so the
    result is never the zero vector (cosine distance on it is undefined)."""
    rng = random.Random(int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16))
    vec = [rng.uniform(-1.0, 1.0) for _ in range(EMBEDDING_DIM)]
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def embed_text(text: str) -> list[float]:
    counts = Counter(tokenize(text))
    if not counts:
        return _fallback_vector(text)

    vec = [0.0] * EMBEDDING_DIM
    for feature, tf in counts.items():
        index, sign = _bucket(feature)
        vec[index] += sign * (1.0 + math.log(tf))

    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:  # every feature cancelled out in shared buckets
        return _fallback_vector(text)
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
