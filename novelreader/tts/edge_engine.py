"""Microsoft Edge neural voices — free, no API key, requires internet."""
from __future__ import annotations

import asyncio
import os
import time

from .base import Engine, TTSError, Voice

# Pinned so the UI works offline; verified against edge_tts.list_voices().
_VOICES = [
    Voice("zh-HK-HiuMaanNeural", "HiuMaan 曉曼 — female, warm narrator", "Female",
          "Best all-round pick for novels"),
    Voice("zh-HK-WanLungNeural", "WanLung 雲龍 — male, steady", "Male",
          "Good for non-fiction and male narrators"),
    Voice("zh-HK-HiuGaaiNeural", "HiuGaai 曉佳 — female, brighter", "Female",
          "Livelier, slightly faster feel"),
]


class EdgeEngine(Engine):
    id = "edge"
    label = "Edge Neural (free)"
    description = "Microsoft's natural Cantonese neural voices. Free, no API key, needs internet."
    needs_network = True

    def available(self) -> tuple[bool, str]:
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            return False, "edge-tts is not installed (pip install edge-tts)"
        return True, ""

    def voices(self) -> list[Voice]:
        return list(_VOICES)

    def synth(self, text: str, out_path: str, voice: str,
              rate: int = 0, pitch: int = 0) -> None:
        import edge_tts

        voice = voice or self.default_voice()
        params = {
            "rate": f"{rate:+d}%",
            "pitch": f"{pitch:+d}Hz",
        }

        async def run() -> None:
            comm = edge_tts.Communicate(text, voice, **params)
            await comm.save(out_path)

        last_error: Exception | None = None
        # The free endpoint drops connections under load; a few backoffs fix it.
        for attempt in range(4):
            try:
                asyncio.run(run())
                if os.path.exists(out_path) and os.path.getsize(out_path) > 512:
                    return
                last_error = TTSError("Edge returned an empty audio stream")
            except Exception as exc:  # network, throttling, malformed chunk
                last_error = exc
            time.sleep(1.5 * (attempt + 1))

        raise TTSError(f"Edge TTS failed after 4 attempts: {last_error}")
