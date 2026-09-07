"""On-disk store for uploaded books, with an in-memory parse cache."""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid

from .ebook import Book, load_book

DATA_DIR = os.environ.get("NOVELREADER_DATA", os.path.abspath("data"))
BOOKS_DIR = os.path.join(DATA_DIR, "books")
OUTPUT_DIR = os.path.join(DATA_DIR, "output")

_cache: dict[str, Book] = {}
_lock = threading.Lock()


def ensure_dirs() -> None:
    os.makedirs(BOOKS_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def book_dir(book_id: str) -> str:
    return os.path.join(BOOKS_DIR, book_id)


def add_book(temp_path: str, original_name: str) -> tuple[str, Book]:
    """Parse an uploaded file, then keep the source around for later re-parsing."""
    ensure_dirs()
    book = load_book(temp_path)  # parse first: a bad file leaves nothing behind

    book_id = uuid.uuid4().hex[:12]
    target_dir = book_dir(book_id)
    os.makedirs(target_dir, exist_ok=True)
    stored = os.path.join(target_dir, "source" + os.path.splitext(original_name)[1].lower())
    shutil.copyfile(temp_path, stored)

    with open(os.path.join(target_dir, "meta.json"), "w", encoding="utf-8") as handle:
        json.dump({
            "id": book_id,
            "original_name": original_name,
            "stored": os.path.basename(stored),
            "title": book.title,
            "author": book.author,
            "format": book.source_format,
            "chapters": len(book.chapters),
            "added": time.time(),
        }, handle, ensure_ascii=False, indent=2)

    if book.cover:
        ext = ".png" if book.cover_mime.endswith("png") else ".jpg"
        with open(os.path.join(target_dir, "cover" + ext), "wb") as handle:
            handle.write(book.cover)

    with _lock:
        _cache[book_id] = book
    return book_id, book


def get_book(book_id: str) -> Book:
    """Return a parsed book, re-parsing from the stored source after a restart."""
    with _lock:
        if book_id in _cache:
            return _cache[book_id]

    target_dir = book_dir(book_id)
    meta_path = os.path.join(target_dir, "meta.json")
    if not os.path.exists(meta_path):
        raise KeyError(f"Unknown book: {book_id}")
    with open(meta_path, encoding="utf-8") as handle:
        meta = json.load(handle)

    book = load_book(os.path.join(target_dir, meta["stored"]))
    with _lock:
        _cache[book_id] = book
    return book


def list_books() -> list[dict]:
    ensure_dirs()
    out = []
    for entry in os.listdir(BOOKS_DIR):
        meta_path = os.path.join(BOOKS_DIR, entry, "meta.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as handle:
                    out.append(json.load(handle))
            except Exception:
                continue
    return sorted(out, key=lambda m: m.get("added", 0), reverse=True)


def delete_book(book_id: str) -> None:
    with _lock:
        _cache.pop(book_id, None)
    shutil.rmtree(book_dir(book_id), ignore_errors=True)


def book_summary(book_id: str, book: Book) -> dict:
    return {
        "id": book_id,
        "title": book.title,
        "author": book.author,
        "format": book.source_format,
        "has_cover": bool(book.cover),
        "total_chars": sum(c.char_count for c in book.chapters),
        "chapters": [
            {"index": c.index, "title": c.title, "chars": c.char_count,
             "est_seconds": c.est_seconds}
            for c in book.chapters
        ],
    }
