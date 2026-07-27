import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


def application_directory():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_directory():
    configured = str(os.environ.get("VARREDURA_SISTEMA_DATA_DIR") or "").strip()
    if configured:
        return Path(configured)
    local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if local_app_data:
        return Path(local_app_data) / "VarreduraSistema"
    return application_directory()


def user_data_file(file_name, seed_file=None):
    destination = data_directory() / file_name
    candidates = [application_directory() / file_name]
    if seed_file:
        candidates.append(Path(seed_file))
    if not destination.exists():
        for candidate in candidates:
            if destination == candidate or not candidate.exists():
                continue
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, destination)
                break
            except OSError:
                continue
    return destination


def logs_directory():
    return data_directory() / "logs"


def resource_path(relative_path):
    if getattr(sys, "frozen", False):
        base_path = Path(getattr(sys, "_MEIPASS"))
    else:
        base_path = application_directory()
    return base_path / relative_path


def executable_generation_text():
    if not getattr(sys, "frozen", False):
        return "Ambiente de desenvolvimento"

    try:
        generated_at = datetime.fromtimestamp(Path(sys.executable).stat().st_mtime)
    except OSError:
        return "Nao identificado"
    return generated_at.strftime("%d/%m/%Y %H:%M")
