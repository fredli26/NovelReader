"""Background conversion jobs: chapter text in, tagged MP3s and an M4B out."""
from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import audio, library, textprep
from .ebook import Book
from .tts import get_engine


# Cantonese neural TTS speaks ~4.4 characters per second.
_CHARS_PER_SECOND = 4.4
# Measured throughput of the Edge engine at 3 parallel workers, used only until
# the running job has produced enough of its own timing to measure.
_PRIOR_CHARS_PER_SEC = 50.0


@dataclass
class ChapterState:
    index: int
    title: str
    status: str = "pending"       # pending | running | done | failed | skipped
    chunks_total: int = 0
    chunks_done: int = 0
    chars_total: int = 0
    chars_done: int = 0
    seconds: float = 0.0
    file: str = ""
    error: str = ""


@dataclass
class Job:
    id: str
    book_id: str
    book_title: str
    author: str
    engine: str
    voice: str
    rate: int = 0
    pitch: int = 0
    gap_ms: int = 350
    make_m4b: bool = True
    to_traditional: bool = False
    status: str = "queued"        # queued | running | done | failed | cancelled
    message: str = ""
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    chapters: list[ChapterState] = field(default_factory=list)
    outputs: list[dict] = field(default_factory=list)
    m4b: str = ""
    zip: str = ""
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def outdir(self) -> str:
        return os.path.join(library.OUTPUT_DIR, self.id)

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def to_dict(self) -> dict:
        chars_total = sum(c.chars_total for c in self.chapters)
        chars_done = sum(c.chars_done for c in self.chapters)
        elapsed = (self.finished or time.time()) - self.started
        fraction = min(chars_done / chars_total, 1.0) if chars_total else 0.0

        # A chunk takes ~80s, so a chapter-granular ETA shows nothing for the first
        # minute and a half. Seed the rate with a measured prior, then switch to the
        # job's own throughput as soon as there is some.
        rate = chars_done / elapsed if chars_done and elapsed > 1 else _PRIOR_CHARS_PER_SEC
        eta = int((chars_total - chars_done) / rate) if chars_total and self.status == "running" else None

        # Count audio from finished chapters exactly, and in-flight ones by estimate,
        # so the number climbs during a long chapter instead of sitting at zero.
        audio_seconds = sum(
            c.seconds if c.status == "done" else c.chars_done / _CHARS_PER_SECOND
            for c in self.chapters
        )
        return {
            "id": self.id,
            "book_id": self.book_id,
            "book_title": self.book_title,
            "status": self.status,
            "message": self.message,
            "engine": self.engine,
            "voice": self.voice,
            "percent": round(fraction * 100, 1),
            "elapsed": int(elapsed),
            "eta": eta,
            "audio_seconds": round(audio_seconds),
            "chars_done": chars_done,
            "chars_total": chars_total,
            "chapters": [
                {"index": c.index, "title": c.title, "status": c.status,
                 "chunks_total": c.chunks_total, "chunks_done": c.chunks_done,
                 "chars_total": c.chars_total, "chars_done": c.chars_done,
                 "seconds": round(c.seconds), "file": c.file, "error": c.error}
                for c in self.chapters
            ],
            "outputs": self.outputs,
            "m4b": self.m4b,
            "zip": self.zip,
        }


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def get_job(job_id: str) -> Job:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise KeyError(f"Unknown job: {job_id}")
    return job


def list_jobs() -> list[dict]:
    with _jobs_lock:
        jobs = list(_jobs.values())
    return [j.to_dict() for j in sorted(jobs, key=lambda j: j.started, reverse=True)]


def _synth_chapter(job: Job, state: ChapterState, chunks: list[str],
                   workdir: str, concurrency: int) -> list[str]:
    """Synthesise every chunk of one chapter, in parallel but returned in order."""
    engine = get_engine(job.engine)
    paths: list[str | None] = [None] * len(chunks)

    def one(i: int) -> None:
        if job.cancelled:
            return
        path = os.path.join(workdir, f"{state.index:04d}_{i:04d}.mp3")
        engine.synth(chunks[i], path, job.voice, rate=job.rate, pitch=job.pitch)
        paths[i] = path
        state.chunks_done += 1
        state.chars_done += len(chunks[i])

    if concurrency <= 1:
        for i in range(len(chunks)):
            if job.cancelled:
                break
            one(i)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            list(pool.map(one, range(len(chunks))))

    return [p for p in paths if p]


def _run(job: Job, book: Book) -> None:
    # Everything lives inside the try: a crash here must surface on the job,
    # not kill the worker thread and leave the UI stuck on "queued".
    workdir = os.path.join(job.outdir, ".chunks")
    try:
        os.makedirs(workdir, exist_ok=True)
        engine = get_engine(job.engine)
        concurrency = 3 if engine.needs_network else 2
        total = len(job.chapters)
        job.status = "running"

        # Prepare all text up front so chunk counts (and the progress bar) are exact.
        prepared: dict[int, list[str]] = {}
        for state in job.chapters:
            text = textprep.clean(book.chapters[state.index].text)
            if job.to_traditional:
                text = textprep.to_traditional(text)
            pieces = textprep.chunk(text)
            prepared[state.index] = pieces
            state.chunks_total = len(pieces)
            state.chars_total = sum(len(p) for p in pieces)

        for position, state in enumerate(job.chapters, start=1):
            if job.cancelled:
                break
            chunks = prepared[state.index]
            if not chunks:
                state.status = "skipped"
                state.error = "Chapter contains no readable text"
                continue

            state.status = "running"
            job.message = f"Chapter {position}/{total} · {state.title}"
            try:
                parts = _synth_chapter(job, state, chunks, workdir, concurrency)
                if job.cancelled:
                    state.status = "pending"
                    break
                if not parts:
                    raise RuntimeError("No audio was produced for this chapter")

                name = f"{state.index + 1:03d} - {audio.safe_filename(state.title)}.mp3"
                out_path = os.path.join(job.outdir, name)
                audio.join_audio(parts, out_path, gap_ms=job.gap_ms)
                audio.tag_mp3(
                    out_path, title=state.title, album=job.book_title,
                    artist=job.author, track=state.index + 1,
                    total=len(book.chapters), cover=book.cover,
                    cover_mime=book.cover_mime,
                )
                state.seconds = audio.duration(out_path)
                state.file = name
                state.status = "done"
                job.outputs.append({
                    "name": name,
                    "title": state.title,
                    "seconds": round(state.seconds),
                    "bytes": os.path.getsize(out_path),
                })
            except Exception as exc:
                # One bad chapter must not cost the user the rest of the book.
                state.status = "failed"
                state.error = str(exc)[:300]
            finally:
                for leftover in os.listdir(workdir):
                    if leftover.startswith(f"{state.index:04d}_"):
                        try:
                            os.remove(os.path.join(workdir, leftover))
                        except OSError:
                            pass

        if job.cancelled:
            job.status = "cancelled"
            job.message = "Cancelled — finished chapters are still available below."
        else:
            done = [c for c in job.chapters if c.status == "done"]
            if not done:
                job.status = "failed"
                first_error = next((c.error for c in job.chapters if c.error), "")
                job.message = f"No chapters could be converted. {first_error}"
            else:
                if job.make_m4b and len(done) >= 1:
                    job.message = "Building audiobook file…"
                    try:
                        m4b_name = audio.safe_filename(job.book_title, "audiobook") + ".m4b"
                        audio.build_m4b(
                            [os.path.join(job.outdir, c.file) for c in done],
                            [c.title for c in done],
                            os.path.join(job.outdir, m4b_name),
                            book_title=job.book_title, author=job.author,
                            cover=book.cover,
                        )
                        job.m4b = m4b_name
                        job.message = ""
                    except Exception as exc:
                        job.message = f"MP3s are ready; the M4B step failed: {str(exc)[:200]}"
                else:
                    job.message = ""
                try:
                    zip_name = audio.safe_filename(job.book_title, "audiobook") + ".zip"
                    zip_path = os.path.join(job.outdir, zip_name)
                    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as archive:
                        for chapter in done:
                            archive.write(os.path.join(job.outdir, chapter.file), chapter.file)
                    job.zip = zip_name
                except Exception:
                    pass

                failed = [c for c in job.chapters if c.status == "failed"]
                job.status = "done"
                if failed and not job.message:
                    job.message = f"{len(failed)} chapter(s) failed; the rest converted fine."
    except Exception as exc:
        job.status = "failed"
        job.message = str(exc)[:400]
    finally:
        job.finished = time.time()
        shutil.rmtree(workdir, ignore_errors=True)


def start_job(book_id: str, book: Book, indexes: list[int], *, engine: str, voice: str,
              rate: int = 0, pitch: int = 0, gap_ms: int = 350,
              make_m4b: bool = True, to_traditional: bool = False) -> Job:
    valid = [i for i in indexes if 0 <= i < len(book.chapters)]
    if not valid:
        raise ValueError("No valid chapters were selected.")

    job = Job(
        id=uuid.uuid4().hex[:12], book_id=book_id, book_title=book.title,
        author=book.author or "Unknown", engine=engine, voice=voice, rate=rate,
        pitch=pitch, gap_ms=gap_ms, make_m4b=make_m4b, to_traditional=to_traditional,
    )
    job.chapters = [
        ChapterState(index=i, title=book.chapters[i].title) for i in sorted(set(valid))
    ]

    with _jobs_lock:
        _jobs[job.id] = job

    threading.Thread(target=_run, args=(job, book), daemon=True).start()
    return job
