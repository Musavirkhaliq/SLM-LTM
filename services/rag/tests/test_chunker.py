import random

import pytest

from slm_rag.chunker import chunk_text

PARAGRAPHS = [
    "Alpha paragraph talks about GPUs. It has two sentences.",
    "Beta paragraph covers quantization. AWQ and GPTQ are discussed here. Another sentence follows.",
    "Gamma is short.",
    "Delta paragraph explains speculative decoding in some detail so that it is longer than the rest of them.",
]
DOC = "\n\n".join(PARAGRAPHS)


def covered(text: str, chunks) -> bool:
    mask = [False] * len(text)
    for c in chunks:
        for i in range(c.start, c.end):
            mask[i] = True
    return all(m or ch.isspace() for m, ch in zip(mask, text, strict=True))


def test_short_text_is_one_chunk():
    chunks = chunk_text("Hello world.", chunk_size=100, chunk_overlap=10)
    assert [(c.index, c.text, c.start, c.end) for c in chunks] == [(0, "Hello world.", 0, 12)]


def test_empty_and_whitespace_only_input():
    assert chunk_text("") == []
    assert chunk_text("  \n\n \t ") == []


@pytest.mark.parametrize("size", [20, 40, 80, 200])
@pytest.mark.parametrize("overlap", [0, 5, 15])
def test_invariants_on_prose(size, overlap):
    if overlap >= size:
        pytest.skip("invalid combination")
    chunks = chunk_text(DOC, size, overlap)
    assert chunks, "non-empty text must yield chunks"
    for i, c in enumerate(chunks):
        assert c.index == i
        assert 0 < len(c.text) <= size
        assert DOC[c.start : c.end] == c.text  # exact slice of the source
        assert c.text == c.text.strip()
    assert covered(DOC, chunks), "every non-space character belongs to some chunk"
    starts = [c.start for c in chunks]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)


def test_prefers_paragraph_boundaries():
    chunks = chunk_text(DOC, chunk_size=110, chunk_overlap=0)
    assert chunks[0].text.startswith("Alpha") and "Beta" not in chunks[0].text.split("\n\n")[0]
    for c in chunks:
        # no chunk starts mid-sentence except where a long paragraph had to be broken
        assert c.start == 0 or DOC[c.start - 1].isspace() or DOC[c.start - 2 : c.start] == ". "


def test_overlap_repeats_trailing_context():
    words = " ".join(f"w{i:03d}" for i in range(200))  # 4-char words + spaces
    chunks = chunk_text(words, chunk_size=50, chunk_overlap=15)
    assert len(chunks) > 3
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        shared = prev.end - nxt.start
        assert 0 < shared <= 15, (prev, nxt)
        assert words[nxt.start : prev.end] in prev.text  # the overlap is the tail of the previous chunk
    assert covered(words, chunks)


def test_zero_overlap_chunks_are_disjoint():
    words = " ".join(f"w{i:03d}" for i in range(100))
    chunks = chunk_text(words, chunk_size=40, chunk_overlap=0)
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert nxt.start >= prev.end


def test_unbreakable_run_is_split_by_characters_with_exact_overlap():
    blob = "".join(chr(97 + i % 26) for i in range(100))
    chunks = chunk_text(blob, chunk_size=30, chunk_overlap=10)
    assert all(len(c.text) <= 30 for c in chunks)
    assert chunks[0].text == blob[:30]
    assert chunks[1].start == 20  # 30 - 10
    assert "".join([chunks[0].text] + [c.text[10:] for c in chunks[1:]]) == blob


def test_custom_separators():
    csv_like = "a,b,c;d,e,f;g,h,i"
    chunks = chunk_text(csv_like, chunk_size=6, chunk_overlap=0, separators=(";", ","))
    assert [c.text for c in chunks] == ["a,b,c;", "d,e,f;", "g,h,i"]


def test_random_text_never_violates_limits():
    rng = random.Random(42)
    alphabet = "abc  \n.,;"
    for _ in range(200):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 400)))
        size = rng.randint(2, 60)
        overlap = rng.randint(0, size - 1)
        chunks = chunk_text(text, size, overlap)
        for c in chunks:
            assert 0 < len(c.text) <= size
            assert text[c.start : c.end] == c.text
        assert covered(text, chunks)


@pytest.mark.parametrize(("size", "overlap"), [(0, 0), (10, 10), (10, 11), (10, -1)])
def test_invalid_parameters(size, overlap):
    with pytest.raises(ValueError):
        chunk_text("text", size, overlap)
