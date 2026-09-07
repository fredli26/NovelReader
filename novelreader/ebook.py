"""Parse EPUB / MOBI / AZW3 ebooks into an ordered list of chapters."""
from __future__ import annotations

import os
import re
import shutil
import warnings
import tempfile
import zipfile
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import unquote, urldefrag

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# Ebook XHTML is routinely served through the HTML parser; the warning is noise.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Tags that should never contribute spoken text.
_DROP_TAGS = ("script", "style", "head", "sup", "rt", "rp", "noscript", "svg")
# Block-level tags: a newline is inserted after each so sentences don't run together.
_BLOCK_TAGS = (
    "p", "div", "br", "li", "tr", "blockquote", "section", "article",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "figcaption", "td",
)


@dataclass
class Chapter:
    index: int
    title: str
    text: str

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def est_seconds(self) -> int:
        # Cantonese neural TTS reads roughly 4.4 Chinese characters per second.
        return int(self.char_count / 4.4) if self.char_count else 0


@dataclass
class Book:
    title: str
    author: str
    chapters: list[Chapter] = field(default_factory=list)
    cover: Optional[bytes] = None
    cover_mime: str = "image/jpeg"
    source_format: str = ""


def html_to_text(html: str) -> str:
    """Flatten a chapter's XHTML into clean, speakable plain text."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.append("\n")
    text = soup.get_text()
    text = text.replace(" ", " ").replace("﻿", "")
    text = re.sub(r"[ \t\r]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _first_heading(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "lxml")
    for level in ("h1", "h2", "h3", "h4", "title"):
        node = soup.find(level)
        if node:
            title = " ".join(node.get_text().split())
            if title:
                return title[:120]
    return None


def _guess_title_from_text(text: str) -> Optional[str]:
    """Many CJK ebooks put the chapter name on the first line with no heading tag."""
    for line in text.split("\n"):
        line = line.strip()
        if 0 < len(line) <= 40:
            return line
    return None


# --------------------------------------------------------------------------- EPUB


def _epub_cover(book) -> tuple[Optional[bytes], str]:
    import ebooklib
    from ebooklib import epub

    candidates: list = []
    # 1. <meta name="cover" content="item-id"/>
    for _, meta in book.get_metadata("OPF", "cover") or []:
        cover_id = meta.get("content") if isinstance(meta, dict) else None
        if cover_id:
            item = book.get_item_with_id(cover_id)
            if item:
                candidates.append(item)
    # 2. Items explicitly flagged as the cover image.
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_COVER:
            candidates.append(item)
    # 3. Any image whose filename mentions "cover".
    for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
        if "cover" in item.get_name().lower():
            candidates.append(item)
    # 4. Fall back to the first image in the book.
    candidates.extend(book.get_items_of_type(ebooklib.ITEM_IMAGE))

    for item in candidates:
        try:
            data = item.get_content()
        except Exception:
            continue
        if data and len(data) > 1024:
            name = item.get_name().lower()
            mime = "image/png" if name.endswith(".png") else "image/jpeg"
            return data, mime
    return None, "image/jpeg"


def _epub_toc_titles(book) -> dict[str, str]:
    """Map spine href -> human chapter title, using the book's table of contents."""
    from ebooklib import epub

    titles: dict[str, str] = {}

    def walk(nodes):
        for node in nodes:
            if isinstance(node, (tuple, list)):
                if node and isinstance(node[0], epub.Section):
                    walk(node[1] if len(node) > 1 else [])
                else:
                    walk(node)
            elif isinstance(node, epub.Link):
                href = unquote(urldefrag(node.href)[0])
                title = " ".join((node.title or "").split())
                if href and title:
                    titles.setdefault(href, title)
            elif isinstance(node, epub.Section):
                walk(getattr(node, "subitems", []) or [])

    try:
        walk(book.toc)
    except Exception:
        pass
    return titles


_NAV_NAMES = ("nav.xhtml", "nav.html", "toc.xhtml", "toc.html", "contents.xhtml")


def _is_navigation(item) -> bool:
    """True for the EPUB3 nav document or a generated table-of-contents page."""
    from ebooklib import epub

    if isinstance(item, epub.EpubNav):
        return True
    properties = item.get_properties() if hasattr(item, "get_properties") else []
    if "nav" in (properties or []):
        return True
    name = item.get_name().lower().split("/")[-1]
    return name in _NAV_NAMES


def parse_epub(path: str) -> Book:
    import ebooklib
    from ebooklib import epub

    book = epub.read_epub(path, options={"ignore_ncx": False})

    def meta(field_name: str, default: str) -> str:
        try:
            values = book.get_metadata("DC", field_name)
            if values and values[0][0]:
                return " ".join(str(values[0][0]).split())
        except Exception:
            pass
        return default

    title = meta("title", os.path.splitext(os.path.basename(path))[0])
    author = meta("creator", "Unknown")
    toc_titles = _epub_toc_titles(book)
    cover, cover_mime = _epub_cover(book)

    chapters: list[Chapter] = []
    for spine_id, _ in book.spine:
        item = book.get_item_with_id(spine_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        if _is_navigation(item):
            continue
        try:
            html = item.get_content().decode("utf-8", "replace")
        except Exception:
            continue
        text = html_to_text(html)
        if len(text) < 30:  # covers, blank pages, ad inserts
            continue
        name = unquote(item.get_name())
        chapter_title = (
            toc_titles.get(name)
            or toc_titles.get(name.split("/")[-1])
            or _first_heading(html)
            or _guess_title_from_text(text)
            or f"Section {len(chapters) + 1}"
        )
        chapters.append(Chapter(len(chapters), chapter_title, text))

    return Book(title, author, chapters, cover, cover_mime, "EPUB")


# --------------------------------------------------------------------------- MOBI


def _split_mobi_html(html: str) -> list[tuple[str, str]]:
    """Split a flat MOBI6 HTML blob into (title, text) chapters."""
    # Kindle marks chapter boundaries with page breaks.
    parts = re.split(r"<\s*mbp:pagebreak[^>]*>", html, flags=re.I)
    if len(parts) < 3:
        parts = re.split(r'<\s*div[^>]*page-break-before\s*:\s*always[^>]*>', html, flags=re.I)

    if len(parts) >= 3:
        chapters = []
        for part in parts:
            text = html_to_text(part)
            if len(text) < 200:  # merge stray fragments into the previous chapter
                if chapters and text:
                    prev_title, prev_text = chapters[-1]
                    chapters[-1] = (prev_title, prev_text + "\n" + text)
                continue
            title = _first_heading(part) or _guess_title_from_text(text) or ""
            chapters.append((title, text))
        if len(chapters) >= 2:
            return chapters

    # No page breaks: fall back to splitting on whichever heading level is used most.
    soup = BeautifulSoup(html, "lxml")
    for level in ("h1", "h2", "h3"):
        heads = soup.find_all(level)
        if len(heads) >= 2:
            pieces = re.split(rf"(?=<\s*{level}[\s>])", html, flags=re.I)
            chapters = []
            for piece in pieces:
                text = html_to_text(piece)
                if len(text) < 100:
                    continue
                title = _first_heading(piece) or _guess_title_from_text(text) or ""
                chapters.append((title, text))
            if len(chapters) >= 2:
                return chapters

    text = html_to_text(html)
    return [("Full text", text)] if text else []


def parse_mobi(path: str) -> Book:
    """Unpack a MOBI/AZW/AZW3 with the `mobi` library, then parse what falls out."""
    import mobi

    tempdir, extracted = mobi.extract(path)
    try:
        ext = os.path.splitext(extracted)[1].lower()

        # KF8 / AZW3 unpacks to a real EPUB — reuse the richer EPUB path.
        if ext == ".epub":
            book = parse_epub(extracted)
            book.source_format = "MOBI/AZW3"
            if not book.title or book.title == os.path.splitext(os.path.basename(extracted))[0]:
                book.title = os.path.splitext(os.path.basename(path))[0]
            return book

        with open(extracted, "rb", buffering=0) as handle:
            raw = handle.read()
        html = raw.decode("utf-8", "replace")

        title = _first_heading(html) or os.path.splitext(os.path.basename(path))[0]
        author = "Unknown"
        match = re.search(r'<\s*meta[^>]+name=["\']author["\'][^>]+content=["\']([^"\']+)', html, re.I)
        if match:
            author = match.group(1).strip()

        chapters = [
            Chapter(i, t or f"Section {i + 1}", text)
            for i, (t, text) in enumerate(_split_mobi_html(html))
        ]

        cover, cover_mime = None, "image/jpeg"
        best = 0
        for root, _, files in os.walk(tempdir):
            for name in files:
                if not name.lower().endswith((".jpg", ".jpeg", ".png")):
                    continue
                full = os.path.join(root, name)
                size = os.path.getsize(full)
                # Prefer a file named "cover"; otherwise take the largest image.
                score = size + (10_000_000 if "cover" in name.lower() else 0)
                if score > best:
                    with open(full, "rb", buffering=0) as handle:
                        cover = handle.read()
                    cover_mime = "image/png" if name.lower().endswith(".png") else "image/jpeg"
                    best = score

        return Book(os.path.splitext(os.path.basename(path))[0], author, chapters,
                    cover, cover_mime, "MOBI")
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)


# --------------------------------------------------------------------------- entry


def load_book(path: str) -> Book:
    """Detect the format from content (not just the extension) and parse it."""
    ext = os.path.splitext(path)[1].lower()

    if ext in (".mobi", ".azw", ".azw3", ".prc"):
        book = parse_mobi(path)
    elif ext == ".epub":
        book = parse_epub(path)
    else:
        if zipfile.is_zipfile(path):
            book = parse_epub(path)
        else:
            book = parse_mobi(path)

    if not book.chapters:
        raise ValueError(
            "No readable text found in this file. If it is a DRM-protected Kindle "
            "purchase, it must be de-DRMed before it can be read."
        )
    # Renumber defensively so indexes stay contiguous after any filtering above.
    for i, chapter in enumerate(book.chapters):
        chapter.index = i
    return book
