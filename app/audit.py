from pathlib import Path

from app.logging_utils import LOGS_DIRECTORY


def list_log_files():
    if not LOGS_DIRECTORY.exists():
        return []
    return sorted(LOGS_DIRECTORY.glob("*.log"))


def read_log_file(log_path):
    try:
        return Path(log_path).read_text(encoding="utf-8")
    except OSError as error:
        return f"Erro ao ler log: {error}"
