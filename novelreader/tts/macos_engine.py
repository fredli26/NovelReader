"""macOS built-in `say` — fully offline, but the Cantonese voice is not neural."""
from __future__ import annotations

import os
import platform
import re
import subprocess

from .base import Engine, TTSError, Voice

_BASE_WPM = 180  # `say` default speaking rate


class MacSayEngine(Engine):
    id = "macos"
    label = "macOS say (offline)"
    description = ("Apple's built-in voices. Works with no internet at all, but the "
                   "Cantonese voice (Sinji) is Apple's older, robotic-sounding engine.")
    needs_network = False

    def available(self) -> tuple[bool, str]:
        if platform.system() != "Darwin":
            return False, "Only available on macOS"
        if not self.voices():
            return False, ("No Cantonese (zh_HK) voice installed. Add one under System Settings › "
                           "Accessibility › Spoken Content › System Voice › Manage Voices.")
        return True, ""

    def voices(self) -> list[Voice]:
        try:
            out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True,
                                 timeout=15).stdout
        except Exception:
            return []
        found = []
        for line in out.splitlines():
            match = re.match(r"^(.+?)\s{2,}(zh_HK|yue\S*)\s", line)
            if match:
                name = match.group(1).strip()
                found.append(Voice(name, f"{name} — macOS offline", "", "Non-neural"))
        return found

    def synth(self, text: str, out_path: str, voice: str,
              rate: int = 0, pitch: int = 0) -> None:
        voice = voice or self.default_voice()
        if not voice:
            raise TTSError("No Cantonese voice is installed for macOS `say`.")

        wpm = max(90, min(400, int(_BASE_WPM * (1 + rate / 100))))
        aiff = out_path + ".aiff"
        try:
            proc = subprocess.run(
                # AIFF is big-endian; LEI16 here makes `say` reject the output file.
                ["say", "-v", voice, "-r", str(wpm), "-o", aiff,
                 "--data-format=BEI16@22050", "--file-format=AIFF"],
                input=text, text=True, capture_output=True, timeout=900,
            )
            if proc.returncode != 0 or not os.path.exists(aiff):
                raise TTSError(f"`say` failed: {proc.stderr.strip()[:300]}")

            # `say` cannot emit MP3; hand the PCM to ffmpeg.
            conv = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-i", aiff, "-codec:a", "libmp3lame", "-b:a", "96k", "-ar", "24000",
                 "-ac", "1", out_path],
                capture_output=True, text=True, timeout=600,
            )
            if conv.returncode != 0:
                raise TTSError(f"ffmpeg conversion failed: {conv.stderr.strip()[:300]}")
        finally:
            if os.path.exists(aiff):
                os.remove(aiff)
