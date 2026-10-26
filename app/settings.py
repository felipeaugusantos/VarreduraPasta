import json
import shutil
import threading
from functools import wraps
from pathlib import Path
from uuid import uuid4

from app.config import (
    BASE_DIRECTORY,
    COPY_TARGET_DIRECTORIES,
    COPY_TARGET_DIRECTORY,
    IGNORED_PROJECT_FOLDERS,
    SCRIPT_CONTROL_DIRECTORY,
    SVN_BASE_PATH,
    UPDATE_DIRECTORY,
)
from app.runtime import user_data_file
from app.credentials import load_jenkins_password, save_jenkins_password


SETTINGS_FILE = user_data_file("settings.json")
_SETTINGS_LOCK = threading.RLock()


def _synchronized(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        with _SETTINGS_LOCK:
            return function(*args, **kwargs)
    return wrapper


def _load_settings():
    if not SETTINGS_FILE.exists():
        return {}

    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        try:
            shutil.copy2(
                SETTINGS_FILE,
                SETTINGS_FILE.with_name(f"{SETTINGS_FILE.name}.corrupt.bak"),
            )
        except OSError:
            pass
        return {}
    except OSError:
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


@_synchronized
def save_base_directory(base_directory):
    data = _load_settings()
    data["base_directory"] = str(Path(base_directory))
    _save_settings(data)


def load_copy_target_directory():
    data = _load_settings()
    value = data.get("copy_target_directory")
    return Path(value) if value else COPY_TARGET_DIRECTORY


def _unique_paths(paths):
    unique = []
    seen = set()
    for path in paths:
        normalized = str(Path(path)).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(Path(normalized))
    return unique


def load_additional_copy_target_directories():
    data = _load_settings()
    values = data.get("copy_target_directories")
    if isinstance(values, list):
        return _unique_paths(values)

    value = data.get("copy_target_directory")
    return _unique_paths([value]) if value else []


def load_copy_target_directories():
    return _unique_paths(
        [
            *COPY_TARGET_DIRECTORIES,
            *load_additional_copy_target_directories(),
        ]
    )


@_synchronized
def save_copy_target_directories(copy_target_directories):
    data = _load_settings()
    directories = _unique_paths(copy_target_directories)
    data["copy_target_directories"] = [str(path) for path in directories]
    data["copy_target_directory"] = str(directories[0]) if directories else ""
    _save_settings(data)


@_synchronized
def save_copy_target_directory(copy_target_directory):
    value = str(copy_target_directory).strip()
    save_copy_target_directories([value] if value else [])


@_synchronized
def load_jenkins_config():
    data = _load_settings()
    value = data.get("jenkins")
    if not isinstance(value, dict):
        value = {}

    password = load_jenkins_password()
    legacy_password = str(value.get("password") or "")
    if not password and legacy_password:
        username = str(value.get("username") or "admin").strip()
        save_jenkins_password(username, legacy_password)
        password = legacy_password
        value.pop("password", None)
        data["jenkins"] = value
        _save_settings(data)

    return {
        "url": str(value.get("url") or "http://localhost:8080").strip(),
        "username": str(value.get("username") or "admin").strip(),
        "password": password,
    }


@_synchronized
def save_jenkins_config(url, username, password):
    save_jenkins_password(username, password)
    data = _load_settings()
    data["jenkins"] = {
        "url": str(url or "").strip() or "http://localhost:8080",
        "username": str(username or "").strip(),
    }
    _save_settings(data)


def load_svn_base_path():
    data = _load_settings()
    return str(data.get("svn_base_path") or SVN_BASE_PATH).strip()


@_synchronized
def save_svn_base_path(svn_base_path):
    data = _load_settings()
    data["svn_base_path"] = str(svn_base_path or "").strip()
    _save_settings(data)


def load_script_control_directory():
    data = _load_settings()
    value = str(data.get("script_control_directory") or "").strip()
    return Path(value) if value else SCRIPT_CONTROL_DIRECTORY


@_synchronized
def save_script_control_directory(directory):
    data = _load_settings()
    value = str(directory or "").strip()
    if value:
        data["script_control_directory"] = str(Path(value))
    else:
        data.pop("script_control_directory", None)
    _save_settings(data)


def load_update_directory():
    data = _load_settings()
    value = str(data.get("update_directory") or "").strip()
    return Path(value) if value else UPDATE_DIRECTORY


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


@_synchronized
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


def load_script_monitor_state():
    data = _load_settings()
    state = data.get("script_monitor")
    if not isinstance(state, dict):
        return {
            "initialized": False,
            "last_check_date": "",
            "known_files": [],
            "pending_files": [],
        }

    known_files = state.get("known_files")
    if not isinstance(known_files, list):
        known_files = []
    pending_files = state.get("pending_files")
    if not isinstance(pending_files, list):
        pending_files = []
    return {
        "initialized": bool(state.get("initialized", False)),
        "last_check_date": str(state.get("last_check_date") or ""),
        "known_files": sorted(
            {
                str(file_name).strip()
                for file_name in known_files
                if str(file_name).strip()
            }
        ),
        "pending_files": sorted(
            {
                str(file_name).strip()
                for file_name in pending_files
                if str(file_name).strip()
            }
        ),
    }


@_synchronized
def save_script_monitor_state(
    last_check_date,
    known_files,
    pending_files=None,
    initialized=True,
):
    data = _load_settings()
    if pending_files is None:
        pending_files = load_script_monitor_state().get("pending_files", [])
    data["script_monitor"] = {
        "initialized": bool(initialized),
        "last_check_date": str(last_check_date or ""),
        "known_files": sorted(
            {
                str(file_name).strip()
                for file_name in known_files
                if str(file_name).strip()
            }
        ),
        "pending_files": sorted(
            {
                str(file_name).strip()
                for file_name in pending_files
                if str(file_name).strip()
            }
        ),
    }
    _save_settings(data)
