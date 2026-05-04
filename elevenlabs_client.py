"""ElevenLabs async client wrapper for TTS and STT (Scribe)."""

from __future__ import annotations

import io
from typing import Optional

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
    ) -> dict:
        """Transcribe audio con Scribe.

        Devuelve dict con:
          - text: str  (transcript limpio)
          - audio_events: list[str]  (ej: ['laughter', 'applause'])
          - speakers: list[str] | None (si Scribe diariza)
        Eventos detectados se inyectan inline en el texto que se manda a Claude
        para que pueda adecuar su respuesta y emoción.
        """
        client = self._ensure_client()
        lang = language_code or settings.VOICE_LANGUAGE
        buf = io.BytesIO(audio_bytes)
        buf.name = filename

        # tag_audio_events=True → Scribe detecta risas, aplausos, etc.
        # diarize=True → identifica hablantes (útil si hay varios en el mismo buffer)
        try:
            result = await client.speech_to_text.convert(
                file=buf,
                model_id=settings.ELEVENLABS_STT_MODEL,
                language_code=lang,
                tag_audio_events=True,
                diarize=False,  # ya bufferamos por user_id en VC, no hace falta
            )
        except TypeError:
            # SDK antiguo: fallback sin esos params
            buf.seek(0)
            result = await client.speech_to_text.convert(
                file=buf,
                model_id=settings.ELEVENLABS_STT_MODEL,
                language_code=lang,
            )

        text = getattr(result, "text", None)
        if text is None and isinstance(result, dict):
            text = result.get("text", "")
        text = (text or "").strip()

        # Extraer audio events (palabras tipo "[laughter]", "[applause]")
        # Scribe los devuelve inline en el texto Y/O en .words con type='audio_event'
        audio_events: list[str] = []
        words = getattr(result, "words", None) or (
            result.get("words") if isinstance(result, dict) else None
        )
        if words:
            for w in words:
                w_type = getattr(w, "type", None) or (
                    w.get("type") if isinstance(w, dict) else None
                )
                w_text = getattr(w, "text", None) or (
                    w.get("text") if isinstance(w, dict) else None
                )
                if w_type == "audio_event" and w_text:
                    audio_events.append(w_text.strip("[]"))

        return {
            "text": text,
            "audio_events": audio_events,
        }


elevenlabs_client = ElevenLabsClient()
