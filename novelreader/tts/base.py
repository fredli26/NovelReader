"""Common interface every TTS engine implements."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Voice:
    id: str
    label: str
    gender: str = ""
    note: str = ""


class TTSError(RuntimeError):
    pass


class Engine:
    """A speech backend.

    `synth` must write a single decodable audio file to `out_path` (any format
    ffmpeg can read) and raise TTSError on failure so the caller can retry.
    """

    id: str = ""
    label: str = ""
    description: str = ""
    needs_network: bool = False

    def available(self) -> tuple[bool, str]:
        """Return (usable, reason-if-not)."""
        return True, ""

    def voices(self) -> list[Voice]:
        raise NotImplementedError

    def default_voice(self) -> str:
        voices = self.voices()
        return voices[0].id if voices else ""

    def synth(self, text: str, out_path: str, voice: str,
              rate: int = 0, pitch: int = 0) -> None:
        """Speak `text` into `out_path`. rate/pitch are percentages, 0 = natural."""
        raise NotImplementedError
