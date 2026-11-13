#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd -- "$PROJECT_DIR"
if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    echo "Venv ausente. Execute: bash scripts/install.sh" >&2
    exit 1
fi
# Sem source do .env, ativação interativa ou serviços externos iniciados implicitamente.
exec "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/server/main.py"
