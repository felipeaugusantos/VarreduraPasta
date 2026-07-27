import hashlib
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.pattern import pattern_scope_from_project_name
from app.runtime import user_data_file


LEARNING_DB = user_data_file("learning.db")
_DB_LOCK = threading.Lock()


@dataclass(frozen=True)
class LearningInsight:
    message: str
    confidence: float


def initialize_learning_database():
    with closing(_connect()) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS project_observations (
                signature TEXT PRIMARY KEY,
                family TEXT NOT NULL,
                project_type TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                autcom_mb REAL NOT NULL,
                required_files INTEGER NOT NULL,
                zip_files INTEGER NOT NULL
            )
            """
        )
        connection.commit()


def learn_from_projects(projects):
    observations = []
    for project in projects:
        if project.status != "OK" or project.display_autcom_size_mb is None:
            continue
        checks = list(project.file_checks)
        signature_text = "|".join(
            [
                project.folder_name,
                f"{project.display_autcom_size_mb:.6f}",
                *sorted(
                    f"{check.source}:{check.found_name or ''}"
                    for check in checks
                ),
            ]
        )
        observations.append(
            (
                hashlib.sha256(signature_text.encode("utf-8")).hexdigest(),
                pattern_scope_from_project_name(project.folder_name),
                "Cloud"
                if project.folder_name.upper().endswith("_CLOUD")
                else "Local",
                datetime.now().isoformat(timespec="seconds"),
                float(project.display_autcom_size_mb),
                sum(1 for check in checks if check.found_name),
                sum(1 for check in checks if check.source == "Zip"),
            )
        )
    if not observations:
        return 0

    with _DB_LOCK:
        initialize_learning_database()
        with closing(_connect()) as connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO project_observations (
                    signature, family, project_type, observed_at, autcom_mb,
                    required_files, zip_files
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                observations,
            )
            connection.commit()
            return connection.total_changes - before


def insights_for_project(project):
    if project.display_autcom_size_mb is None:
        return []
    family = pattern_scope_from_project_name(project.folder_name)
    with _DB_LOCK:
        initialize_learning_database()
        with closing(_connect()) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*), AVG(autcom_mb), MIN(autcom_mb), MAX(autcom_mb)
                FROM project_observations
                WHERE family = ?
                """,
                (family,),
            ).fetchone()
    sample_count, average, minimum, maximum = row
    if sample_count < 3:
        return []

    current = float(project.display_autcom_size_mb)
    lower_limit = minimum * 0.80
    upper_limit = maximum * 1.20
    if lower_limit <= current <= upper_limit:
        return []
    confidence = min(0.95, 0.50 + sample_count * 0.05)
    return [
        LearningInsight(
            message=(
                f"Autcom com {current:.2f} MB fora do historico desta familia "
                f"({minimum:.2f} a {maximum:.2f} MB; media {average:.2f} MB)."
            ),
            confidence=confidence,
        )
    ]


def clear_learning_history():
    with _DB_LOCK:
        path = Path(LEARNING_DB)
        if path.exists():
            path.unlink()


def _connect():
    path = Path(LEARNING_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path, timeout=10)
