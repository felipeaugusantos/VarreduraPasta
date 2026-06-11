from datetime import datetime
from pathlib import Path

from app.runtime import logs_directory


LOGS_DIRECTORY = logs_directory()
LOG_FILE = None


def current_log_file(now=None):
    if LOG_FILE is not None:
        return Path(LOG_FILE)
    now = now or datetime.now()
    return LOGS_DIRECTORY / f"fechamentos-{now:%Y-%m}.log"


def write_log(message):
    now = datetime.now()
    log_file = current_log_file(now)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    with log_file.open("a", encoding="utf-8") as log:
        log.write(f"[{timestamp}] {message}\n")
