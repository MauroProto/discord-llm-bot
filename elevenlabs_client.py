"""ElevenLabs async client wrapper for TTS and STT (Scribe)."""

from __future__ import annotations

import asyncio
import io
from typing import Optional

import aiohttp

from config import settings


class ElevenLabsClient:
    """Lazy-init async wrapper around the elevenlabs SDK."""

    def __init__(self) -> None:
        self._client = None  # type: ignore[assignment]

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not settings.ELEVENLABS_API_KEY:
            raise RuntimeError(
                "ELEVENLABS_API_KEY no configurada. Seteala en .env para usar voz."
            )
        try:
            from elevenlabs.client import AsyncElevenLabs
        except ImportError as e:
            raise RuntimeError(
                "Falta el paquete 'elevenlabs'. Instalalo con `pip install elevenlabs`."
            ) from e
        self._client = AsyncElevenLabs(api_key=settings.ELEVENLABS_API_KEY)
        return self._client

    def _voice_settings(self) -> dict:
        """Construye el dict de voice_settings desde config."""
        return {
            "stability": settings.VOICE_STABILITY,
            "similarity_boost": settings.VOICE_SIMILARITY_BOOST,
            "style": settings.VOICE_STYLE,
            "use_speaker_boost": settings.VOICE_USE_SPEAKER_BOOST,
            "speed": settings.VOICE_SPEED,
        }

    async def tts(
        self,
        text: str,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> bytes:
        """Convierte texto a MP3 usando ElevenLabs.

        Aplica voice_settings (speed, stability, etc.) configurables.
        Devuelve los bytes completos del audio MP3.
        """
        client = self._ensure_client()
        vid = voice_id or settings.ELEVENLABS_VOICE_ID
        mid = model_id or settings.ELEVENLABS_TTS_MODEL

        # voice_settings: el SDK acepta dict o el modelo VoiceSettings
        try:
            from elevenlabs import VoiceSettings  # type: ignore
            vs = VoiceSettings(**self._voice_settings())
        except Exception:
            vs = self._voice_settings()  # fallback dict

        audio_iter = client.text_to_speech.convert(
            voice_id=vid,
            model_id=mid,
            text=text,
            output_format="mp3_44100_128",
            voice_settings=vs,
        )

        chunks: list[bytes] = []
        async for chunk in audio_iter:
            if chunk:
                chunks.append(chunk)
        return b"".join(chunks)

    async def stt(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        language_code: Optional[str] = None,
        timeout_seconds: float = 20.0,
    ) -> dict:
        """Transcribe audio con Scribe via HTTP REST directo.

        El SDK async-elevenlabs en 2.34.0 a veces queda colgado con audios
        largos. Usamos aiohttp directo, que respeta timeouts.

        Devuelve dict con:
          - text: str  (transcript limpio)
          - audio_events: list[str]  (ej: ['laughter', 'applause'])
        """
        if not settings.ELEVENLABS_API_KEY:
            raise RuntimeError("ELEVENLABS_API_KEY no configurada")

        lang = language_code or settings.VOICE_LANGUAGE
        url = "https://api.elevenlabs.io/v1/speech-to-text"

        form = aiohttp.FormData()
        form.add_field("model_id", settings.ELEVENLABS_STT_MODEL)
        form.add_field("language_code", lang)
        form.add_field("tag_audio_events", "true")
        form.add_field("diarize", "false")
        form.add_field(
            "file",
            audio_bytes,
            filename=filename,
            content_type="audio/wav",
        )

        headers = {"xi-api-key": settings.ELEVENLABS_API_KEY}
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=form, headers=headers) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(
                        f"Scribe HTTP {resp.status}: {body[:200]}"
                    )
                import json
                try:
                    result = json.loads(body)
                except Exception as e:
                    raise RuntimeError(f"Scribe respuesta no-JSON: {body[:200]}") from e

        text = (result.get("text") or "").strip()

        audio_events: list[str] = []
        for w in result.get("words", []) or []:
            if isinstance(w, dict) and w.get("type") == "audio_event":
                t = w.get("text") or ""
                if t:
                    audio_events.append(t.strip("[]"))

        return {
            "text": text,
            "audio_events": audio_events,
        }


elevenlabs_client = ElevenLabsClient()
