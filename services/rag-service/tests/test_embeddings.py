from app.embeddings import EMBEDDING_DIM, chunk_text, embed_text


def test_embed_text_is_deterministic():
    a = embed_text("hello world")
    b = embed_text("hello world")
    assert a == b


def test_embed_text_dimension_and_normalization():
    vec = embed_text("some text")
    assert len(vec) == EMBEDDING_DIM
    norm = sum(x * x for x in vec) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_embed_text_different_inputs_differ():
    assert embed_text("foo") != embed_text("bar")


def test_chunk_text_empty():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_chunk_text_short_text_single_chunk():
    text = "short text"
    assert chunk_text(text) == [text]


def test_chunk_text_long_text_overlaps():
    text = "a" * 1200
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    assert len(chunks) == 3
    # consecutive chunks overlap by `overlap` chars
    assert chunks[0][-50:] == chunks[1][:50]
    assert chunks[1][-50:] == chunks[2][:50]
    # full text is covered
    assert chunks[-1][-1] == text[-1]
