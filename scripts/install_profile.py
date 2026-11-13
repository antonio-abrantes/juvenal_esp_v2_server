"""Seleciona dependências sem importar/iniciar a aplicação nem expor o .env."""

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

root = Path(__file__).resolve().parent.parent
profile = sys.argv[1] if len(sys.argv) > 1 else "auto"
values = {**dotenv_values(root / ".env"), **os.environ}
enabled = lambda key, default: str(values.get(key, default)).lower() in {"true", "1", "yes"}
if profile == "auto":
    mode = str(values.get("AUDIO_OUTPUT_MODE", "") or "local").lower()
    asr = enabled("STT_ENABLED", "true")
    local_input = enabled("LOCAL_VOICE_ENABLED", "true") and asr
    local_output = mode == "local"
else:
    if profile not in {"text", "endpoint", "local", "esp32"}:
        raise SystemExit("Perfil inválido: auto, text, endpoint, local ou esp32")
    asr = profile != "text"
    local_input = profile == "local"
    local_output = profile == "local"
for needed, filename in [(asr, "requirements-asr.txt"),
                         (local_input, "requirements-local-input.txt"),
                         (local_output, "requirements-local-output.txt")]:
    if needed:
        print(filename)
