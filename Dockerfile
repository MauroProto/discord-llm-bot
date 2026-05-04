FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (incluye ffmpeg/libopus/libsodium para voz)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    ffmpeg \
    libopus0 \
    libsodium23 \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies (--pre por discord-ext-voice-recv)
COPY requirements.txt .
RUN pip install --no-cache-dir --pre -r requirements.txt

# Copy source code
COPY *.py .

# Create data directory
ENV DATA_DIR=/app/data
RUN mkdir -p /app/data

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "print('alive')" || exit 1

CMD ["python", "bot.py"]
