"""Azure Cognitive Services Speech — same neural voices as Edge, via a paid key.

Uses the plain REST endpoint so no heavy SDK is required. Set the key and region
as environment variables before starting the server:

    export AZURE_SPEECH_KEY=...
    export AZURE_SPEECH_REGION=southeastasia
"""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from xml.sax.saxutils import escape

from .base import Engine, TTSError, Voice

_VOICES = [
    Voice("zh-HK-HiuMaanNeural", "HiuMaan 曉曼 — female, warm narrator", "Female"),
    Voice("zh-HK-WanLungNeural", "WanLung 雲龍 — male, steady", "Male"),
    Voice("zh-HK-HiuGaaiNeural", "HiuGaai 曉佳 — female, brighter", "Female"),
    Voice("yue-CN-XiaoMinNeural", "XiaoMin 曉敏 — female, mainland Cantonese", "Female"),
    Voice("yue-CN-YunSongNeural", "YunSong 雲松 — male, mainland Cantonese", "Male"),
]


class AzureEngine(Engine):
    id = "azure"
    label = "Azure Speech (API key)"
    description = ("Official Microsoft Speech service. Same neural voices as Edge plus "
                   "mainland Cantonese, with an SLA and SSML control. Billed per character.")
    needs_network = True

    def _credentials(self) -> tuple[str, str]:
        return (os.environ.get("AZURE_SPEECH_KEY", "").strip(),
                os.environ.get("AZURE_SPEECH_REGION", "").strip())

    def available(self) -> tuple[bool, str]:
        key, region = self._credentials()
        if not key or not region:
            return False, "Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION to enable Azure."
        return True, ""

    def voices(self) -> list[Voice]:
        return list(_VOICES)

    def synth(self, text: str, out_path: str, voice: str,
              rate: int = 0, pitch: int = 0) -> None:
        key, region = self._credentials()
        if not key or not region:
            raise TTSError("Azure credentials are not configured.")

        voice = voice or self.default_voice()
        lang = "yue-CN" if voice.startswith("yue-") else "zh-HK"
        ssml = (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xml:lang="{lang}"><voice name="{voice}">'
            f'<prosody rate="{rate:+d}%" pitch="{pitch:+d}%">{escape(text)}</prosody>'
            f"</voice></speak>"
        ).encode("utf-8")

        url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3",
            "User-Agent": "NovelReader",
        }

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, data=ssml, headers=headers, method="POST")
                with urllib.request.urlopen(request, timeout=180) as response:
                    audio = response.read()
                if len(audio) > 512:
                    with open(out_path, "wb") as handle:
                        handle.write(audio)
                    return
                last_error = TTSError("Azure returned an empty audio stream")
            except urllib.error.HTTPError as exc:
                detail = exc.read()[:200].decode("utf-8", "replace")
                last_error = TTSError(f"HTTP {exc.code}: {detail}")
                if exc.code in (401, 403):  # bad key — retrying will not help
                    break
            except Exception as exc:
                last_error = exc
            time.sleep(2 * (attempt + 1))

        raise TTSError(f"Azure TTS failed: {last_error}")
