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
| `!lain` | Info del bot |
| `!helpbot` | Ayuda |

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
| `ANTHROPIC_MODEL` | No | claude-sonnet-4-5 | Modelo de Claude |
| `MAX_TOKENS` | No | 4096 | Max tokens por respuesta |
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
