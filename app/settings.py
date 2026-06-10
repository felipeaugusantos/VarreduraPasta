import json
from pathlib import Path
from uuid import uuid4

from app.config import (
    BASE_DIRECTORY,
    COPY_TARGET_DIRECTORY,
    IGNORED_PROJECT_FOLDERS,
)
from app.runtime import application_directory


SETTINGS_FILE = application_directory() / "settings.json"


def _load_settings():
    if not SETTINGS_FILE.exists():
        return {}

    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_settings(data):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = SETTINGS_FILE.with_name(f".{SETTINGS_FILE.name}.{uuid4().hex}.tmp")
    try:
        temp_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temp_file.replace(SETTINGS_FILE)
    finally:
        try:
            temp_file.unlink(missing_ok=True)
        except OSError:
            pass


def load_base_directory():
    data = _load_settings()
    value = data.get("base_directory")
    return Path(value) if value else BASE_DIRECTORY


def save_base_directory(base_directory):
    data = _load_settings()
    data["base_directory"] = str(Path(base_directory))
    _save_settings(data)


def load_copy_target_directory():
    data = _load_settings()
    value = data.get("copy_target_directory")
    return Path(value) if value else COPY_TARGET_DIRECTORY


def save_copy_target_directory(copy_target_directory):
    data = _load_settings()
    value = str(copy_target_directory).strip()
    data["copy_target_directory"] = str(Path(value)) if value else ""
    _save_settings(data)


def load_ignored_project_folders():
    data = _load_settings()
    value = data.get("ignored_project_folders")
    if not isinstance(value, list):
        return set(IGNORED_PROJECT_FOLDERS)

    folders = {
        str(folder).strip().lower()
        for folder in value
        if str(folder).strip()
    }
    return folders


def save_ignored_project_folders(folders):
    data = _load_settings()
    normalized = sorted(
        {
            str(folder).strip().lower()
            for folder in folders
            if str(folder).strip()
        }
    )
    data["ignored_project_folders"] = normalized
    _save_settings(data)
