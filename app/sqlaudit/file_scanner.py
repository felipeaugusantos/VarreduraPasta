"""Descoberta e leitura dos scripts `.SQL` de uma pasta `SCRIPTS`, sem Tkinter.

Somente le arquivos: nunca executa nem altera nada. Cada arquivo recebe
SHA-256 (evidencia de alteracao; a data do arquivo nao e prova) e, quando o
nome segue `SCRIPT_<numero>.SQL`, o numero que define a ordem dentro da pasta.
"""

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES = 50000
HASH_CHUNK_BYTES = 1024 * 1024
MAX_GAP_RANGES = 20
_NUMBERED_NAME = re.compile(r"^SCRIPT_(\d+)\.sql$", re.IGNORECASE)


class SqlFileError(Exception):
    """Falha de leitura/decodificacao com mensagem pronta para o operador."""


@dataclass(frozen=True)
class ScriptFile:
    relative: str
    path: Path
    size: int
    sha256: str
    number: int | None = None


@dataclass(frozen=True)
class SkippedFile:
    relative: str
    reason: str


@dataclass
class ScanResult:
    root: Path
    files: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    cancelled: bool = False


def script_number(name):
    """Numero de `SCRIPT_<n>.SQL` (sem diferenciar maiusculas) ou None."""
    match = _NUMBERED_NAME.match(re.split(r"[\\/]", str(name))[-1])
    return int(match.group(1)) if match else None


def sort_key(script):
    """Ordem de aplicacao: numerados pelo numero (inteiro), depois os demais."""
    if script.number is None:
        return (1, 0, script.relative.lower())
    return (0, script.number, script.relative.lower())


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_sql_files(root, max_file_bytes=MAX_FILE_BYTES, max_files=MAX_FILES, cancel_event=None, progress=None):
    """Lista os `.sql` (qualquer caixa) de `root` e subpastas, em ordem de aplicacao.

    Arquivos grandes demais, ilegiveis ou que resolvam para fora de `root`
    (atalhos) vao para `skipped` com o motivo. `cancel_event` e checado a cada
    arquivo; `progress(feitos)` e chamado a cada arquivo lido.
    """
    root = Path(root)
    if not root.is_dir():
        raise SqlFileError(f"Pasta de scripts nao encontrada: {root}")
    real_root = root.resolve()
    result = ScanResult(root=root)

    for directory, subdirectories, names in os.walk(root, followlinks=False):
        subdirectories.sort(key=str.lower)
        for name in sorted(names, key=str.lower):
            if not name.lower().endswith(".sql"):
                continue
            if cancel_event is not None and cancel_event.is_set():
                result.cancelled = True
                return _finish(result)
            path = Path(directory) / name
            relative = str(path.relative_to(root))
            if len(result.files) >= max_files:
                result.skipped.append(SkippedFile(relative, f"limite de {max_files} arquivos atingido"))
                continue
            try:
                resolved = path.resolve()
                if real_root not in resolved.parents:
                    result.skipped.append(SkippedFile(relative, "aponta para fora da pasta de scripts"))
                    continue
                if not path.is_file():
                    continue
                size = path.stat().st_size
                if size > max_file_bytes:
                    result.skipped.append(
                        SkippedFile(relative, f"arquivo maior que {max_file_bytes // (1024 * 1024)} MB")
                    )
                    continue
                result.files.append(ScriptFile(relative, path, size, file_sha256(path), script_number(name)))
            except OSError as error:
                result.skipped.append(SkippedFile(relative, f"nao foi possivel ler: {error}"))
                continue
            if progress is not None:
                progress(len(result.files))
    return _finish(result)


def _finish(result):
    result.files.sort(key=sort_key)
    result.warnings = sequence_warnings(result.files)
    return result


def _ranges(numbers):
    ranges = []
    for number in numbers:
        if ranges and number == ranges[-1][1] + 1:
            ranges[-1][1] = number
        else:
            ranges.append([number, number])
    return ranges


def sequence_warnings(files):
    """Avisos sobre a sequencia numerica: duplicados, lacunas e nomes fora do padrao."""
    warnings = []
    by_number = {}
    for script in files:
        if script.number is not None:
            by_number.setdefault(script.number, []).append(script)
    for number in sorted(by_number):
        group = by_number[number]
        if len(group) > 1:
            names = ", ".join(script.relative for script in group)
            same = len({script.sha256 for script in group}) == 1
            detail = "mesmo conteudo" if same else "conteudo diferente"
            warnings.append(f"Numero {number} repetido ({detail}): {names}")
    if by_number:
        present = sorted(by_number)
        missing = [n for n in range(present[0], present[-1] + 1) if n not in by_number]
        if missing:
            ranges = _ranges(missing)
            text = ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in ranges[:MAX_GAP_RANGES])
            more = f" (+{len(ranges) - MAX_GAP_RANGES} faixa(s))" if len(ranges) > MAX_GAP_RANGES else ""
            warnings.append(f"Numeros ausentes na sequencia: {text}{more}")
    unnumbered = [script.relative for script in files if script.number is None]
    if unnumbered:
        preview = ", ".join(unnumbered[:5]) + ("..." if len(unnumbered) > 5 else "")
        warnings.append(f"{len(unnumbered)} arquivo(s) fora do padrao SCRIPT_<n>.SQL, sem ordem definida: {preview}")
    return warnings


def read_sql_text(path, max_bytes=MAX_FILE_BYTES):
    """(texto, codificacao) do script. Nunca devolve texto corrompido sem aviso.

    BOM UTF-8/UTF-16 e respeitado; sem BOM tenta UTF-8, cp1252 e latin-1.
    Binario ou UTF-16 sem BOM (bytes nulos) levanta SqlFileError.
    """
    path = Path(path)
    try:
        if path.stat().st_size > max_bytes:
            raise SqlFileError(f"{path.name}: arquivo maior que {max_bytes // (1024 * 1024)} MB.")
        raw = path.read_bytes()
    except OSError as error:
        raise SqlFileError(f"{path.name}: nao foi possivel ler ({error}).") from error
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16"), "utf-16"
        except UnicodeDecodeError as error:
            raise SqlFileError(f"{path.name}: UTF-16 invalido ({error}).") from error
    if b"\x00" in raw:
        raise SqlFileError(f"{path.name}: contem bytes nulos (binario ou UTF-16 sem BOM).")
    for encoding in ("utf-8", "cp1252"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"
