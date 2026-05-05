FROM python:3.11-slim

WORKDIR /app

# System deps: ffmpeg/libopus/libsodium for Discord voice; gcc for any
# C-extension wheels that might need to build from source on this slim base.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    git \
    ffmpeg \
    libopus0 \
    libsodium23 \
    && rm -rf /var/lib/apt/lists/*

# Python deps. --pre is required because discord-ext-voice-recv ships only
# alphas. --no-cache-dir keeps the image small.
COPY requirements.txt .
RUN pip install --no-cache-dir --pre -r requirements.txt

# Source. Copy the root modules AND every Python package directory the bot
# imports — providers/, personalities/, plus their __init__.py + assets.
COPY *.py ./
COPY providers/ ./providers/
COPY personalities/ ./personalities/

# Persistent data directory. Mount a volume here in production.
ENV DATA_DIR=/app/data
RUN mkdir -p /app/data

# Healthcheck — confirms the container can still execute Python. Real
# liveness lives on the Discord side (heartbeats from gateway).
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "print('alive')" || exit 1

CMD ["python", "bot.py"]
