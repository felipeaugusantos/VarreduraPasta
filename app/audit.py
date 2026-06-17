from pathlib import Path
import re

from app.logging_utils import LOGS_DIRECTORY


LOG_LINE_PATTERN = re.compile(r"^\[(?P<timestamp>[^\]]+)\]\s+(?P<body>.*)$")
AUDIT_FIELDS = (
    "timestamp",
    "acao",
    "resultado",
    "usuario",
    "projeto",
    "tipo",
    "origem",
    "destino",
    "bat",
    "fileversion",
    "productversion",
    "autcom_mb",
    "motivo",
    "raw",
)


def list_log_files():
    if not LOGS_DIRECTORY.exists():
        return []
    return sorted(LOGS_DIRECTORY.glob("*.log"))


def read_log_file(log_path):
    try:
        return Path(log_path).read_text(encoding="utf-8")
    except OSError as error:
        return f"Erro ao ler log: {error}"


def parse_log_line(line):
    match = LOG_LINE_PATTERN.match(line)
    if not match:
        return {
            "timestamp": "",
            "acao": "",
            "resultado": "",
            "usuario": "",
            "projeto": "",
            "tipo": "",
            "origem": "",
            "destino": "",
            "bat": "",
            "fileversion": "",
            "productversion": "",
            "autcom_mb": "",
            "motivo": "",
            "raw": line,
        }

    body_parts = [part.strip() for part in match.group("body").split("|")]
    action_parts = body_parts[0].split()
    action = " ".join(action_parts[:-1]) if len(action_parts) > 1 else body_parts[0]
    result = action_parts[-1] if len(action_parts) > 1 else ""
    entry = {
        "timestamp": match.group("timestamp"),
        "acao": action,
        "resultado": result,
        "usuario": "",
        "projeto": "",
        "tipo": "",
        "origem": "",
        "destino": "",
        "bat": "",
        "fileversion": "",
        "productversion": "",
        "autcom_mb": "",
        "motivo": "",
        "raw": line,
    }

    for part in body_parts[1:]:
        key, separator, value = part.partition("=")
        if not separator:
            continue
        normalized_key = key.strip().lower()
        if normalized_key in entry:
            entry[normalized_key] = value.strip()
    return entry


def parse_log_entries(content):
    return [
        parse_log_line(line)
        for line in content.splitlines()
        if line.strip()
    ]


def filter_log_entries(entries, search_text="", result_filter="Todos"):
    search_text = search_text.strip().lower()
    filtered = []
    for entry in reversed(entries):
        raw_text = entry.get("raw", "").lower()
        if search_text and search_text not in raw_text:
            continue
        if result_filter != "Todos" and entry.get("resultado") != result_filter:
            continue
        filtered.append(entry)
    return filtered
