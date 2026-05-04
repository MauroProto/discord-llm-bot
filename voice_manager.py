"""Voice manager: join/leave VC, capture per-user audio, STT, generate, TTS, play.

Toda la lógica de voz está aislada acá. El resto del bot solo sabe de VoiceManager.

Pipeline:
  Discord VC -> AudioSink (sync write, en thread de voice_recv)
             -> per-user PCM buffer
             -> al detectar silencio (~VOICE_SILENCE_MS) flush:
                PCM -> WAV mono 16k -> ElevenLabs Scribe -> texto
             -> guardar en daily .md (mismo archivo que texto)
             -> si corresponde, generar respuesta con Claude (modo voz)
             -> ElevenLabs TTS -> ffmpeg pipe -> voice_client.play()
"""

from __future__ import annotations

import asyncio
import audioop
import io
import time
import wave
from collections import deque
from datetime import datetime
from typing import Optional

import discord

try:
    from discord.ext import voice_recv  # type: ignore
    VOICE_RECV_AVAILABLE = True
except ImportError:
    voice_recv = None  # type: ignore
    VOICE_RECV_AVAILABLE = False

from config import settings
from context_manager import context_manager
from claude_client import claude_client
from elevenlabs_client import elevenlabs_client


# Discord voice_recv entrega PCM 48kHz, 16-bit, stereo
PCM_SAMPLE_RATE = 48_000
PCM_SAMPLE_WIDTH = 2  # 16-bit
PCM_CHANNELS = 2

# STT: downmuestreamos a 16kHz mono para ahorrar bandwidth y tokens
STT_SAMPLE_RATE = 16_000
STT_CHANNELS = 1


def _pcm_to_wav_mono16k(pcm_stereo_48k: bytes) -> bytes:
    """Convierte PCM stereo 48kHz 16-bit a WAV mono 16kHz 16-bit."""
    if not pcm_stereo_48k:
        return b""
    # stereo -> mono
    mono_48k = audioop.tomono(pcm_stereo_48k, PCM_SAMPLE_WIDTH, 1, 1)
    # 48k -> 16k
    mono_16k, _ = audioop.ratecv(
        mono_48k, PCM_SAMPLE_WIDTH, STT_CHANNELS, PCM_SAMPLE_RATE, STT_SAMPLE_RATE, None
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(STT_CHANNELS)
        wf.setsampwidth(PCM_SAMPLE_WIDTH)
        wf.setframerate(STT_SAMPLE_RATE)
        wf.writeframes(mono_16k)
    return buf.getvalue()


def _has_voice_signal(pcm: bytes, threshold_rms: int = 200) -> bool:
    """Heurística simple: rechaza buffers que son básicamente silencio."""
    if not pcm:
        return False
    try:
        rms = audioop.rms(pcm, PCM_SAMPLE_WIDTH)
    except audioop.error:
        return False
    return rms >= threshold_rms


def _build_sink_class():
    """Construye dinámicamente la clase sink heredando de voice_recv.AudioSink."""
    if not VOICE_RECV_AVAILABLE:
        return None

    class Sink(voice_recv.AudioSink):  # type: ignore[misc]
        def __init__(self, session: "VoiceSession", loop: asyncio.AbstractEventLoop):
            super().__init__()
            self._session = session
            self._loop = loop

        def wants_opus(self) -> bool:
            return False

        def write(self, user, data) -> None:
            if user is None or getattr(user, "bot", False):
                return
            pcm = getattr(data, "pcm", None)
            if not pcm:
                return
            asyncio.run_coroutine_threadsafe(
                self._session._on_user_audio(user.id, str(user), pcm), self._loop
            )

        def cleanup(self) -> None:
            pass

    return Sink


class VoiceSession:
    """Estado de una conexión de Lain a un canal de voz de un guild."""

    def __init__(
        self,
        voice_client: discord.VoiceClient,
        text_channel: discord.abc.Messageable,
        voice_channel: discord.VoiceChannel,
        loop: asyncio.AbstractEventLoop,
    ):
        self.voice_client = voice_client
        self.text_channel = text_channel
        self.voice_channel = voice_channel
        self.loop = loop

        # Buffers de PCM crudo por usuario
        self._buffers: dict[int, bytearray] = {}
        self._authors: dict[int, str] = {}
        self._buffer_started_at: dict[int, float] = {}
        self._silence_tasks: dict[int, asyncio.Task] = {}

        # Últimos turnos de voz (para inyectar como contexto inmediato a Claude)
        self.recent_transcripts: deque[dict] = deque(maxlen=settings.VOICE_RECENT_TURNS)

        # Sincronización
        self._respond_lock = asyncio.Lock()
        self._last_response_at: float = 0.0
        self._closed = False

    # --- Captura ---

    async def _on_user_audio(self, user_id: int, author: str, pcm: bytes) -> None:
        if self._closed:
            return
        buf = self._buffers.setdefault(user_id, bytearray())
        if not buf:
            self._buffer_started_at[user_id] = time.monotonic()
        buf.extend(pcm)
        self._authors[user_id] = author

        # Reinicia el timer de silencio
        existing = self._silence_tasks.get(user_id)
        if existing and not existing.done():
            existing.cancel()
        self._silence_tasks[user_id] = asyncio.create_task(
            self._silence_timer(user_id)
        )

        # Force-flush si el turno se hizo demasiado largo
        started = self._buffer_started_at.get(user_id, 0)
        if time.monotonic() - started >= settings.VOICE_MAX_TURN_SECONDS:
            await self._flush_user(user_id)

    async def _silence_timer(self, user_id: int) -> None:
        try:
            await asyncio.sleep(settings.VOICE_SILENCE_MS / 1000)
        except asyncio.CancelledError:
            return
        await self._flush_user(user_id)

    async def _flush_user(self, user_id: int) -> None:
        if self._closed:
            return
        buf = self._buffers.pop(user_id, None)
        self._buffer_started_at.pop(user_id, None)
        task = self._silence_tasks.pop(user_id, None)
        if task and not task.done():
            task.cancel()
        if not buf:
            return

        pcm_bytes = bytes(buf)
        if not _has_voice_signal(pcm_bytes):
            return

        author = self._authors.get(user_id, f"user_{user_id}")

        try:
            wav = _pcm_to_wav_mono16k(pcm_bytes)
            stt_result = await elevenlabs_client.stt(wav, filename="voice.wav")
        except Exception as e:
            print(f"[VOZ][STT] error: {e}")
            return

        transcript = stt_result.get("text", "")
        events = stt_result.get("audio_events", [])

        if not transcript or len(transcript) < settings.VOICE_MIN_TURN_CHARS:
            # Si solo hubo audio_events sin texto (ej: risa pura), igual lo registramos
            if events:
                evt_str = " ".join(f"[{e}]" for e in events)
                await self._handle_transcript(author, evt_str, events=events)
            return

        # Enriquecemos el transcript con los eventos detectados al final
        if events:
            transcript = f"{transcript}  ({', '.join(events)})"

        await self._handle_transcript(author, transcript, events=events)

    # --- Manejo de transcript ---

    async def _handle_transcript(
        self,
        author: str,
        text: str,
        events: list[str] | None = None,
    ) -> None:
        print(f"[VOZ] {author}: {text}" + (f"  events={events}" if events else ""))

        # 1) Guardar en memoria a corto plazo
        self.recent_transcripts.append({
            "author": author,
            "text": text,
            "ts": datetime.now().isoformat(),
        })

        # 2) Persistir en .md diario (mismo archivo que el texto)
        try:
            context_manager.save_daily_context(
                channel_id=self.voice_channel.id,
                bot_response=None,
                query=text,
                author=author,
                channel_name=f"VOZ:{self.voice_channel.name}",
            )
        except Exception as e:
            print(f"[VOZ] no pude guardar transcript: {e}")

        # 3) ¿Respondemos?
        if not self._should_respond(text):
            return

        if not await self._cooldown_ok():
            return

        await self._respond(text)

    def _should_respond(self, text: str) -> bool:
        if settings.VOICE_ALWAYS_RESPOND:
            return True
        words = [w.strip().lower() for w in settings.VOICE_WAKE_WORDS.split(",") if w.strip()]
        lower = text.lower()
        return any(w in lower for w in words)

    async def _cooldown_ok(self) -> bool:
        delta_ms = (time.monotonic() - self._last_response_at) * 1000
        return delta_ms >= settings.VOICE_COOLDOWN_MS

    async def _respond(self, last_user_text: str) -> None:
        # Serializa: una respuesta a la vez
        async with self._respond_lock:
            try:
                # Construir mensajes para Claude
                claude_messages = self._build_claude_messages()
                memory = context_manager.load_recent_memory(
                    days=settings.MEMORY_DAYS,
                    max_chars=settings.MEMORY_MAX_CHARS,
                )

                response = await claude_client.generate_voice_response(
                    messages=claude_messages,
                    memory_text=memory,
                )

                if not response:
                    return

                # Guardar respuesta en .md diario
                try:
                    context_manager.save_daily_context(
                        channel_id=self.voice_channel.id,
                        bot_response=response,
                        query=None,
                        author=None,
                        channel_name=f"VOZ:{self.voice_channel.name}",
                    )
                except Exception as e:
                    print(f"[VOZ] no pude guardar respuesta: {e}")

                self.recent_transcripts.append({
                    "author": "Lain",
                    "text": response,
                    "ts": datetime.now().isoformat(),
                    "is_bot": True,
                })

                # TTS + play
                await self.speak(response)
                self._last_response_at = time.monotonic()

            except Exception as e:
                print(f"[VOZ] error generando/respondiendo: {e}")

    def _build_claude_messages(self) -> list[dict]:
        """Convierte recent_transcripts a formato Claude messages."""
        msgs: list[dict] = []
        for t in self.recent_transcripts:
            if t.get("is_bot"):
                msgs.append({"role": "assistant", "content": t["text"]})
            else:
                msgs.append({
                    "role": "user",
                    "content": f"{t['author']}: {t['text']}",
                })
        # Asegurar que el último sea user (sino Claude no responde)
        if not msgs or msgs[-1]["role"] != "user":
            msgs.append({"role": "user", "content": "(continúa la conversación)"})
        return msgs

    # --- TTS / playback ---

    async def speak(self, text: str) -> None:
        """Genera TTS y lo reproduce en el VC. Bloquea hasta terminar."""
        if not self.voice_client or not self.voice_client.is_connected():
            return
        try:
            mp3 = await elevenlabs_client.tts(text)
        except Exception as e:
            print(f"[VOZ][TTS] error: {e}")
            return

        # Esperar si Lain ya está hablando
        while self.voice_client.is_playing():
            await asyncio.sleep(0.1)

        source = discord.FFmpegPCMAudio(io.BytesIO(mp3), pipe=True)
        done = asyncio.Event()

        def _after(err):
            if err:
                print(f"[VOZ][PLAY] error: {err}")
            self.loop.call_soon_threadsafe(done.set)

        self.voice_client.play(source, after=_after)
        await done.wait()

    # --- Lifecycle ---

    async def close(self) -> None:
        self._closed = True
        # Cancelar timers
        for t in list(self._silence_tasks.values()):
            if not t.done():
                t.cancel()
        self._silence_tasks.clear()
        self._buffers.clear()

        if self.voice_client:
            try:
                if self.voice_client.is_playing():
                    self.voice_client.stop()
                # Stop listening si aplica
                if hasattr(self.voice_client, "stop_listening"):
                    try:
                        self.voice_client.stop_listening()
                    except Exception:
                        pass
                await self.voice_client.disconnect(force=True)
            except Exception as e:
                print(f"[VOZ] error desconectando: {e}")


class VoiceManager:
    """Mantiene una VoiceSession por guild."""

    def __init__(self) -> None:
        self._sessions: dict[int, VoiceSession] = {}

    def get(self, guild_id: int) -> Optional[VoiceSession]:
        return self._sessions.get(guild_id)

    def is_connected(self, guild_id: int) -> bool:
        s = self._sessions.get(guild_id)
        return bool(s and s.voice_client and s.voice_client.is_connected())

    async def join(
        self,
        voice_channel: discord.VoiceChannel,
        text_channel: discord.abc.Messageable,
    ) -> VoiceSession:
        if not VOICE_RECV_AVAILABLE:
            raise RuntimeError(
                "discord-ext-voice-recv no instalado. Agregá `discord-ext-voice-recv` "
                "a requirements.txt y reinstalá."
            )
        if not settings.ELEVENLABS_API_KEY:
            raise RuntimeError("Falta ELEVENLABS_API_KEY en .env")

        guild_id = voice_channel.guild.id
        existing = self._sessions.get(guild_id)
        if existing:
            await existing.close()
            self._sessions.pop(guild_id, None)

        voice_client = await voice_channel.connect(
            cls=voice_recv.VoiceRecvClient,  # type: ignore[union-attr]
            self_deaf=False,
        )

        loop = asyncio.get_running_loop()
        session = VoiceSession(
            voice_client=voice_client,
            text_channel=text_channel,
            voice_channel=voice_channel,
            loop=loop,
        )

        SinkCls = _build_sink_class()
        sink = SinkCls(session, loop)  # type: ignore[misc]
        voice_client.listen(sink)

        self._sessions[guild_id] = session
        return session

    async def leave(self, guild_id: int) -> bool:
        session = self._sessions.pop(guild_id, None)
        if not session:
            return False
        await session.close()
        return True

    async def close_all(self) -> None:
        for gid in list(self._sessions.keys()):
            await self.leave(gid)


voice_manager = VoiceManager()
