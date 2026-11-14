"""Prepara volumes do container, sem sobrescrever persona ou expor configuração."""

import os
from pathlib import Path
import shutil
import sys


def main():
    os.umask(0o077)
    local = os.getenv("LOCAL_VOICE_ENABLED", "false").strip().lower()
    mode = os.getenv("AUDIO_OUTPUT_MODE", "esp32").strip().lower()
    if local not in {"false", "0", "no"} or mode not in {"esp32", "endpoint"}:
        raise SystemExit("A imagem é headless: LOCAL_VOICE_ENABLED=false; AUDIO_OUTPUT_MODE=esp32 ou endpoint.")
    for folder in ("/config", "/data", "/cache"):
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
            raise SystemExit(f"Volume {folder} precisa permitir acesso ao UID/GID 10001.")
    prompt = Path(os.getenv("SYSTEM_PROMPT_PATH", "/config/system_prompt.md"))
    if not prompt.is_file():
        if prompt != Path("/config/system_prompt.md"):
            raise SystemExit("SYSTEM_PROMPT_PATH não aponta para um arquivo disponível no container.")
        shutil.copyfile("/app/configs/system_prompt.example.md", prompt)
    command = sys.argv[1:] or ["python", "/app/server/main.py"]
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
