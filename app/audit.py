from pathlib import Path

from app.runtime import application_directory

LOGS_DIRECTORY = application_directory() / "logs"


def list_log_files():
    if not LOGS_DIRECTORY.exists():
        return []
    return sorted(LOGS_DIRECTORY.glob("*.log"))


def read_log_file(log_path):
    try:
        return Path(log_path).read_text(encoding="utf-8")
    except OSError as error:
        return f"Erro ao ler log: {error}"
