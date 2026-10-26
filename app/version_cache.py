"""Cache em disco das versoes lidas dentro dos ZIPs.

Extrair o Autcom.exe de cada ZIP e a parte mais lenta da varredura. O resultado
so muda quando o ZIP muda (data/tamanho), entao fica gravado em
`zip_versions.json` e e reaproveitado ao reabrir o sistema.
"""
import json
import threading
from uuid import uuid4

from app.runtime import user_data_file

CACHE_FILE = user_data_file("zip_versions.json")
MAX_ENTRIES = 2000
_LOCK = threading.Lock()
_entries = None
_dirty = False


def _key(zip_path, file_name, read_version):
    return f"{str(zip_path).lower()}|{file_name.lower()}|{int(bool(read_version))}"


def _load():
    global _entries
    if _entries is None:
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            _entries = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _entries = {}
    return _entries


def lookup(zip_path, file_name, read_version, stamp):
    """Resultado gravado para o ZIP, ou None se nao existe ou o ZIP mudou."""
    with _LOCK:
        entry = _load().get(_key(zip_path, file_name, read_version))
    if not isinstance(entry, dict) or entry.get("stamp") != list(stamp):
        return None
    result = entry.get("result")
    if not isinstance(result, list) or len(result) != 4:
        return None
    return tuple(result)


def store(zip_path, file_name, read_version, stamp, result):
    """Grava so leituras bem-sucedidas; erros (ZIP em gravacao etc.) sao refeitos."""
    global _dirty
    if result[3] is not None:
        return
    with _LOCK:
        entries = _load()
        key = _key(zip_path, file_name, read_version)
        entries.pop(key, None)
        entries[key] = {"stamp": list(stamp), "result": list(result)}
        while len(entries) > MAX_ENTRIES:
            entries.pop(next(iter(entries)))
        _dirty = True


def flush():
    """Grava no disco se houve novidade. Falha de disco nunca derruba a varredura."""
    global _dirty
    with _LOCK:
        if not _dirty or _entries is None:
            return
        snapshot = dict(_entries)
        _dirty = False
    temp_file = CACHE_FILE.with_name(f".{CACHE_FILE.name}.{uuid4().hex}.tmp")
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_file.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        temp_file.replace(CACHE_FILE)
    except OSError:
        pass
    finally:
        try:
            temp_file.unlink()
        except OSError:
            pass


def reset():
    """Esquece o que esta em memoria (usado pelos testes)."""
    global _entries, _dirty
    with _LOCK:
        _entries = None
        _dirty = False
