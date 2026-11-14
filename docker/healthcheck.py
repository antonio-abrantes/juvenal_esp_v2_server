"""Sonda interna sem acionar ferramentas, LLM ou reprodução de áudio."""

import json
import os
import sys
from urllib.request import urlopen


def enabled(name, default="false"):
    return os.getenv(name, default).strip().lower() in {"true", "1", "yes"}


def main():
    try:
        port = int(os.getenv("JUVENAL_PORT", "8000"))
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            return 1
        info = json.loads(raw)
        if info.get("status") != "ok":
            return 1
        if enabled("STT_ENABLED", "true") and not info.get("asr"):
            return 1
        ha_voice = (enabled("HASS_ENABLED", "true")
                    and os.getenv("HASS_MODE", "api") == "api"
                    and all(os.getenv(key) for key in
                            ("HASS_API_ENDPOINT", "HASS_API_TOKEN", "ESP_DEVICE_TOKEN")))
        if (info.get("output_mode") == "esp32" or ha_voice) and not info.get("tts"):
            return 1
        return 0
    except Exception:
        # Não imprimir exceções que possam incluir detalhes da configuração.
        return 1


if __name__ == "__main__":
    sys.exit(main())
