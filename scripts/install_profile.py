"""Seleciona dependências sem importar/iniciar a aplicação nem expor o .env."""

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

root = Path(__file__).resolve().parent.parent
profile = sys.argv[1] if len(sys.argv) > 1 else "auto"
env_file = Path(os.environ.get("JUVENAL_ENV_FILE", str(root / ".env"))).expanduser()
if not env_file.is_absolute():
    env_file = root / env_file
values = {**dotenv_values(env_file), **os.environ}
enabled = lambda key, default: str(values.get(key, default)).lower() in {"true", "1", "yes"}
if profile == "auto":
    mode = str(values.get("AUDIO_OUTPUT_MODE", "") or "local").lower()
    asr = enabled("STT_ENABLED", "true")
    local_input = enabled("LOCAL_VOICE_ENABLED", "true") and asr
    local_output = mode == "local"
    tts = mode == "esp32"
else:
    if profile not in {"text", "endpoint", "local", "esp32"}:
        raise SystemExit("Perfil inválido: auto, text, endpoint, local ou esp32")
    asr = profile != "text"
    local_input = profile == "local"
    local_output = profile == "local"
    tts = profile == "esp32"
backend = str(values.get("ASR_BACKEND", "whisper")).strip().lower()
if asr and backend not in {"whisper", "parakeet"}:
    raise SystemExit("ASR_BACKEND: use whisper ou parakeet")
asr_manifest = "requirements-asr-parakeet.txt" if backend == "parakeet" else "requirements-asr.txt"
for needed, filename in [(asr, asr_manifest),
                         (local_input, "requirements-local-input.txt"),
                         (tts, "requirements-tts.txt"),
                         (local_output, "requirements-local-output.txt"),
                         (enabled("INTERNET_ENABLED", "false") and enabled("WEB_SEARCH_ENABLED", "true"),
                          "requirements-internet.txt")]:
    if needed:
        print(filename)
