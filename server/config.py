"""Configuração única, carregada antes dos módulos que usam o ambiente."""

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)


def flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name}: use true ou false")
    return value in {"true", "1", "yes"}


def number(name: str, default: float, maximum: float, minimum: float = 0.001) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        raise ValueError(f"{name}: número inválido") from None
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name}: valor fora dos limites")
    return value


def integer(name: str, default: int, maximum: int, minimum: int = 1) -> int:
    value = number(name, default, maximum, minimum)
    if value != int(value):
        raise ValueError(f"{name}: inteiro obrigatório")
    return int(value)


def local_device(name: str):
    value = os.getenv(name, "").strip()
    return int(value) if value.isdigit() else None


def whisper_options():
    return {"model_size": os.getenv("WHISPER_MODEL_SIZE", "base"),
            "language": os.getenv("WHISPER_LANGUAGE", "pt"),
            "device": os.getenv("WHISPER_DEVICE", "cpu"),
            "compute_type": os.getenv("WHISPER_COMPUTE_TYPE", "int8")}


def tts_options():
    return {"backend": os.getenv("TTS_BACKEND", "piper"),
            "model_name": os.getenv("PIPER_MODEL", "pt_PT-tugao-medium"),
            "models_dir": str(PROJECT_ROOT / "models"),
            "length_scale": number("PIPER_LENGTH_SCALE", 1, 10),
            "volume": number("PIPER_VOLUME", 1, 10, 0),
            "use_cuda": flag("PIPER_USE_CUDA", False),
            "output_device": local_device("AUDIO_OUTPUT_DEVICE")}


def project_path(value: str) -> Path:
    path = Path(value).expanduser()
    # Caminho legado ../models era relativo a server/, não à raiz do projeto.
    if value.replace("\\", "/").startswith("../models/"):
        path = Path(value.replace("\\", "/")[3:])
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def validate_url(value: str, name: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                 and not parsed.username and not parsed.password and not parsed.fragment
                 and not any(ord(c) <= 32 for c in value))
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"{name}: URL HTTP(S) inválida")
    return value


@dataclass(frozen=True)
class Settings:
    output_mode: str
    legacy_endpoint: bool
    endpoint_url: str
    endpoint_format: str
    endpoint_key: str
    endpoint_auth_header: str
    endpoint_auth_prefix: str
    endpoint_timeout: float
    endpoint_statuses: tuple[int, ...]
    endpoint_log_response: bool
    log_transcripts: bool
    local_voice_enabled: bool
    stt_enabled: bool
    data_dir: Path
    prompt_path: Path
    wake_model: Path
    history_messages: int
    history_bytes: int
    context_ttl: float
    max_contexts: int
    text_bytes: int
    token_queue_bytes: int
    llm_timeout: float
    interaction_timeout: float
    delivery_timeout: float
    conversation_timeout: float
    esp_silence_timeout: float
    idle_enabled: bool
    idle_message: str
    panel_username: str
    panel_password: str
    esp_token: str
    esp_device_id: str
    host: str
    port: int

    @classmethod
    def from_environment(cls):
        mode = os.getenv("AUDIO_OUTPUT_MODE", "").strip().lower()
        legacy_url = os.getenv("TTS_HA_WEBHOOK_URL", "").strip()
        legacy = not mode and bool(legacy_url)
        mode = mode or "local"
        if mode not in {"local", "endpoint", "esp32"}:
            raise ValueError("AUDIO_OUTPUT_MODE: use local, endpoint ou esp32")
        url = os.getenv("AUDIO_TEXT_ENDPOINT_URL", "").strip()
        if legacy:
            url = legacy_url
        endpoint_active = mode == "endpoint" or legacy
        if endpoint_active:
            validate_url(url, "AUDIO_TEXT_ENDPOINT_URL")
        fmt = os.getenv("AUDIO_TEXT_ENDPOINT_FORMAT", "json").strip().lower() if endpoint_active else "json"
        if fmt not in {"json", "text"}:
            raise ValueError("AUDIO_TEXT_ENDPOINT_FORMAT: use json ou text")
        key = os.getenv("AUDIO_TEXT_ENDPOINT_API_KEY", "") if endpoint_active else ""
        header = os.getenv("AUDIO_TEXT_ENDPOINT_AUTH_HEADER", "").strip() if endpoint_active else ""
        prefix = os.getenv("AUDIO_TEXT_ENDPOINT_AUTH_PREFIX", "") if endpoint_active else ""
        if (header and not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", header)
                or any(c in key + prefix for c in "\r\n")):
            raise ValueError("Autenticação do endpoint inválida")
        if key and not header:
            raise ValueError("Defina AUDIO_TEXT_ENDPOINT_AUTH_HEADER ao fornecer API Key")
        if header.lower() in {"content-type", "content-length", "host", "transfer-encoding"}:
            raise ValueError("Header reservado não pode autenticar o endpoint")
        try:
            statuses = tuple(int(s.strip()) for s in (os.getenv(
                "AUDIO_TEXT_ENDPOINT_SUCCESS_STATUSES", "200,201,202")
                if endpoint_active else "200,201,202").split(","))
        except ValueError:
            raise ValueError("AUDIO_TEXT_ENDPOINT_SUCCESS_STATUSES inválido") from None
        if not statuses or any(s < 200 or s >= 300 for s in statuses):
            raise ValueError("Status de aceite do endpoint deve ser 2xx")
        username = os.getenv("PANEL_USERNAME", "")
        password = os.getenv("PANEL_PASSWORD", "")
        if bool(username) != bool(password):
            raise ValueError("PANEL_USERNAME e PANEL_PASSWORD devem ser definidos juntos")
        return cls(
            output_mode=mode, legacy_endpoint=legacy, endpoint_url=url,
            endpoint_format=fmt, endpoint_key=key, endpoint_auth_header=header,
            endpoint_auth_prefix=prefix,
            endpoint_timeout=number("AUDIO_TEXT_ENDPOINT_TIMEOUT_SECONDS", 10, 60) if endpoint_active else 10,
            endpoint_statuses=statuses,
            endpoint_log_response=flag("AUDIO_TEXT_ENDPOINT_LOG_RESPONSE", False),
            log_transcripts=flag("LOG_TRANSCRIPTS", False),
            local_voice_enabled=flag("LOCAL_VOICE_ENABLED", True),
            stt_enabled=flag("STT_ENABLED", True),
            data_dir=project_path(os.getenv("JUVENAL_DATA_DIR", "data")),
            prompt_path=project_path(os.getenv("SYSTEM_PROMPT_PATH", "configs/system_prompt.md")),
            wake_model=project_path(os.getenv("WAKE_WORD_MODEL", "models/juvehnal.tflite")),
            history_messages=integer("CONTEXT_MAX_MESSAGES", 20, 100, 2),
            history_bytes=integer("CONTEXT_MAX_BYTES", 65536, 1048576, 1024),
            context_ttl=number("CONTEXT_TTL_SECONDS", 1800, 86400),
            max_contexts=integer("CONTEXT_MAX_COUNT", 8, 64),
            text_bytes=integer("RESPONSE_MAX_BYTES", 32768, 131072, 256),
            token_queue_bytes=integer("TOKEN_QUEUE_MAX_BYTES", 65536, 262144, 1024),
            llm_timeout=number("LLM_TIMEOUT_SECONDS", 60, 120),
            interaction_timeout=number("INTERACTION_TIMEOUT_SECONDS", 180, 600),
            delivery_timeout=number("DELIVERY_TIMEOUT_SECONDS", 60, 120),
            conversation_timeout=number("CONVERSATION_TIMEOUT", 15, 120),
            esp_silence_timeout=number("ESP_SILENCE_TIMEOUT", 1.8, 10),
            idle_enabled=flag("IDLE_MESSAGE_ENABLED", True),
            idle_message=os.getenv("IDLE_MESSAGE", "Estou voltando ao modo de espera. Me chame quando precisar!"),
            panel_username=username, panel_password=password,
            esp_token=os.getenv("ESP_DEVICE_TOKEN", ""),
            esp_device_id=os.getenv("ESP_DEVICE_ID", "juvenal"),
            host=os.getenv("JUVENAL_HOST", "0.0.0.0"),
            port=integer("JUVENAL_PORT", 8000, 65535),
        )


settings = Settings.from_environment()
