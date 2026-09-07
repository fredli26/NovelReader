"""Local web UI: upload a book, pick chapters, get Cantonese audio."""
from __future__ import annotations

import os
import tempfile
import webbrowser
from threading import Timer

from flask import (Flask, abort, jsonify, render_template, request,
                   send_file, send_from_directory)

from . import audio, jobs, library
from .tts import DEFAULT_ENGINE, describe_all, get_engine

ALLOWED_EXT = {".epub", ".mobi", ".azw", ".azw3", ".prc"}
MAX_UPLOAD_MB = 300

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.config["JSON_AS_ASCII"] = False


@app.get("/")
def index():
    return render_template("index.html", ffmpeg_ok=audio.ffmpeg_available())


@app.get("/api/engines")
def api_engines():
    return jsonify({"engines": describe_all(), "default": DEFAULT_ENGINE})


@app.get("/api/books")
def api_books():
    return jsonify({"books": library.list_books()})


@app.post("/api/books")
def api_upload():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": "No file was uploaded."}), 400

    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return jsonify({"error": f"Unsupported file type '{ext}'. "
                                 "Use .epub, .mobi, .azw or .azw3."}), 400

    handle, temp_path = tempfile.mkstemp(suffix=ext)
    os.close(handle)
    try:
        upload.save(temp_path)
        book_id, book = library.add_book(temp_path, upload.filename)
        return jsonify(library.book_summary(book_id, book))
    except Exception as exc:
        return jsonify({"error": str(exc)[:400]}), 400
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@app.get("/api/books/<book_id>")
def api_book(book_id: str):
    try:
        return jsonify(library.book_summary(book_id, library.get_book(book_id)))
    except KeyError:
        abort(404)
    except Exception as exc:
        return jsonify({"error": str(exc)[:400]}), 400


@app.delete("/api/books/<book_id>")
def api_delete_book(book_id: str):
    library.delete_book(book_id)
    return jsonify({"ok": True})


@app.get("/api/books/<book_id>/cover")
def api_cover(book_id: str):
    try:
        book = library.get_book(book_id)
    except Exception:
        abort(404)
    if not book.cover:
        abort(404)
    import io
    return send_file(io.BytesIO(book.cover), mimetype=book.cover_mime)


@app.get("/api/books/<book_id>/chapters/<int:index>/preview")
def api_chapter_preview(book_id: str, index: int):
    """First few hundred characters, so the user can confirm they picked right."""
    try:
        book = library.get_book(book_id)
    except Exception:
        abort(404)
    if not 0 <= index < len(book.chapters):
        abort(404)
    chapter = book.chapters[index]
    text = chapter.text.strip()
    return jsonify({
        "index": index,
        "title": chapter.title,
        "chars": chapter.char_count,
        "est_seconds": chapter.est_seconds,
        "excerpt": text[:600] + ("…" if len(text) > 600 else ""),
    })


@app.post("/api/sample")
def api_sample():
    """Synthesise one short line so the user can audition a voice before committing."""
    body = request.get_json(force=True, silent=True) or {}
    engine_id = body.get("engine", DEFAULT_ENGINE)
    voice = body.get("voice", "")
    text = (body.get("text") or "歡迎使用有聲書轉換器，今次旅程祝你一路順風。").strip()[:200]

    try:
        engine = get_engine(engine_id)
        ok, reason = engine.available()
        if not ok:
            return jsonify({"error": reason}), 400
        handle, path = tempfile.mkstemp(suffix=".mp3")
        os.close(handle)
        engine.synth(text, path, voice or engine.default_voice(),
                     rate=int(body.get("rate", 0)), pitch=int(body.get("pitch", 0)))
        return send_file(path, mimetype="audio/mpeg", as_attachment=False,
                         download_name="sample.mp3")
    except Exception as exc:
        return jsonify({"error": str(exc)[:300]}), 500


@app.post("/api/jobs")
def api_start_job():
    body = request.get_json(force=True, silent=True) or {}
    book_id = body.get("book_id", "")
    indexes = body.get("chapters") or []

    try:
        book = library.get_book(book_id)
    except Exception:
        return jsonify({"error": "That book is no longer available. Upload it again."}), 404

    engine_id = body.get("engine", DEFAULT_ENGINE)
    try:
        engine = get_engine(engine_id)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    ok, reason = engine.available()
    if not ok:
        return jsonify({"error": reason}), 400

    try:
        job = jobs.start_job(
            book_id, book, [int(i) for i in indexes],
            engine=engine_id,
            voice=body.get("voice") or engine.default_voice(),
            rate=max(-50, min(100, int(body.get("rate", 0)))),
            pitch=max(-50, min(50, int(body.get("pitch", 0)))),
            gap_ms=max(0, min(3000, int(body.get("gap_ms", 350)))),
            make_m4b=bool(body.get("make_m4b", True)),
            to_traditional=bool(body.get("to_traditional", False)),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(job.to_dict())


@app.get("/api/jobs")
def api_jobs():
    return jsonify({"jobs": jobs.list_jobs()})


@app.get("/api/jobs/<job_id>")
def api_job(job_id: str):
    try:
        return jsonify(jobs.get_job(job_id).to_dict())
    except KeyError:
        abort(404)


@app.post("/api/jobs/<job_id>/cancel")
def api_cancel(job_id: str):
    try:
        job = jobs.get_job(job_id)
    except KeyError:
        abort(404)
    job.cancel()
    return jsonify({"ok": True})


@app.get("/api/jobs/<job_id>/files/<path:name>")
def api_download(job_id: str, name: str):
    try:
        job = jobs.get_job(job_id)
    except KeyError:
        abort(404)
    directory = os.path.abspath(job.outdir)
    target = os.path.abspath(os.path.join(directory, name))
    # Keep path traversal out of the download route.
    if not target.startswith(directory + os.sep) or not os.path.exists(target):
        abort(404)
    return send_from_directory(directory, name, as_attachment=True)


@app.get("/api/jobs/<job_id>/reveal")
def api_reveal(job_id: str):
    """Open the output folder in Finder — easier than downloading 40 files."""
    try:
        job = jobs.get_job(job_id)
    except KeyError:
        abort(404)
    path = os.path.abspath(job.outdir)
    try:
        import subprocess
        subprocess.run(["open", path], timeout=10)
        return jsonify({"ok": True, "path": path})
    except Exception as exc:
        return jsonify({"ok": False, "path": path, "error": str(exc)}), 500


@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": f"That file is larger than {MAX_UPLOAD_MB} MB."}), 413


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="NovelReader — ebook to Cantonese audio")
    parser.add_argument("--port", type=int, default=5678)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    library.ensure_dirs()
    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser:
        Timer(1.2, lambda: webbrowser.open(url)).start()

    print(f"\n  NovelReader running at {url}")
    print(f"  Audio is written to {library.OUTPUT_DIR}")
    print("  Press Ctrl+C to stop.\n")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
