"""Clean chapter text and split it into TTS-sized chunks."""
from __future__ import annotations

import re

# Sentence-ending punctuation, strongest break first.
_HARD_END = "。！？!?…"
_SOFT_END = "；;：:，,、）)》」』】"

_NOISE_PATTERNS = [
    re.compile(r"\[\s*\d{1,3}\s*\]"),          # [1] footnote markers
    re.compile(r"\(\s*\d{1,3}\s*\)\s*(?=\n)"), # (12) trailing on its own
    re.compile(r"^\s*[-—–_*=~]{3,}\s*$", re.M),# divider rules
    re.compile(r"https?://\S+"),
    re.compile(r"www\.\S+"),
]

# Characters that produce no speech but can confuse the engine's prosody.
_STRIP_CHARS = str.maketrans({
    "​": None, "‌": None, "‍": None, "﻿": None,
    "\xa0": " ", "　": " ",
    "“": "「", "”": "」", "‘": "『", "’": "』",
})


def clean(text: str, strip_noise: bool = True) -> str:
    """Normalise a chapter for speech: drop web noise, footnote marks, stray glyphs."""
    text = text.translate(_STRIP_CHARS)
    if strip_noise:
        for pattern in _NOISE_PATTERNS:
            text = pattern.sub("", text)
    # Collapse runs of repeated punctuation that make the voice stutter.
    text = re.sub(r"([。！？，、；：])\1{1,}", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def to_traditional(text: str) -> str:
    """Convert Simplified to Traditional if OpenCC is installed; otherwise pass through.

    Cantonese voices read Traditional Chinese more idiomatically, so this is
    worth doing when the source is a Mainland edition.
    """
    try:
        from opencc import OpenCC
    except ImportError:
        return text
    try:
        return OpenCC("s2hk").convert(text)
    except Exception:
        return text


def _split_sentences(paragraph: str) -> list[str]:
    """Break a paragraph after sentence-final punctuation, keeping the punctuation."""
    out, buf = [], []
    for ch in paragraph:
        buf.append(ch)
        if ch in _HARD_END:
            out.append("".join(buf))
            buf = []
    if buf:
        out.append("".join(buf))
    return [s for s in out if s.strip()]


def _hard_wrap(piece: str, limit: int) -> list[str]:
    """Last resort for a single sentence longer than the limit: break on soft punctuation."""
    out, buf = [], ""
    for ch in piece:
        buf += ch
        if len(buf) >= limit and ch in _SOFT_END:
            out.append(buf)
            buf = ""
        elif len(buf) >= limit + 120:  # no punctuation in sight — cut anyway
            out.append(buf)
            buf = ""
    if buf.strip():
        out.append(buf)
    return out


def chunk(text: str, limit: int = 1400) -> list[str]:
    """Split text into chunks under `limit` characters, never mid-sentence.

    Long chapters are synthesised chunk-by-chunk and concatenated, which keeps
    each network request small enough to succeed and to retry cheaply.
    """
    chunks: list[str] = []
    current = ""

    for paragraph in text.split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for sentence in _split_sentences(paragraph):
            pieces = [sentence] if len(sentence) <= limit else _hard_wrap(sentence, limit)
            for piece in pieces:
                if len(current) + len(piece) <= limit:
                    current += piece
                else:
                    if current.strip():
                        chunks.append(current.strip())
                    current = piece
        # Paragraph boundary: give the engine a newline so it breathes.
        if current and not current.endswith("\n"):
            current += "\n"

    if current.strip():
        chunks.append(current.strip())
    return chunks
