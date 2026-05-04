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
import re
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


def _ensure_opus_loaded() -> None:
    """En Linux/Docker discord.py a veces no encuentra libopus solo. Lo cargamos."""
    if not VOICE_RECV_AVAILABLE:
        return
    try:
        if discord.opus.is_loaded():
            return
        for name in ("libopus.so.0", "libopus.so", "opus"):
            try:
                discord.opus.load_opus(name)
                print(f"[OPUS] libopus cargada via {name}")
                return
            except OSError:
                continue
        print("[OPUS] WARN: no pude cargar libopus explícitamente")
    except Exception as e:
        print(f"[OPUS] error cargando libopus: {e}")


_opus_errors_seen = 0
_opus_ok_seen = 0
_dave_decrypts_seen = 0
_dave_errors_seen = 0


def _patch_voice_recv_for_dave() -> None:
    """Monkey-patch para soportar DAVE encryption (Discord 2026+).

    Discord migró a DAVE (E2EE basado en MLS). discord-ext-voice-recv 0.5.2a179
    NO desencripta DAVE antes de pasar al decoder Opus → 'corrupted stream'.

    Replicamos el fix del PR #54 (rdphillips7) inline:
    - Antes de decode Opus, llamar dave_session.decrypt() para sacar la capa DAVE
    - Try/except defensivo en _decode_packet
    - Activar passthrough_mode al construir el decoder
    - Filtrar data.source None en el router

    Refs:
      https://github.com/imayhaveborkedit/discord-ext-voice-recv/issues/53
      https://github.com/imayhaveborkedit/discord-ext-voice-recv/pull/54
    """
    if not VOICE_RECV_AVAILABLE:
        return

    # Detectar si el paquete davey está disponible (lo trae discord.py 2.7+)
    try:
        from davey import MediaType  # type: ignore
        has_dave = True
        print("[DAVE] paquete davey disponible — DAVE soportado")
    except ImportError:
        MediaType = None  # type: ignore
        has_dave = False
        print("[DAVE] paquete davey NO disponible — Discord puede rechazar la conexión")

    try:
        from discord.ext.voice_recv import opus as vr_opus  # type: ignore
        from discord.ext.voice_recv.opus import VoiceData  # type: ignore

        if getattr(vr_opus, "_lain_dave_patched", False):
            return

        decoder_cls = vr_opus.PacketDecoder
        _orig_init = decoder_cls.__init__
        _orig_decode = decoder_cls._decode_packet
        _orig_process = decoder_cls._process_packet

        def patched_init(self, router, ssrc):
            _orig_init(self, router, ssrc)
            # Activa passthrough mode en la dave_session si existe
            try:
                vc = self.sink.voice_client
                sess = getattr(vc._connection, "dave_session", None)
                if sess is not None and hasattr(sess, "set_passthrough_mode"):
                    sess.set_passthrough_mode(True, 10)
                    print(f"[DAVE] passthrough_mode activado para SSRC {ssrc}")
            except Exception as e:
                print(f"[DAVE] no pude activar passthrough en init: {e}")

        def patched_decode(self, packet):
            global _opus_errors_seen, _opus_ok_seen
            if not packet:
                return _orig_decode(self, packet)
            try:
                pcm = self._decoder.decode(packet.decrypted_data, fec=False)
                _opus_ok_seen += 1
                if _opus_ok_seen <= 3 or _opus_ok_seen % 1000 == 0:
                    print(f"[OPUS] decode ok #{_opus_ok_seen} (errs={_opus_errors_seen})")
                return packet, pcm
            except Exception as e:
                _opus_errors_seen += 1
                if _opus_errors_seen <= 5 or _opus_errors_seen % 500 == 0:
                    print(f"[OPUS] decode FALLO #{_opus_errors_seen}: {e}")
                try:
                    pcm = self._decoder.decode(None, fec=False)
                except Exception:
                    pcm = b""
                return packet, pcm

        def patched_process(self, packet):
            """Replica _process_packet del PR #54 con desencriptación DAVE.

            Diferencia clave vs PR original: si member es None devolvemos None
            (no VoiceData(source=None)) para que el _do_run ORIGINAL lo filtre
            sin que tengamos que parcharlo.
            """
            global _dave_decrypts_seen, _dave_errors_seen
            pcm = None

            try:
                member = self._get_cached_member()
                if member is None:
                    try:
                        self._cached_id = self.sink.voice_client._get_id_from_ssrc(self.ssrc)
                        member = self._get_cached_member()
                    except Exception:
                        member = None

                # Capa DAVE: desencriptar antes del Opus decode
                if (
                    has_dave
                    and member is not None
                    and not packet.is_silence()
                    and packet.decrypted_data is not None
                ):
                    try:
                        sess = self.sink.voice_client._connection.dave_session
                    except Exception:
                        sess = None
                    if sess is not None and getattr(sess, "ready", False):
                        try:
                            packet.decrypted_data = sess.decrypt(
                                member.id, MediaType.audio, bytes(packet.decrypted_data)
                            )
                            _dave_decrypts_seen += 1
                            if _dave_decrypts_seen <= 3 or _dave_decrypts_seen % 1000 == 0:
                                print(f"[DAVE] decrypt ok #{_dave_decrypts_seen} (errs={_dave_errors_seen})")
                        except Exception as e:
                            _dave_errors_seen += 1
                            if _dave_errors_seen <= 5 or _dave_errors_seen % 500 == 0:
                                print(f"[DAVE] decrypt FALLO #{_dave_errors_seen}: {e}")
                            self._last_seq = packet.sequence
                            self._last_ts = packet.timestamp
                            return None  # filtrado por el router original

                if not self.sink.wants_opus():
                    try:
                        packet, pcm = self._decode_packet(packet)
                    except Exception as e:
                        print(f"[OPUS] decode_packet outer error: {e}")
                        pcm = b""

                data = VoiceData(packet, member, pcm=pcm)
                self._last_seq = packet.sequence
                self._last_ts = packet.timestamp
                return data
            except Exception as e:
                print(f"[VOZ] patched_process unexpected: {type(e).__name__}: {e}")
                return None

        decoder_cls.__init__ = patched_init
        decoder_cls._decode_packet = patched_decode
        decoder_cls._process_packet = patched_process

        vr_opus._lain_dave_patched = True
        print("[DAVE] monkey-patch aplicado: PacketDecoder.__init__/_decode_packet/_process_packet (sin tocar router)")
    except Exception as e:
        import traceback
        print(f"[DAVE] no pude aplicar el patch: {e}")
        traceback.print_exc()


_ensure_opus_loaded()
_patch_voice_recv_for_dave()

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


_CHAT_RE = re.compile(r"\[CHAT:\s*(.+?)\]", re.IGNORECASE | re.DOTALL)
_SOLO_CHAT_RE = re.compile(r"\[SOLO_CHAT:\s*(.+?)\]", re.IGNORECASE | re.DOTALL)


def _split_voice_and_chat(response: str) -> tuple[str, list[str]]:
    """Separa la respuesta de Claude en parte voz y parte(s) chat.

    Reconoce:
      [SOLO_CHAT: ...]  → no se dice nada por voz, todo va al chat
      [CHAT: ...]       → lo de afuera va a voz, lo de adentro al chat
    Devuelve (texto_voz, [textos_chat]).
    """
    response = response.strip()
    if not response:
        return "", []

    # SOLO_CHAT: solo va al chat (puede haber varios)
    solo_chats = [m.group(1).strip() for m in _SOLO_CHAT_RE.finditer(response)]
    if solo_chats:
        # Si hay SOLO_CHAT, sacamos toda la respuesta del flujo de voz
        return "", solo_chats

    # CHAT: extraer del medio
    chats = [m.group(1).strip() for m in _CHAT_RE.finditer(response)]
    voice = _CHAT_RE.sub("", response).strip()
    voice = re.sub(r"\s+", " ", voice)
    return voice, chats


def _coalesce_messages(msgs: list[dict]) -> list[dict]:
    """Colapsa mensajes consecutivos del mismo role (Claude requiere alternancia)."""
    if not msgs:
        return msgs
    out: list[dict] = [msgs[0]]
    for m in msgs[1:]:
        if m["role"] == out[-1]["role"]:
            prev = out[-1]
            prev_content = prev["content"] if isinstance(prev["content"], str) else str(prev["content"])
            cur_content = m["content"] if isinstance(m["content"], str) else str(m["content"])
            out[-1] = {"role": prev["role"], "content": prev_content + "\n" + cur_content}
        else:
            out.append(m)
    return out


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
            self._packet_count = 0
            self._first_logged = False

        def wants_opus(self) -> bool:
            return False

        def write(self, user, data) -> None:
            if user is None or getattr(user, "bot", False):
                return
            pcm = getattr(data, "pcm", None)
            if not pcm:
                return
            self._packet_count += 1
            if not self._first_logged:
                print(f"[VOZ] primer paquete PCM recibido de {user} ({len(pcm)} bytes)")
                self._first_logged = True
            elif self._packet_count % 500 == 0:
                print(f"[VOZ] {self._packet_count} paquetes PCM acumulados")
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
        # Cancel-on-new-input: solo procesa la respuesta más reciente
        self._current_response_task: asyncio.Task | None = None
        self._current_stt_task: asyncio.Task | None = None
        # Anti-eco: cuando Lain habla, su voz se rebota por mic de los humanos
        # → ignorar audio entrante mientras habla + ECHO_GUARD_MS después
        self._is_speaking: bool = False
        self._speak_finished_at: float = 0.0

        # Idle auto-leave
        self._last_audio_at: float = time.monotonic()
        self._idle_task: asyncio.Task | None = None
        if settings.VOICE_IDLE_TIMEOUT_SECONDS > 0:
            self._idle_task = asyncio.create_task(self._idle_watchdog())

    # --- Captura ---

    async def _on_user_audio(self, user_id: int, author: str, pcm: bytes) -> None:
        if self._closed:
            return

        # ANTI-ECO: si Lain está hablando o terminó hace muy poco, IGNORAR audio
        # entrante. Su propia voz se mete por el mic de los humanos y dispara
        # falsos barge-in. ECHO_GUARD_MS es la ventana posterior al TTS.
        ECHO_GUARD_MS = 800
        if self._is_speaking:
            return
        if self._speak_finished_at and (
            time.monotonic() - self._speak_finished_at < ECHO_GUARD_MS / 1000
        ):
            return

        # VAD por paquete: si es silencio (PCM cerca de ceros), NO extender el buffer
        # y NO resetear el silence_timer. Threshold alto para evitar ruido de fondo.
        is_voice = _has_voice_signal(pcm, threshold_rms=400)

        if not is_voice:
            return

        # Resetea timer de inactividad: hay actividad humana real en el VC
        self._last_audio_at = time.monotonic()

        buf = self._buffers.setdefault(user_id, bytearray())
        if not buf:
            self._buffer_started_at[user_id] = time.monotonic()
            print(f"[VOZ] inicio de turno: {author}")
            # Cancelar Claude/TTS PENDIENTE (en proceso de generar) si todavía
            # no empezó a sonar. Si ya sonando, NO interrumpir (lo termina).
            if self._current_response_task and not self._current_response_task.done():
                if not self._is_speaking:
                    self._current_response_task.cancel()
        buf.extend(pcm)
        self._authors[user_id] = author

        # (Re)arma el timer de silencio para CERRAR el turno cuando deje de hablar
        existing = self._silence_tasks.get(user_id)
        if existing and not existing.done():
            existing.cancel()
        self._silence_tasks[user_id] = asyncio.create_task(
            self._silence_timer(user_id)
        )

        # Force-flush si el turno se hizo demasiado largo
        started = self._buffer_started_at.get(user_id, 0)
        if time.monotonic() - started >= settings.VOICE_MAX_TURN_SECONDS:
            print(f"[VOZ] turno largo de {author} → flush forzado")
            await self._flush_user(user_id)

    async def _silence_timer(self, user_id: int) -> None:
        try:
            await asyncio.sleep(settings.VOICE_SILENCE_MS / 1000)
        except asyncio.CancelledError:
            return
        # Importante: lanzamos _flush_user como TASK INDEPENDIENTE.
        # Si lo hiciéramos `await self._flush_user(user_id)` directo, el cancel
        # del próximo turno (que cancela este _silence_timer) MATARÍA también
        # el STT en curso. Por eso lo despachamos como task separada.
        asyncio.create_task(self._flush_user(user_id))

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
        duration_ms = (len(pcm_bytes) / (PCM_SAMPLE_RATE * PCM_SAMPLE_WIDTH * PCM_CHANNELS)) * 1000
        print(f"[VOZ] flush {author}: {len(pcm_bytes)}B (~{int(duration_ms)}ms) → STT")

        try:
            wav = _pcm_to_wav_mono16k(pcm_bytes)
            print(f"[VOZ][STT] enviando WAV de {len(wav)}B a ElevenLabs Scribe...")
            stt_result = await elevenlabs_client.stt(wav, filename="voice.wav")
            print(f"[VOZ][STT] respuesta: {stt_result!r}")
        except Exception as e:
            import traceback
            print(f"[VOZ][STT] error: {type(e).__name__}: {e}")
            traceback.print_exc()
            return

        transcript = (stt_result.get("text") or "").strip() if isinstance(stt_result, dict) else ""
        events = stt_result.get("audio_events", []) if isinstance(stt_result, dict) else []

        if not transcript:
            print(f"[VOZ][STT] transcript VACÍO (events={events}) — Scribe no detectó habla en el audio")
            if events:
                evt_str = " ".join(f"[{e}]" for e in events)
                await self._handle_transcript(author, evt_str, events=events)
            return
        if len(transcript) < settings.VOICE_MIN_TURN_CHARS:
            print(f"[VOZ][STT] transcript demasiado corto ({len(transcript)}c): {transcript!r}")
            return

        # Enriquecemos el transcript con los eventos detectados al final
        if events:
            transcript = f"{transcript}  ({', '.join(events)})"

        await self._handle_transcript(author, transcript, events=events)

    def _cancel_in_flight(self) -> None:
        """Cancela cualquier procesamiento de turno anterior en curso."""
        for task_attr in ("_current_response_task", "_current_stt_task"):
            t = getattr(self, task_attr, None)
            if t and not t.done():
                t.cancel()

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

        # Lanzamos como task para que sea cancelable cuando llegue audio nuevo
        if self._current_response_task and not self._current_response_task.done():
            self._current_response_task.cancel()
        self._current_response_task = asyncio.create_task(self._respond(text))

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
        try:
            t0 = time.monotonic()
            # Construir mensajes para Claude (historial texto + voz)
            claude_messages = await self._build_claude_messages()

            # Memoria a largo plazo: para voz por defecto NO la cargamos (latencia)
            mem_max = settings.VOICE_MEMORY_MAX_CHARS
            if mem_max == 0:
                memory = ""
            elif mem_max < 0:
                memory = context_manager.load_recent_memory(
                    days=settings.MEMORY_DAYS,
                    max_chars=settings.MEMORY_MAX_CHARS,
                )
            else:
                memory = context_manager.load_recent_memory(
                    days=settings.MEMORY_DAYS,
                    max_chars=mem_max,
                )

            response = await claude_client.generate_voice_response(
                messages=claude_messages,
                memory_text=memory,
            )
            t_claude = time.monotonic() - t0
            print(f"[VOZ] Claude respondió en {t_claude:.1f}s: {response[:80]!r}")

            if not response:
                return

            # Separar parte voz vs parte chat ([CHAT: ...] / [SOLO_CHAT: ...])
            voice_part, chat_parts = _split_voice_and_chat(response)
            if chat_parts:
                print(f"[VOZ] Lain quiere mandar al chat: {chat_parts}")

            # Guardar respuesta completa en .md diario
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

            # 1) Mandar al chat de texto si Claude lo pidió
            for chat_text in chat_parts:
                if not chat_text:
                    continue
                try:
                    # Discord limita a 2000 chars; chunkear si hace falta
                    if len(chat_text) <= 2000:
                        await self.text_channel.send(chat_text)
                    else:
                        for i in range(0, len(chat_text), 1900):
                            await self.text_channel.send(chat_text[i:i + 1900])
                except Exception as e:
                    print(f"[VOZ] no pude mandar al chat: {e}")

            # 2) Hablar por voz (si hay parte voz)
            if voice_part:
                t1 = time.monotonic()
                await self.speak(voice_part)
                t_tts = time.monotonic() - t1
                print(f"[VOZ] TTS+play tardó {t_tts:.1f}s (total: {(time.monotonic()-t0):.1f}s)")
            else:
                print(f"[VOZ] sin parte voz (todo fue al chat)")

            self._last_response_at = time.monotonic()

        except asyncio.CancelledError:
            print(f"[VOZ] respuesta cancelada (usuario habló cosa nueva)")
            raise
        except Exception as e:
            print(f"[VOZ] error generando/respondiendo: {e}")

    async def _build_claude_messages(self) -> list[dict]:
        """Construye mensajes Claude: historial del CANAL DE TEXTO + transcripts de voz.

        Antes solo usaba recent_transcripts de voz → Lain perdía toda la
        conversación que había en el chat de texto. Ahora cargamos el historial
        del canal de texto ligado y le agregamos los turnos de voz al final.
        """
        msgs: list[dict] = []

        # 1) Historial del canal de TEXTO (lo mismo que hace bot.on_message)
        try:
            text_history = await context_manager.get_channel_history(self.text_channel)
            for m in text_history:
                if m.get("is_bot"):
                    content = m["content"]
                    # Quitar prefijos del bot si existen
                    for prefix in ("Lain-bot#0013:", "Lain-bot:", "Lain:"):
                        if content.startswith(prefix):
                            content = content[len(prefix):].lstrip()
                            break
                    msgs.append({"role": "assistant", "content": content})
                else:
                    msgs.append({
                        "role": "user",
                        "content": f"{m['author']}: {m['content']}",
                    })
        except Exception as e:
            print(f"[VOZ] no pude cargar historial del canal de texto: {e}")

        # 2) Turnos recientes de voz (los más nuevos al final)
        for t in self.recent_transcripts:
            content = t["text"]
            if t.get("is_bot"):
                msgs.append({"role": "assistant", "content": content})
            else:
                msgs.append({
                    "role": "user",
                    "content": f"[VOZ] {t['author']}: {content}",
                })

        # Limpieza: Claude requiere alternancia user/assistant. Colapsamos consecutivos.
        msgs = _coalesce_messages(msgs)

        # Asegurar que el último sea user (sino Claude no responde)
        if not msgs or msgs[-1]["role"] != "user":
            msgs.append({"role": "user", "content": "(continúa la conversación)"})
        return msgs

    # --- TTS / playback ---

    async def speak(self, text: str) -> None:
        """Genera TTS y lo reproduce en el VC. Bloquea hasta terminar."""
        if not self.voice_client or not self.voice_client.is_connected():
            return

        # Filtrado defensivo: si el TTS no es v3, sacar audio tags entre corchetes
        # ([laughs], [whispers], etc.) para que el modelo no los lea literal.
        if settings.ELEVENLABS_TTS_MODEL != "eleven_v3":
            import re
            cleaned = re.sub(r"\[[a-zA-Z][a-zA-Z\s]*\]", "", text)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned != text:
                print(f"[VOZ][TTS] audio tags filtrados: {text!r} → {cleaned!r}")
            text = cleaned
            if not text:
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

        # Marca anti-eco: ignorar audio entrante mientras Lain habla
        self._is_speaking = True
        try:
            self.voice_client.play(source, after=_after)
            await done.wait()
        finally:
            self._is_speaking = False
            self._speak_finished_at = time.monotonic()

    # --- Idle auto-leave ---

    async def _idle_watchdog(self) -> None:
        """Si pasan VOICE_IDLE_TIMEOUT_SECONDS sin recibir audio, Lain se va sola."""
        timeout = settings.VOICE_IDLE_TIMEOUT_SECONDS
        check_every = max(5, settings.VOICE_IDLE_CHECK_SECONDS)
        try:
            while not self._closed:
                await asyncio.sleep(check_every)
                if self._closed:
                    return
                idle_for = time.monotonic() - self._last_audio_at
                if idle_for >= timeout:
                    print(f"[VOZ] sin audio por {int(idle_for)}s — me piro del VC")
                    # Avisar en el chat de texto antes de irse
                    try:
                        await self.text_channel.send(
                            f"Me re aburrí, no escuché nada en {int(idle_for/60)} min. "
                            "Me piro del canal de voz, llamame con `!join` cuando quieras. 👋"
                        )
                    except Exception as e:
                        print(f"[VOZ] no pude avisar idle leave: {e}")
                    # Disparar leave SIN bloquear este task (el close() nos cancela)
                    guild_id = self.voice_channel.guild.id
                    asyncio.create_task(voice_manager.leave(guild_id))
                    return
        except asyncio.CancelledError:
            return

    # --- Lifecycle ---

    async def close(self) -> None:
        self._closed = True
        # Cancelar watchdog de inactividad
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
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
