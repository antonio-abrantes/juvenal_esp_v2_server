# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/juvenal \
    TZ=America/Sao_Paulo \
    JUVENAL_HOST=0.0.0.0 \
    JUVENAL_PORT=8000 \
    JUVENAL_ENV_FILE=/config/juvenal.env \
    JUVENAL_DATA_DIR=/data \
    SYSTEM_PROMPT_PATH=/config/system_prompt.md \
    XDG_CACHE_HOME=/cache \
    HF_HOME=/cache/huggingface \
    LOCAL_VOICE_ENABLED=false \
    AUDIO_OUTPUT_MODE=esp32 \
    STT_ENABLED=true \
    TTS_BACKEND=piper \
    WHISPER_DEVICE=cpu \
    WHISPER_COMPUTE_TYPE=int8

WORKDIR /app

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       ca-certificates libgomp1 libstdc++6 tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 juvenal \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin juvenal \
    && mkdir -p /config /data /cache \
    && chown 10001:10001 /config /data /cache

COPY requirements-core.txt requirements-asr.txt requirements-tts.txt ./
RUN python -m pip install --no-cache-dir --prefer-binary \
    -r requirements-core.txt -r requirements-asr.txt -r requirements-tts.txt

COPY server/ ./server/
COPY frontend/ ./frontend/
COPY configs/system_prompt.example.md ./configs/system_prompt.example.md
COPY --chown=10001:10001 configs/system_prompt.example.md /config/system_prompt.md
COPY docker/entrypoint.py docker/healthcheck.py /usr/local/lib/juvenal/

USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s --retries=5 \
    CMD ["python", "/usr/local/lib/juvenal/healthcheck.py"]
ENTRYPOINT ["python", "/usr/local/lib/juvenal/entrypoint.py"]
CMD ["python", "/app/server/main.py"]
