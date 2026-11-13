"""Texto de diagnóstico em uma linha, com limite e ocultação de segredos conhecidos."""

import json
import os
import re


def log_text(value, limit=4096):
    text = str(value)
    for name, secret in os.environ.items():
        if secret and (any(part in name.upper() for part in
                           ("API_KEY", "PASSWORD", "TOKEN", "SECRET"))
                       or name.upper().endswith("_URL")):
            text = text.replace(secret, "[oculto]")
    # Não interpretar JSON ou executar conteúdo do retorno: só ocultar campos sensíveis.
    text = re.sub(
        r'''(?i)((?:api[_-]?key|password|secret|authorization|access_token|token)["']?\s*[:=]\s*)("[^"\r\n]*(?:"|$)|'[^'\r\n]*(?:'|$)|[^\s,;}]+)''',
        r"\1[oculto]", text)
    truncated = len(text) > limit
    text = text[:limit]
    # Escapar quebras/controles evita que o retorno crie entradas falsas no terminal.
    return json.dumps(text, ensure_ascii=False) + (" [truncado]" if truncated else "")
