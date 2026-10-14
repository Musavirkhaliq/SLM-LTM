"""Recursive character chunker with overlap.

Text is split on the coarsest separator that still yields pieces (paragraph, line, sentence,
word, finally characters), small pieces are packed back together up to `chunk_size`, and each
chunk starts with up to `chunk_overlap` characters of the previous one. Chunks are exact slices
of the input, so `text[chunk.start:chunk.end] == chunk.text` always holds and citations can
point back at the source.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_SEPARATORS: tuple[str, ...] = ("\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ", "")

Span = tuple[int, int]


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    text: str
    start: int  # offsets into the original text
    end: int


def chunk_text(
    text: str,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
    separators: Sequence[str] = DEFAULT_SEPARATORS,
) -> list[Chunk]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    if not 0 <= chunk_overlap < chunk_size:
        raise ValueError("chunk_overlap must be >= 0 and smaller than chunk_size")
    if not text.strip():
        return []
    seps = tuple(separators)
    if not seps or seps[-1] != "":
        seps = (*seps, "")  # guarantee termination: characters are the last resort
    pieces = _split(text, 0, len(text), seps, chunk_size)
    chunks: list[Chunk] = []
    for start, end in _pack(pieces, chunk_size, chunk_overlap):
        raw = text[start:end]
        stripped = raw.strip()
        if not stripped:
            continue
        lead = len(raw) - len(raw.lstrip())
        chunks.append(Chunk(len(chunks), stripped, start + lead, start + lead + len(stripped)))
    return chunks


def _split(text: str, start: int, end: int, seps: tuple[str, ...], limit: int) -> list[Span]:
    """Contiguous spans covering text[start:end], each <= limit, cut at the coarsest separator."""
    if end - start <= limit:
        return [(start, end)]
    sep, rest = seps[0], seps[1:]
    if sep == "":  # unbreakable run (URL, base64, no spaces): one span per character so overlap is exact
        return [(i, i + 1) for i in range(start, end)]
    spans: list[Span] = []
    cursor = start
    while cursor < end:
        hit = text.find(sep, cursor, end)
        stop = end if hit == -1 else hit + len(sep)  # the separator stays with the left piece
        spans.append((cursor, stop))
        cursor = stop
    if len(spans) == 1:  # separator absent: try the next, finer one
        return _split(text, start, end, rest, limit)
    out: list[Span] = []
    for s, e in spans:
        out.extend(_split(text, s, e, rest, limit) if e - s > limit else [(s, e)])
    return out


def _pack(pieces: list[Span], size: int, overlap: int) -> list[Span]:
    chunks: list[Span] = []
    window: list[Span] = []
    length = 0
    for piece in pieces:
        piece_len = piece[1] - piece[0]
        if window and length + piece_len > size:
            chunks.append((window[0][0], window[-1][1]))
            # carry the tail of the finished chunk into the next one as overlap
            while window and (length > overlap or length + piece_len > size):
                dropped = window.pop(0)
                length -= dropped[1] - dropped[0]
        window.append(piece)
        length += piece_len
    if window and (not chunks or window[-1][1] > chunks[-1][1]):
        chunks.append((window[0][0], window[-1][1]))
    return chunks
