import json
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.runtime import user_data_file


MEMORY_FILE = user_data_file("zip_memory.json")
_LOCK = threading.Lock()
_cache = None


@dataclass(frozen=True)
class ZipRecord:
    """Total de ZIPs do ultimo fechamento e o maior total ja visto na pasta."""

    last_zips: int
    max_zips: int
    updated_at: str = ""

    @property
    def below_max(self):
        return self.last_zips < self.max_zips


def _key(folder_name):
    return str(folder_name).strip().lower()


def _load():
    global _cache
    if _cache is not None:
        return _cache
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except FileNotFoundError:
        data = {}
    except (OSError, json.JSONDecodeError):
        try:
            shutil.copy2(MEMORY_FILE, MEMORY_FILE.with_name(f"{MEMORY_FILE.name}.corrupt.bak"))
        except OSError:
            pass
        data = {}
    _cache = data
    return _cache


def _save(data):
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = MEMORY_FILE.with_name(f".{MEMORY_FILE.name}.{uuid4().hex}.tmp")
    try:
        temp_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_file.replace(MEMORY_FILE)
    finally:
        try:
            temp_file.unlink(missing_ok=True)
        except OSError:
            pass


def _to_record(entry):
    if not isinstance(entry, dict):
        return None
    try:
        last_zips = int(entry["last_zips"])
        max_zips = int(entry["max_zips"])
    except (KeyError, TypeError, ValueError):
        return None
    return ZipRecord(last_zips, max_zips, str(entry.get("updated_at") or ""))


def get_record(folder_name):
    with _LOCK:
        return _to_record(_load().get(_key(folder_name)))


def record_closing(folder_name, zip_count, now=None):
    """Registra o total do fechamento e devolve o ZipRecord atualizado."""
    zip_count = int(zip_count)
    with _LOCK:
        data = dict(_load())
        previous = _to_record(data.get(_key(folder_name)))
        max_zips = max(zip_count, previous.max_zips) if previous else zip_count
        record = ZipRecord(
            last_zips=zip_count,
            max_zips=max_zips,
            updated_at=(now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
        )
        data[_key(folder_name)] = {
            "last_zips": record.last_zips,
            "max_zips": record.max_zips,
            "updated_at": record.updated_at,
        }
        _save(data)
        global _cache
        _cache = data
        return record


def count_zip_files(folder):
    return sum(
        1
        for item in Path(folder).iterdir()
        if item.suffix.lower() == ".zip" and item.is_file()
    )


def reset_cache():
    """Usado nos testes ao trocar MEMORY_FILE."""
    global _cache
    with _LOCK:
        _cache = None
