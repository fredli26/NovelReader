"""ffmpeg glue: join chunks, tag MP3s, and build a chaptered M4B audiobook."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


class AudioError(RuntimeError):
    pass


def _run(args: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AudioError(f"{args[0]} failed: {proc.stderr.strip()[-500:]}")
    return proc


def ffmpeg_available() -> bool:
    try:
        subprocess.run([FFMPEG, "-version"], capture_output=True, timeout=15)
        return True
    except Exception:
        return False


def duration(path: str) -> float:
    """Length of an audio file in seconds."""
    proc = _run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", path], timeout=120)
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def make_silence(path: str, ms: int, rate: int = 24000) -> str:
    """Render a short silent MP3 used as the gap between chunks."""
    _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
          "-f", "lavfi", "-i", f"anullsrc=r={rate}:cl=mono",
          "-t", f"{ms / 1000:.3f}", "-codec:a", "libmp3lame", "-b:a", "64k", path],
         timeout=120)
    return path


def _concat_list(paths: list[str], list_path: str) -> str:
    with open(list_path, "w", encoding="utf-8") as handle:
        for path in paths:
            # The concat demuxer needs single quotes escaped this exact way.
            safe = os.path.abspath(path).replace("'", r"'\''")
            handle.write(f"file '{safe}'\n")
    return list_path


def join_audio(parts: list[str], out_path: str, gap_ms: int = 350,
               bitrate: str = "96k") -> str:
    """Concatenate synthesised chunks into one MP3, with a pause between each."""
    if not parts:
        raise AudioError("Nothing to join — no audio chunks were produced.")

    workdir = tempfile.mkdtemp(prefix="nr_join_")
    try:
        sequence: list[str] = []
        if gap_ms > 0 and len(parts) > 1:
            silence = make_silence(os.path.join(workdir, "gap.mp3"), gap_ms)
            for i, part in enumerate(parts):
                if i:
                    sequence.append(silence)
                sequence.append(part)
        else:
            sequence = list(parts)

        listfile = _concat_list(sequence, os.path.join(workdir, "list.txt"))
        _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
              "-f", "concat", "-safe", "0", "-i", listfile,
              "-codec:a", "libmp3lame", "-b:a", bitrate, "-ar", "24000", "-ac", "1",
              out_path])
        return out_path
    finally:
        for name in os.listdir(workdir):
            try:
                os.remove(os.path.join(workdir, name))
            except OSError:
                pass
        os.rmdir(workdir)


def tag_mp3(path: str, *, title: str, album: str, artist: str,
            track: int, total: int, cover: bytes | None = None,
            cover_mime: str = "image/jpeg") -> None:
    """Write ID3 tags and embed cover art so phones display the book properly."""
    from mutagen.id3 import (APIC, ID3, TALB, TIT2, TPE1, TPE2, TRCK, ID3NoHeaderError)
    from mutagen.mp3 import MP3

    audio = MP3(path)
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        audio.add_tags()
        tags = audio.tags

    tags.delall("TIT2"); tags.delall("TALB"); tags.delall("TPE1")
    tags.delall("TPE2"); tags.delall("TRCK"); tags.delall("APIC")

    tags.add(TIT2(encoding=3, text=title))
    tags.add(TALB(encoding=3, text=album))
    tags.add(TPE1(encoding=3, text=artist))
    tags.add(TPE2(encoding=3, text=artist))   # album artist: keeps chapters grouped
    tags.add(TRCK(encoding=3, text=f"{track}/{total}"))
    if cover:
        tags.add(APIC(encoding=3, mime=cover_mime, type=3, desc="Cover", data=cover))
    tags.save(path, v2_version=3)  # v2.3 is the most widely supported on phones


def _escape_meta(value: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", value or "")


def build_m4b(chapter_files: list[str], titles: list[str], out_path: str, *,
              book_title: str, author: str, cover: bytes | None = None,
              bitrate: str = "64k") -> str:
    """Merge chapter MP3s into a single .m4b audiobook with real chapter markers."""
    if not chapter_files:
        raise AudioError("No chapters to build into an audiobook.")

    workdir = tempfile.mkdtemp(prefix="nr_m4b_")
    try:
        listfile = _concat_list(chapter_files, os.path.join(workdir, "list.txt"))

        lines = [";FFMETADATA1",
                 f"title={_escape_meta(book_title)}",
                 f"artist={_escape_meta(author)}",
                 f"album={_escape_meta(book_title)}",
                 f"album_artist={_escape_meta(author)}",
                 "genre=Audiobook"]
        cursor_ms = 0
        for path, title in zip(chapter_files, titles):
            length_ms = int(duration(path) * 1000)
            if length_ms <= 0:
                continue
            lines += ["[CHAPTER]", "TIMEBASE=1/1000",
                      f"START={cursor_ms}", f"END={cursor_ms + length_ms}",
                      f"title={_escape_meta(title)}"]
            cursor_ms += length_ms

        metafile = os.path.join(workdir, "meta.txt")
        with open(metafile, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

        args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", listfile,
                "-i", metafile]

        coverfile = None
        if cover:
            coverfile = os.path.join(workdir, "cover.img")
            with open(coverfile, "wb") as handle:
                handle.write(cover)
            args += ["-i", coverfile]

        args += ["-map", "0:a", "-map_metadata", "1",
                 "-codec:a", "aac", "-b:a", bitrate, "-ar", "24000", "-ac", "1"]
        if coverfile:
            args += ["-map", "2:v", "-codec:v", "mjpeg", "-disposition:v", "attached_pic"]
        args += ["-movflags", "+faststart", "-f", "mp4", out_path]

        try:
            _run(args)
        except AudioError:
            if not coverfile:
                raise
            # A malformed cover image should not cost the user the whole audiobook.
            fallback = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "concat", "-safe", "0", "-i", listfile,
                        "-i", metafile, "-map", "0:a", "-map_metadata", "1",
                        "-codec:a", "aac", "-b:a", bitrate, "-ar", "24000", "-ac", "1",
                        "-movflags", "+faststart", "-f", "mp4", out_path]
            _run(fallback)
        return out_path
    finally:
        for name in os.listdir(workdir):
            try:
                os.remove(os.path.join(workdir, name))
            except OSError:
                pass
        os.rmdir(workdir)


def safe_filename(name: str, fallback: str = "chapter") -> str:
    """Make a chapter title safe for a filename on macOS, Windows and iOS."""
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", " ", name or "")
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = fallback
    return name[:80]
