"""Aviso de versao nova do sistema no Terminal Server.

O exe oficial fica numa pasta compartilhada; o pacote traz um `versao.txt`
com o numero da versao (gerado pelo build). Ao abrir, o sistema le esse
arquivo e avisa se a versao publicada for mais nova que a do exe em uso.
Qualquer falha de leitura e silenciosa: sem rede nao pode atrapalhar o uso.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app import __version__
from app.config import UPDATE_LAUNCHER_FILE_NAME, UPDATE_VERSION_FILE_NAME
from app.runtime import data_directory
from app.settings import load_update_directory

_VERSION_PATTERN = re.compile(r"^\s*v?(\d+(?:\.\d+){1,3})\s*$")


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    published: str
    directory: Path

    @property
    def available(self):
        return parse_version(self.published) > parse_version(self.current)


def parse_version(text):
    """'2.6.10' -> (2, 6, 10), comparavel numericamente. Invalido -> ()."""
    match = _VERSION_PATTERN.match(str(text or ""))
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def read_published_version(directory=None):
    """Versao publicada na pasta oficial, ou None se nao der para ler."""
    directory = Path(directory or load_update_directory())
    try:
        content = (directory / UPDATE_VERSION_FILE_NAME).read_text(encoding="utf-8-sig")
    except (OSError, ValueError):
        return None
    first_line = content.strip().splitlines()[0] if content.strip() else ""
    return first_line.strip().lstrip("vV") if parse_version(first_line) else None


def check_for_update(directory=None, current=None):
    """UpdateInfo se houver versao publicada legivel; None se nao der para saber."""
    directory = Path(directory or load_update_directory())
    published = read_published_version(directory)
    if published is None:
        return None
    return UpdateInfo(current=current or __version__, published=published, directory=directory)


def launcher_path(directory):
    return Path(directory) / UPDATE_LAUNCHER_FILE_NAME


def start_update(directory):
    """Dispara o iniciador da pasta oficial (copia a versao nova e a abre).

    O chamador fecha o sistema logo em seguida. Levanta FileNotFoundError se a
    pasta publicada nao tiver o iniciador e OSError se nao der para executa-lo.
    """
    launcher = launcher_path(directory)
    if not launcher.exists():
        raise FileNotFoundError(str(launcher))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    # pasta local como diretorio atual: o cmd nao aceita caminho de rede
    subprocess.Popen(
        ["cmd.exe", "/c", str(launcher)],
        cwd=str(data_directory()) if data_directory().exists() else None,
        creationflags=flags,
        close_fds=True,
    )
