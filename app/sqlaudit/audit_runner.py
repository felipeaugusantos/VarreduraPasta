"""Orquestra uma auditoria completa, sem Tkinter: scripts -> modelo -> banco -> comparacao.

Para cada versao escolhida: le os scripts da cadeia de bases, monta o estado
esperado, le a estrutura do banco DAQUELA versao (somente leitura) e compara. Um
erro numa versao (rede, permissao, pasta ausente) vira o `error` dela e as outras
continuam. Cancelamento e checado entre etapas; senhas nunca entram no resultado.
"""

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.sqlaudit.comparator import ComparisonError, compare
from app.sqlaudit.db_reader import TLS_ENCRYPT, AuditDbError, connect_readonly
from app.sqlaudit.expected_model import build_expected_model
from app.sqlaudit.file_scanner import SqlFileError, scan_sql_files
from app.sqlaudit.fork_hint import suggest_fork
from app.sqlaudit.sql_parse import SqlParserUnavailable
from app.sqlaudit.version_tree import TreeError, effective_scripts

EXPECTED_ERRORS = (AuditDbError, SqlFileError, TreeError, ComparisonError, SqlParserUnavailable)


@dataclass(frozen=True)
class ServerConnection:
    host: str
    port: int
    username: str
    password: str = field(repr=False)
    tls: str = TLS_ENCRYPT
    ca_file: str = ""


@dataclass(frozen=True)
class VersionRequest:
    version: str
    database: str


@dataclass
class AuditRequest:
    root: Path
    versions: list  # VersionRequest
    folders: dict  # versao -> VersionFolder (todas as descobertas, para a cadeia de bases)
    tree: object  # VersionTree
    server: ServerConnection


@dataclass(frozen=True)
class ScriptRecord:
    """Um script usado no modelo de uma versao, com o SHA-256 que o identifica."""

    version: str  # pasta de onde veio
    script: str
    number: int | None
    sha256: str
    size: int
    certain: bool
    inherited: bool


@dataclass
class VersionAudit:
    version: str
    database: str
    comparison: object = None
    fork_hint: object = None
    server_version: str = ""
    tls_verified: bool = False
    scripts_total: int = 0
    excluded: list = field(default_factory=list)
    files: list = field(default_factory=list)  # ScriptRecord
    warnings: list = field(default_factory=list)  # leitura, fila efetiva, arquivos pulados
    error: str = ""
    duration: float = 0.0


@dataclass
class AuditRun:
    started: datetime
    user: str
    root: Path
    versions: list = field(default_factory=list)
    finished: datetime | None = None
    cancelled: bool = False


def os_user():
    return os.environ.get("USERNAME") or os.environ.get("USER") or "desconhecido"


def _scrub(text, password):
    return str(text).replace(password, "***") if password else str(text)


def test_connection(server, database, connector=None, cancel_event=None):
    """(ok, mensagem) depois de conectar e ler a lista de tabelas visiveis."""
    try:
        with connect_readonly(
            server.host, server.port, database, server.username, server.password,
            tls=server.tls, ca_file=server.ca_file, connector=connector,
        ) as connection:
            schema = connection.read_schema(database, cancel_event)
    except AuditDbError as error:
        return False, _scrub(error, server.password)
    tls = "certificado verificado" if schema.tls_verified else "TLS sem verificar certificado"
    head = f"Conectado ao MySQL {schema.server_version}, banco '{schema.database}' ({tls})"
    if not schema.tables:
        return False, f"{head}: nenhuma tabela visivel. Confira o banco e se o usuario tem SELECT nas tabelas."
    return True, f"{head}: {len(schema.tables)} tabela(s) visivel(is)."


def run_audit(request, progress=None, cancel_event=None, connector=None):
    """Executa a auditoria. `progress(mensagem, fracao_0_a_1)` e opcional."""
    run = AuditRun(started=datetime.now(), user=os_user(), root=Path(request.root))
    total = max(len(request.versions), 1)
    scans = {}
    parse_cache = {}

    def cancelled():
        return cancel_event is not None and cancel_event.is_set()

    for index, item in enumerate(request.versions):
        if cancelled():
            break
        audit = VersionAudit(item.version, item.database)
        run.versions.append(audit)
        started = time.monotonic()

        def report(message, fraction, _index=index, _item=item):
            if progress is not None:
                progress(f"[{_index + 1}/{total}] {_item.version}: {message}", (_index + fraction) / total)

        try:
            _audit_version(request, item, audit, scans, parse_cache, report, cancel_event, connector)
        except EXPECTED_ERRORS as error:
            audit.error = _scrub(error, request.server.password)
        except Exception as error:  # um erro inesperado nao derruba as outras versoes
            audit.error = _scrub(f"Erro inesperado: {type(error).__name__}: {error}", request.server.password)
        audit.duration = time.monotonic() - started
    run.cancelled = cancelled()
    run.finished = datetime.now()
    if progress is not None and not run.cancelled:
        progress("Concluido.", 1.0)
    return run


def _scan(request, version, scans, cancel_event):
    if version not in scans:
        folder = request.folders.get(version)
        if folder is None:
            raise SqlFileError(f"Pasta SCRIPTS da versao {version} nao foi encontrada.")
        scans[version] = scan_sql_files(folder.scripts_path, cancel_event=cancel_event)
    return scans[version]


def _audit_version(request, item, audit, scans, parse_cache, report, cancel_event, connector):
    def cancelled():
        return cancel_event is not None and cancel_event.is_set()

    server = request.server
    tree = request.tree
    chain = tree.chain(item.version)
    report("lendo scripts", 0.02)
    scripts_by_version = {}
    for version in chain:
        scan = _scan(request, version, scans, cancel_event)
        if scan.cancelled:
            return
        scripts_by_version[version] = scan.files
        if version == item.version:
            audit.warnings.extend(scan.warnings)
            audit.warnings.extend(f"Arquivo ignorado: {skipped.relative} ({skipped.reason})" for skipped in scan.skipped)

    effective = effective_scripts(tree, scripts_by_version, item.version)
    audit.warnings.extend(effective.warnings)
    audit.excluded = list(effective.excluded)
    audit.scripts_total = len(effective.scripts)
    audit.files = [
        ScriptRecord(
            item.version, item.script.relative, item.script.number, item.script.sha256,
            item.script.size, item.certain, item.inherited,
        )
        for item in effective.scripts
    ]

    def model_progress(start, width):
        return lambda done, size: report("interpretando scripts", start + width * done / max(size, 1))

    model = build_expected_model(effective, cache=parse_cache, cancel_event=cancel_event, progress=model_progress(0.1, 0.5))
    if cancelled():
        return

    report("consultando o banco", 0.65)
    with connect_readonly(
        server.host, server.port, item.database, server.username, server.password,
        tls=server.tls, ca_file=server.ca_file, connector=connector,
    ) as connection:
        schema = connection.read_schema(item.database, cancel_event)
    if cancelled():
        return
    audit.server_version = schema.server_version
    audit.tls_verified = schema.tls_verified

    report("comparando", 0.8)
    audit.comparison = compare(model, schema, expected_database=item.database)

    base = tree.nodes[item.version].base
    if base:
        report("sugerindo o ponto de fork", 0.9)
        base_model = build_expected_model(
            effective_scripts(tree, scripts_by_version, base), cache=parse_cache, cancel_event=cancel_event
        )
        if cancelled():
            return
        audit.fork_hint = suggest_fork(
            compare(base_model, schema), base_version=base, earliest_restore=schema.earliest_table_creation()
        )
    report("pronto", 1.0)
