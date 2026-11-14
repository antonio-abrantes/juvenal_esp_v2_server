"""Gera uma unit para os caminhos reais informados; não instala nem inicia serviços."""

import argparse
import os
from pathlib import Path
import re
import sys


def quote(value):
    value = str(value)
    if any(char in value for char in "\r\n\0"):
        raise ValueError("Caminho inválido")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def existing(value, directory=False):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Informe caminhos absolutos")
    path = path.resolve(strict=True)
    if directory and not path.is_dir() or not directory and not path.is_file():
        raise ValueError("Tipo de caminho incorreto")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Gere a unit no host Linux real, após conferir os caminhos e usuário.")
    import pwd
    try:
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", args.user) or pwd.getpwnam(args.user).pw_uid == 0:
            raise ValueError("Use uma conta existente sem privilégios de root")
        project = existing(args.project_dir, directory=True)
        env_file = existing(args.env_file)
        data = existing(args.data_dir, directory=True)
        cache = existing(args.cache_dir, directory=True)
        python = project / ".venv/bin/python"
        existing(str(python))  # Validar o destino sem perder o caminho que ativa o venv.
        application = existing(str(project / "server/main.py"))
        if not os.access(python, os.X_OK):
            raise ValueError("Python do venv não é executável")
        output = Path(args.output).expanduser().absolute()
        if output in {env_file, application, python} or not output.name.endswith((".service", ".service.local")):
            raise ValueError("Destino deve ser um arquivo .service ou .service.local separado")
        if output.exists() and not args.replace:
            raise ValueError("Arquivo de saída já existe; revise antes de usar --replace")
        template = (Path(__file__).resolve().parent.parent /
                    "deploy/juvenal-server.service.in").read_text(encoding="utf-8")
        replacements = {
            "USER": args.user, "PROJECT": quote(project), "PYTHON": quote(python),
            "MAIN": quote(application), "ENV_FILE": quote("JUVENAL_ENV_FILE=" + str(env_file)),
            "DATA_ENV": quote("JUVENAL_DATA_DIR=" + str(data)),
            "CACHE_ENV": quote("XDG_CACHE_HOME=" + str(cache)),
            "DATA": quote(data), "CACHE": quote(cache)}
        for key, value in replacements.items():
            template = template.replace("@" + key + "@", value)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w" if args.replace else "x", encoding="utf-8") as handle:
            handle.write(template)
        print("Unit gerada para revisão:", output)
        print("Nenhum serviço instalado, habilitado ou iniciado. Confira permissões e catálogo antes de instalar.")
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
