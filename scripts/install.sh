#!/usr/bin/env bash
# Uso pelo usuário: bash scripts/install.sh [auto|text|endpoint|local|esp32]
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd -- "$PROJECT_DIR"
PROFILE="${1:-auto}"
case "$PROFILE" in
    auto|text|endpoint|local|esp32) ;;
    *) echo "Perfil inválido." >&2; exit 1 ;;
esac
PYTHON_CMD="${JUVENAL_PYTHON:-python3}"
if [[ ! -d "$PROJECT_DIR/.venv" ]]; then
    "$PYTHON_CMD" -m venv "$PROJECT_DIR/.venv"
fi
PYTHON="$PROJECT_DIR/.venv/bin/python"
"$PYTHON" -m pip install -r requirements-core.txt
if [[ ! -f .env ]]; then
    # Template, não configuração pronta. Nunca imprimir nem sobrescrever segredos.
    (umask 077; cp -- .env.example .env)
    echo ".env criado a partir do exemplo. Configure-o antes de iniciar."
fi
MANIFESTS="$("$PYTHON" scripts/install_profile.py "$PROFILE")"
while IFS= read -r manifest; do
    [[ -z "$manifest" ]] && continue
    "$PYTHON" -m pip install -r "$manifest"
done <<< "$MANIFESTS"
echo "Dependências instaladas. Perfil não altera .env, modelos ou prompts existentes."
echo "Confira a configuração; início manual: bash scripts/start.sh"
# Não instalar pacotes do SO, descarregar modelos, iniciar aplicação ou executar testes.
