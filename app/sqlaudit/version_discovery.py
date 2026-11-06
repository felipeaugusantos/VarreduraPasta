"""Descobre as pastas de versao com scripts e sugere o banco de cada uma.

Estrutura conhecida (a raiz e a pasta `379.48.2.30`):

    <raiz>\\48.2.30.72\\SCRIPTS                      -> BANCO_AUTCOM_379_48_02_30_72
    <raiz>\\Versoes Descontinuada\\48.2.30.74.150\\SCRIPTS

O nome do banco e so uma SUGESTAO inferida de exemplos reais: o operador
confirma ou corrige antes de qualquer consulta. Somente le nomes de pastas.
"""

import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

DATABASE_PREFIX = "BANCO_AUTCOM_"
SCRIPTS_FOLDER = "scripts"
DISCONTINUED_MARK = "descontinuad"
MAX_DEPTH = 6
_VERSION_NAME = re.compile(r"^\d+(\.\d+)+$")


@dataclass(frozen=True)
class VersionFolder:
    version: str
    scripts_path: Path
    relative: str  # pasta da versao, relativa a raiz
    discontinued: bool
    database: str  # nome sugerido (pode estar vazio)


@dataclass
class DiscoveryResult:
    root: Path
    versions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def is_version_name(name):
    return bool(_VERSION_NAME.match(str(name)))


def version_sort_key(version):
    """Chave de ordenacao: numeros primeiro (por valor); nomes nao numericos depois."""
    parts = str(version).split(".")
    if all(part.isdigit() for part in parts):
        return (0, tuple(int(part) for part in parts), "")
    return (1, (), str(version).lower())


def _plain(text):
    decomposed = unicodedata.normalize("NFKD", str(text))
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()


def database_name_for(root_name, version):
    """`BANCO_AUTCOM_<produto>_<partes da versao com 2 digitos>` ou '' se nao der.

    O produto e a primeira parte do nome da raiz (`379` em `379.48.2.30`).
    Hipotese a partir de 5 exemplos: cada parte da versao tem ao menos 2 digitos
    (`2` vira `02`).
    """
    if not is_version_name(root_name) or not is_version_name(version):
        return ""
    product = root_name.split(".")[0]
    return DATABASE_PREFIX + "_".join([product] + [part.zfill(2) for part in version.split(".")])


def _product_root_name(folder):
    """Nome da pasta ancestral mais proxima com nome de versao (ex.: `379.48.2.30`)."""
    for ancestor in folder.parents:
        if is_version_name(ancestor.name):
            return ancestor.name
    return ""


def _has_sql_files(folder):
    try:
        return any(entry.is_file() and entry.name.lower().endswith(".sql") for entry in os.scandir(folder))
    except OSError:
        return False


def _children(folder):
    try:
        return [Path(e.path) for e in os.scandir(folder) if e.is_dir()]
    except OSError:
        return []


def _direct_selection(root):
    """Quando a pasta escolhida e uma pasta SCRIPTS, uma pasta de versao ou tem .SQL.

    Devolve um resultado com uma unica versao, ou None se nao for o caso.
    """
    version_dir = None
    scripts_path = None
    if root.name.lower() == SCRIPTS_FOLDER:
        scripts_path = root
        version_dir = root.parent
    elif is_version_name(root.name):
        child = next((c for c in _children(root) if c.name.lower() == SCRIPTS_FOLDER), None)
        if child is not None:
            scripts_path, version_dir = child, root
    if scripts_path is None and _has_sql_files(root):
        scripts_path, version_dir = root, root
    if scripts_path is None:
        return None
    name = version_dir.name
    product = _product_root_name(version_dir)
    result = DiscoveryResult(root=root)
    result.versions.append(
        VersionFolder(
            version=name,
            scripts_path=scripts_path,
            relative=".",
            discontinued=False,
            database=database_name_for(product, name) if product else "",
        )
    )
    result.warnings.append(f"Usando a pasta escolhida como uma unica versao ({name}).")
    return result


def discover_versions(root, cancel_event=None):
    """Acha as pastas `SCRIPTS` abaixo de `root` cujo pai tem nome de versao.

    Tambem aceita a propria pasta `SCRIPTS`, uma pasta de versao ou uma pasta com .SQL.
    """
    root = Path(root)
    result = DiscoveryResult(root=root)
    if not root.is_dir():
        result.warnings.append(f"Pasta raiz nao encontrada: {root}")
        return result
    direct = _direct_selection(root)
    if direct is not None:
        return direct
    for directory, subdirectories, _names in os.walk(root, followlinks=False):
        if cancel_event is not None and cancel_event.is_set():
            break
        current = Path(directory)
        depth = len(current.relative_to(root).parts)
        subdirectories.sort(key=str.lower)
        if depth >= MAX_DEPTH:
            subdirectories.clear()
            continue
        for name in list(subdirectories):
            if name.lower() != SCRIPTS_FOLDER:
                continue
            subdirectories.remove(name)  # nao desce dentro de SCRIPTS
            if current == root or not is_version_name(current.name):
                shown = current.name if current != root else "(raiz)"
                result.warnings.append(f"Pasta SCRIPTS ignorada: o pai '{shown}' nao tem nome de versao.")
                continue
            relative_parts = current.relative_to(root).parts
            result.versions.append(
                VersionFolder(
                    version=current.name,
                    scripts_path=current / name,
                    relative=str(current.relative_to(root)),
                    discontinued=any(DISCONTINUED_MARK in _plain(part) for part in relative_parts[:-1]),
                    database=database_name_for(root.name, current.name),
                )
            )
    result.versions.sort(key=lambda item: (version_sort_key(item.version), item.relative.lower()))
    seen = {}
    for item in result.versions:
        seen.setdefault(item.version, []).append(item.relative)
    for version, places in seen.items():
        if len(places) > 1:
            result.warnings.append(f"Versao {version} aparece em mais de uma pasta: {', '.join(places)}")
    return result
