"""Historico local das auditorias (SQLite, so biblioteca padrao) e comparacao entre elas.

Fica em `%LOCALAPPDATA%\\VarreduraSistema\\sqlaudit.db`: cada operador do TS tem o
seu, entao nao ha escrita concorrente entre pessoas. O que vale para todos vai para
`shared_log`. Guarda resultado por objeto, o SHA-256 dos scripts usados e contagens;
nunca senha, nem texto de SQL. Mantem as ultimas `MAX_RUNS` analises.
"""

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from app.runtime import data_directory
from app.sqlaudit import report
from app.sqlaudit.comparator import PRECEDENCE

SCHEMA_VERSION = 1
MAX_RUNS = 60
DB_FILE_NAME = "sqlaudit.db"
BUSY_TIMEOUT_SECONDS = 10

_SCHEMA = """
CREATE TABLE audit_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started TEXT NOT NULL, finished TEXT, os_user TEXT, root TEXT, summary TEXT
);
CREATE TABLE audit_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES audit_run(id) ON DELETE CASCADE,
    version TEXT NOT NULL, db_name TEXT, server_version TEXT, tls_verified INTEGER,
    scripts_total INTEGER, error TEXT, duration REAL, counts TEXT
);
CREATE TABLE audit_object (
    version_id INTEGER NOT NULL REFERENCES audit_version(id) ON DELETE CASCADE,
    kind TEXT, tbl TEXT, col TEXT, status TEXT, differences TEXT, notes TEXT,
    script TEXT, line INTEGER, certain TEXT
);
CREATE INDEX idx_audit_object_version ON audit_object(version_id);
CREATE TABLE audit_file (
    version_id INTEGER NOT NULL REFERENCES audit_version(id) ON DELETE CASCADE,
    origin_version TEXT, script TEXT, number INTEGER, sha256 TEXT, size INTEGER, certain INTEGER
);
CREATE INDEX idx_audit_file_version ON audit_file(version_id);
"""

# 0 = pior. Usado para dizer se uma mudanca de estado piorou ou melhorou.
_RANK = {status: index for index, status in enumerate(PRECEDENCE)}


@dataclass(frozen=True)
class RunInfo:
    id: int
    started: str
    finished: str
    user: str
    root: str
    summary: str
    versions: tuple = ()


@dataclass(frozen=True)
class Change:
    kind: str  # piorou | melhorou | mudou | detalhe | novo | removido
    version: str
    label: str
    old_status: str = ""
    new_status: str = ""
    old_detail: str = ""
    new_detail: str = ""


@dataclass(frozen=True)
class ScriptChange:
    kind: str  # alterado | adicionado | removido
    version: str
    script: str
    old_sha256: str = ""
    new_sha256: str = ""


@dataclass
class RunDiff:
    old: RunInfo
    new: RunInfo
    changes: list = field(default_factory=list)
    script_changes: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def counts(self):
        counts = {}
        for item in self.changes:
            counts[item.kind] = counts.get(item.kind, 0) + 1
        return counts


def default_path():
    return data_directory() / DB_FILE_NAME


class AuditStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()

    def _open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_SECONDS)
        connection.execute("PRAGMA foreign_keys = ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            connection.executescript(_SCHEMA)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
        elif version > SCHEMA_VERSION:
            connection.close()
            raise RuntimeError(f"Historico criado por uma versao mais nova do sistema (esquema {version}).")
        return connection

    # -- gravar --
    def save_run(self, run, keep=MAX_RUNS):
        """Grava a analise e devolve o id. Analise cancelada tambem e gravada (incompleta)."""
        rows_by_version = {}
        for row in report.object_rows(run):
            rows_by_version.setdefault(row["versao"], []).append(row)
        with closing(self._open()) as connection, connection:
            cursor = connection.execute(
                "INSERT INTO audit_run (started, finished, os_user, root, summary) VALUES (?, ?, ?, ?, ?)",
                (
                    run.started.isoformat(timespec="seconds"),
                    (run.finished or run.started).isoformat(timespec="seconds"),
                    run.user,
                    str(run.root),
                    report.summary_text(run),
                ),
            )
            run_id = cursor.lastrowid
            for audit in run.versions:
                counts = dict(audit.comparison.object_counts()) if audit.comparison else {}
                version_id = connection.execute(
                    "INSERT INTO audit_version (run_id, version, db_name, server_version, tls_verified, "
                    "scripts_total, error, duration, counts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id, audit.version, audit.database, audit.server_version, int(audit.tls_verified),
                        audit.scripts_total, audit.error, round(audit.duration, 2), json.dumps(counts),
                    ),
                ).lastrowid
                connection.executemany(
                    "INSERT INTO audit_object (version_id, kind, tbl, col, status, differences, notes, script, line, certain) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            version_id, row["tipo"], row["tabela"], row["coluna"], row["estado"], row["diferencas"],
                            row["notas"], row["script"], row["linha"] if row["linha"] != "" else None, row["origem_certa"],
                        )
                        for row in rows_by_version.get(audit.version, ())
                    ],
                )
                connection.executemany(
                    "INSERT INTO audit_file (version_id, origin_version, script, number, sha256, size, certain) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(version_id, f.version, f.script, f.number, f.sha256, f.size, int(f.certain)) for f in audit.files],
                )
            connection.execute(
                "DELETE FROM audit_run WHERE id NOT IN (SELECT id FROM audit_run ORDER BY id DESC LIMIT ?)", (keep,)
            )
        return run_id

    def delete_run(self, run_id):
        with closing(self._open()) as connection, connection:
            connection.execute("DELETE FROM audit_run WHERE id = ?", (run_id,))

    # -- ler --
    def list_runs(self, limit=100):
        """Analises, da mais recente para a mais antiga."""
        with closing(self._open()) as connection:
            runs = connection.execute(
                "SELECT id, started, finished, os_user, root, summary FROM audit_run ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            result = []
            for run_id, started, finished, user, root, summary in runs:
                versions = tuple(
                    row[0] for row in connection.execute("SELECT version FROM audit_version WHERE run_id = ? ORDER BY id", (run_id,))
                )
                result.append(RunInfo(run_id, started, finished or "", user or "", root or "", summary or "", versions))
        return result

    def get_run(self, run_id):
        return next((run for run in self.list_runs(MAX_RUNS + 20) if run.id == run_id), None)

    def load_objects(self, run_id, version=None):
        """Linhas no mesmo formato de `report.object_rows`."""
        query = (
            "SELECT v.version, v.db_name, o.kind, o.tbl, o.col, o.status, o.differences, o.notes, o.script, o.line, o.certain "
            "FROM audit_object o JOIN audit_version v ON v.id = o.version_id WHERE v.run_id = ?"
        )
        params = [run_id]
        if version:
            query += " AND v.version = ?"
            params.append(version)
        with closing(self._open()) as connection:
            rows = connection.execute(query + " ORDER BY v.id, o.rowid", params).fetchall()
        return [
            {
                "versao": v, "banco": db, "tipo": kind, "tabela": table, "coluna": column or "", "estado": status,
                "diferencas": differences or "", "notas": notes or "", "script": script or "",
                "linha": "" if line is None else line, "origem_certa": certain or "",
            }
            for v, db, kind, table, column, status, differences, notes, script, line, certain in rows
        ]

    def load_files(self, run_id):
        with closing(self._open()) as connection:
            rows = connection.execute(
                "SELECT v.version, f.origin_version, f.script, f.number, f.sha256, f.size, f.certain "
                "FROM audit_file f JOIN audit_version v ON v.id = f.version_id WHERE v.run_id = ? ORDER BY v.id, f.rowid",
                (run_id,),
            ).fetchall()
        return [
            {"versao": v, "versao_origem": origin, "script": script, "numero": number, "sha256": sha, "bytes": size, "origem_certa": bool(certain)}
            for v, origin, script, number, sha, size, certain in rows
        ]

    def version_results(self, run_id):
        """{versao: (banco, erro, qtd_objetos)} para saber se a versao teve resultado."""
        with closing(self._open()) as connection:
            rows = connection.execute(
                "SELECT v.version, v.db_name, v.error, (SELECT COUNT(*) FROM audit_object o WHERE o.version_id = v.id) "
                "FROM audit_version v WHERE v.run_id = ?",
                (run_id,),
            ).fetchall()
        return {version: (db, error or "", count) for version, db, error, count in rows}

    # -- comparar --
    def compare(self, old_id, new_id, version=None):
        old, new = self.get_run(old_id), self.get_run(new_id)
        if old is None or new is None:
            raise LookupError("Auditoria nao encontrada no historico.")
        diff = RunDiff(old, new)
        old_results, new_results = self.version_results(old_id), self.version_results(new_id)
        shared = sorted(set(old_results) & set(new_results))
        if version:
            shared = [v for v in shared if v == version]
        for only, label in ((set(old_results) - set(new_results), "so na anterior"), (set(new_results) - set(old_results), "so na nova")):
            if only and not version:
                diff.notes.append(f"Versao(oes) {', '.join(sorted(only))} {label}: nao comparada(s).")
        for name in shared:
            old_db, old_error, old_count = old_results[name]
            new_db, new_error, new_count = new_results[name]
            if old_db != new_db:
                diff.notes.append(f"{name}: banco diferente ({old_db} x {new_db}); comparacao pode nao fazer sentido.")
            if old_error or new_error or not old_count or not new_count:
                diff.notes.append(f"{name}: sem resultado em uma das auditorias (erro ou nada comparado); objetos nao comparados.")
            else:
                diff.changes.extend(self._object_changes(name, self.load_objects(old_id, name), self.load_objects(new_id, name)))
            diff.script_changes.extend(self._script_changes(name, old_id, new_id))
        diff.changes.sort(key=lambda c: (c.kind != "piorou", c.kind != "novo", c.version, c.label))
        return diff

    @staticmethod
    def _label(row):
        return row["tabela"] + (f".{row['coluna']}" if row["coluna"] else "")

    def _object_changes(self, version, old_rows, new_rows):
        key = lambda row: (row["tipo"], row["tabela"].lower(), row["coluna"].lower())
        old_map = {key(row): row for row in old_rows}
        new_map = {key(row): row for row in new_rows}
        changes = []
        for item, row in new_map.items():
            before = old_map.get(item)
            detail = row["diferencas"] or row["notas"]
            if before is None:
                changes.append(Change("novo", version, self._label(row), "", row["estado"], "", detail))
            elif before["estado"] != row["estado"]:
                kind = "piorou" if _RANK[row["estado"]] < _RANK[before["estado"]] else "melhorou"
                changes.append(Change(kind, version, self._label(row), before["estado"], row["estado"], before["diferencas"] or before["notas"], detail))
            elif before["diferencas"] != row["diferencas"]:
                changes.append(Change("detalhe", version, self._label(row), before["estado"], row["estado"], before["diferencas"], row["diferencas"]))
        for item, row in old_map.items():
            if item not in new_map:
                changes.append(Change("removido", version, self._label(row), row["estado"], "", row["diferencas"] or row["notas"], ""))
        return changes

    def _script_changes(self, version, old_id, new_id):
        index = lambda run_id: {(f["versao_origem"], f["script"]): f["sha256"] for f in self.load_files(run_id) if f["versao"] == version}
        old_files, new_files = index(old_id), index(new_id)
        changes = []
        for (origin, script), sha in new_files.items():
            if (origin, script) not in old_files:
                changes.append(ScriptChange("adicionado", version, script, "", sha))
            elif old_files[(origin, script)] != sha:
                changes.append(ScriptChange("alterado", version, script, old_files[(origin, script)], sha))
        changes.extend(
            ScriptChange("removido", version, script, sha, "") for (origin, script), sha in old_files.items() if (origin, script) not in new_files
        )
        return changes
