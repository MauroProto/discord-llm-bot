# Lain - Bot de Discord con Claude AI

Bot personalidad "Lain" para grupos privados de Discord. Lee el historial del chat, guarda contexto en archivos `.md`, y responde usando la API de Claude con personalidad divertida, directa y sin filtros. Ideal para preparar hackathons en grupos de 3 personas.

## Caracteristicas

- **Responde a @Lain** — Mencionala y te responde con todo el contexto del chat
- **Guarda contexto en .md** — Organizado por fecha (`contexts/YYYY-MM-DD.md`) y threads
- **Busqueda web** — Fallback con Tavily/SerpAPI/DuckDuckGo cuando Claude necesita info actualizada
- **Personalidad propia** — Divertida, energetica, critica con sentido, directa, sin filtros
- **100% privado** — Solo funciona en el servidor y canal que configures
- **Comandos utiles** — Resumen, busqueda manual, info del bot, contexto guardado
- **Respuestas espontaneas** (opt-in) — Puede intervenir sola cuando detecta ideas de hackathon

## Comandos

| Comando | Descripcion |
|---------|-------------|
| `@Lain <mensaje>` | Habla con Lain, lee historial y responde con contexto |
| `!resumen [N]` | Resumen de los ultimos N mensajes (default 50) |
| `!contexto` | Manda el archivo .md de hoy con todo el contexto |
| `!buscar <query>` | Busqueda web manual |
| `!join` | Lain se mete a tu canal de voz (alias: `entra`, `vozon`) |
| `!leave` | Lain sale del canal de voz (alias: `sali`, `vozoff`, `chau`) |
| `!sayvoz <texto>` | Forzar TTS en el VC actual |
| `!lain` | Info del bot |
| `!helpbot` | Ayuda |

También funciona en lenguaje natural mencionándola: `@Lain metete al canal de voz` / `@Lain andate del canal`.

## Voz (ElevenLabs TTS + STT)

Lain puede unirse a un canal de voz, **escuchar** lo que se dice (Scribe STT), **hablar** por TTS de ElevenLabs, y **mantener un único contexto unificado** entre lo que pasa en el chat de texto y lo que se dice por voz: todo se guarda en el mismo `data/contexts/YYYY-MM-DD.md` con marca `#VOZ:<canal>`, y la memoria de los últimos días sigue funcionando igual.

### Setup

1. Crear cuenta en [ElevenLabs](https://elevenlabs.io) y sacar API key en Settings → API Keys.
2. Elegir una voz en la [Voice Library](https://elevenlabs.io/app/voice-library) y copiar su `voice_id`.
3. Agregar al `.env`:
   ```bash
   VOICE_ENABLED=true
   ELEVENLABS_API_KEY=sk_...
   ELEVENLABS_VOICE_ID=AwmgI32PB22lsT7wnBFH
   ELEVENLABS_TTS_MODEL=eleven_turbo_v2_5
   VOICE_LANGUAGE=spa
   ```
4. Re-invitar al bot con permisos extra de Discord: `Connect`, `Speak`, `Use Voice Activity` (Voice Channel Permissions).
5. Si corrés local, instalá `ffmpeg`:
   - macOS: `brew install ffmpeg opus`
   - Linux: `apt install ffmpeg libopus0`
   - Docker: ya está incluido en el `Dockerfile`.

### Modelos TTS (latencia vs calidad)

| Modelo | Latencia | Calidad | Notas |
|--------|----------|---------|-------|
| `eleven_v3` | ~1-2s | Máxima expresividad | **Default actual.** El más natural y emocional |
| `eleven_turbo_v2_5` | ~300ms | Alta | Mejor opción si la latencia molesta |
| `eleven_flash_v2_5` | ~75ms | Media | Para charla ultra-rápida, calidad menor |
| `eleven_multilingual_v2` | ~1-2s | Alta | Para grabaciones |

### Cómo funciona

- `!join` (con vos en un VC) → Lain se conecta y empieza a escuchar.
- Lain transcribe cada turno de voz con ElevenLabs Scribe y lo guarda en el .md diario.
- Si `VOICE_ALWAYS_RESPOND=true` (default) participa de toda la charla. Si lo ponés en false, solo responde cuando alguien dice "Lain" (o `VOICE_WAKE_WORDS`).
- Genera respuestas con Claude usando el contexto unificado (texto + voz + memoria de 14 días) y las dice por TTS.
- `!leave` para que se vaya. Si todos se van del VC, se desconecta sola.

### Tuning útil

- `VOICE_SILENCE_MS=800` → cuántos ms de silencio cierran un turno.
- `VOICE_COOLDOWN_MS=1500` → mínimo entre dos respuestas seguidas (evita atropellarse).
- `VOICE_MIN_TURN_CHARS=3` → ignora ruido tipo "eh", "ah".
- `VOICE_MAX_RESPONSE_CHARS=600` → trunca respuestas TTS para que no eternice.
- `VOICE_MIRROR_TEXT=true` → si está en VC, dice también por voz lo que responde en el chat de texto.

## Setup rapido (Docker)

1. Copiar `.env.example` a `.env` y completar las credenciales
2. `docker compose up --build`

## Deploy en Railway (gratis, one-click)

1. Crear cuenta en [Railway](https://railway.app) (logueate con Google/GitHub)
2. New Project → Deploy from Repo (o Deploy from Dockerfile)
3. Subi el codigo (o conecta tu repo de GitHub)
4. En "Variables", agrega las env vars del `.env.example`
5. Clickea "Deploy"

Railway te da $5/mes de credito gratis, alcanza para el bot.

## Crear el bot en Discord

1. Anda a [Discord Developer Portal](https://discord.com/developers/applications)
2. New Application → dale nombre
3. Sidebar: **Bot** → "Add Bot"
4. Copia el **Token** (Reset Token si no existe)
5. Activa **Message Content Intent**, **Server Members Intent**, **Presence Intent**
6. Guarda cambios
7. Sidebar: **OAuth2** → **URL Generator**
   - Scopes: tilda `bot`
   - Bot Permissions: Send Messages, Read Message History, View Channels, Embed Links
8. Copia la URL, pegala en el navegador, elegi tu servidor

## Sacar IDs de Discord

- **Server ID**: clic derecho en el servidor (arriba a la izquierda) → Copiar ID del servidor
- **Channel ID**: clic derecho en el canal → Copiar ID del canal
- (Si no aparece "Copiar ID", activa Modo Desarrollador en Discord: Configuracion → Avanzado)

## Requisitos de permisos del bot

| Permiso | Por que |
|---------|---------|
| Send Messages | Para responder |
| Read Message History | Para leer el contexto del chat |
| View Channels | Para ver los canales |
| Embed Links | Para compartir links |
| Message Content Intent | Para leer el contenido de los mensajes |
| Connect (voz) | Para unirse al canal de voz |
| Speak (voz) | Para hablar por TTS en el VC |
| Use Voice Activity (voz) | Para detectar audio de los humanos |

## Estructura del proyecto

```
.
├── bot.py              # Core del bot, event handlers, comandos
├── claude_client.py    # Wrapper de Anthropic API
├── context_manager.py  # Lectura/escritura de historial en .md
├── search_client.py    # Busqueda web (Tavily/SerpAPI/DuckDuckGo)
├── config.py           # Configuracion con pydantic-settings
├── Dockerfile          # Multi-stage build
├── docker-compose.yml  # Compose config
├── railway.json        # One-click deploy Railway
├── requirements.txt    # Dependencias
├── .env.example        # Variables de entorno de ejemplo
└── README.md           # Este archivo
```

## Personalidad de Lain

Lain es una asistente AI con personalidad propia:
- **Divertida y alegre**: tiene onda, usa humor, se rie de las burradas
- **Critica con sentido**: evalua ideas honestamente, puede decir "es una cagada" o "es brillante"
- **Directa y precisa**: dice las cosas como son, sin filtros, pero con carino
- **Espontanea**: puede intervenir cuando detecta ideas de hackathon (configurable)
- **Usa español rioplatense**: che, boludo/a, capo, etc.

Para cambiar la personalidad, setea `SYSTEM_PROMPT` en el `.env`.

## Variables de entorno

| Variable | Requerida | Default | Descripcion |
|----------|-----------|---------|-------------|
| `DISCORD_BOT_TOKEN` | Si | - | Token del bot de Discord |
| `ANTHROPIC_API_KEY` | Si | - | API key de Anthropic |
| `ALLOWED_GUILD_ID` | Si | - | Solo responde en este servidor |
| `ALLOWED_CHANNEL_ID` | No | - | Solo responde en este canal (opcional) |
| `ANTHROPIC_MODEL` | No | claude-opus-4-7 | Modelo de Claude |
| `ENABLE_1M_CONTEXT` | No | true | Activa ventana de 1M tokens (beta) |
| `EXTENDED_THINKING` | No | true | Activa razonamiento extendido |
| `THINKING_BUDGET_TOKENS` | No | 24000 | Tokens reservados para pensar |
| `MAX_TOKENS` | No | 32000 | Max tokens por respuesta (output + thinking) |
| `HISTORY_LIMIT` | No | 100 | Mensajes de historial a leer |
| `TAVILY_API_KEY` | No | - | API key de Tavily (fallback busqueda) |
| `SERPAPI_KEY` | No | - | API key de SerpAPI (fallback busqueda) |
| `SPONTANEOUS_RESPONSE` | No | false | Responder sin ser mencionada |
| `SPONTANEOUS_PROBABILITY` | No | 0.3 | Probabilidad de respuesta espontanea |
| `DATA_DIR` | No | ./data | Directorio para archivos .md |

## Hosting alternativos (gratis)

| Servicio | Tier gratis | Notas |
|----------|-------------|-------|
| Railway | $5/mes | Recomendado, facil |
| Fly.io | $5/mes | Buena alternativa |
| Render | Gratis con limitaciones | El bot "duerme" despues de 15 min sin uso |

## Licencia

MIT - Hace lo que quieras.
