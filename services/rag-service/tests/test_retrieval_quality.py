"""
Retrieval quality on the seed corpus: 20 paraphrased user questions, each
labelled with the seed-document source (topic) that answers it.

Ranks the corpus by cosine similarity in pure Python — the same ordering
pgvector's `<=>` produces in search_chunks() — so this runs without a DB.
"""
from app.embeddings import embed_text
from app.seed_data import SEED_DOCUMENTS

LABELLED_QUERIES = [
    ("Do I need a visa to visit Japan?", "visa_guide"),
    ("visa requirements for a trip to Europe in the Schengen zone", "visa_guide"),
    ("tips for packing clothes in my suitcase", "packing_tips"),
    ("what chargers and adapters should I carry abroad", "packing_tips"),
    ("best time of year to visit Paris", "destinations"),
    ("when do the cherry blossoms bloom in Tokyo", "destinations"),
    ("is Bali good for surfing and yoga", "destinations"),
    ("what is the weather like in New York in summer", "weather_patterns"),
    ("when is the monsoon in southeast asia", "weather_patterns"),
    ("do I need to buy music festival tickets in advance", "event_types"),
    ("do championship finals sell out early", "event_types"),
    ("can I get a refund if I cancel 10 days before the event", "refund_policy"),
    ("will I get refunded if I cancel within 24 hours of the show", "refund_policy"),
    ("the organizer canceled the event, do I get my money back", "refund_policy"),
    ("how long until my refund appears on my payment method", "refund_policy"),
    ("how do I cancel tickets from the My Bookings page", "cancellation_policy"),
    ("cancellation policy for a group booking of 12 tickets", "cancellation_policy"),
    ("can I transfer my ticket to a friend's account", "ticket_transfer"),
    ("is there a fee to transfer a ticket", "ticket_transfer"),
    ("can a transferred ticket be transferred a second time", "ticket_transfer"),
]


def _rank(query: str) -> list[str]:
    q = embed_text(query)
    scored = []
    for doc in SEED_DOCUMENTS:
        d = embed_text(doc["content"])
        scored.append((sum(a * b for a, b in zip(q, d)), doc["source"]))
    return [source for _, source in sorted(scored, reverse=True)]


def _hit_rate(k: int) -> float:
    hits = sum(expected in _rank(query)[:k] for query, expected in LABELLED_QUERIES)
    return hits / len(LABELLED_QUERIES)


def test_top1_retrieval_accuracy():
    assert _hit_rate(1) >= 0.8, f"top-1 accuracy {_hit_rate(1):.0%}"


def test_top3_retrieval_accuracy():
    assert _hit_rate(3) >= 0.9, f"top-3 accuracy {_hit_rate(3):.0%}"


def test_unrelated_query_scores_low():
    """Lexically unrelated text should not look like a match."""
    q = embed_text("quantum chromodynamics lattice gauge")
    best = max(sum(a * b for a, b in zip(q, embed_text(d["content"]))) for d in SEED_DOCUMENTS)
    assert best < 0.2
