import csv
import json
import tempfile
import threading
import unittest
from pathlib import Path

from app.sqlaudit import audit_runner, db_reader, report, sql_parse
from app.sqlaudit.audit_runner import AuditRequest, ServerConnection, VersionRequest, run_audit, test_connection
from app.sqlaudit.version_discovery import discover_versions
from app.sqlaudit.version_tree import VersionTree
from tests.test_sqlaudit_reader import FakeConnection, full_responses

AVAILABLE = sql_parse.is_available()
PASSWORD = "SenhaSecreta#42"
BASE = "48.2.30.74.150"
CHILD = "48.2.30.74.150.45"
DB_BASE = "BANCO_AUTCOM_379_48_02_30_74_150"
DB_CHILD = "BANCO_AUTCOM_379_48_02_30_74_150_45"


def responses(database, columns, table="CLIENTES"):
    result = full_responses(database)
    result[db_reader.SQL_TABLES] = [(table, "InnoDB", "utf8mb4_general_ci", None)]
    result[db_reader.SQL_COLUMNS] = [(table, n, i, t, "YES", None, "", None, None, "") for i, (n, t) in enumerate(columns, 1)]
    return result


def connector_for(by_database, seen=None):
    def connector(**options):
        if seen is not None:
            seen.append(options)
        database = options["database"]
        if database not in by_database:
            raise Exception(1049, f"Unknown database '{database}' (using password: YES) {options['password']}")
        return FakeConnection(by_database[database])

    return connector


@unittest.skipUnless(AVAILABLE, "sqlglot nao instalado")
class RunnerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "379.48.2.30"
        for version, files in {
            BASE: {"SCRIPT_1.SQL": "CREATE TABLE CLIENTES (ID INT);", "SCRIPT_2.SQL": "ALTER TABLE CLIENTES ADD A INT;", "SCRIPT_3.SQL": "ALTER TABLE CLIENTES ADD B INT;"},
            CHILD: {"SCRIPT_4.SQL": "ALTER TABLE CLIENTES ADD C INT;"},
        }.items():
            folder = self.root / version / "SCRIPTS"
            folder.mkdir(parents=True)
            for name, text in files.items():
                (folder / name).write_text(text, encoding="utf-8")
        self.folders = {f.version: f for f in discover_versions(self.root).versions}
        self.tree = VersionTree.build(list(self.folders))
        self.server = ServerConnection("10.0.0.35", 43309, "leitura", PASSWORD)

    def request(self, *versions, tree=None, server=None):
        return AuditRequest(
            self.root,
            [VersionRequest(v, {BASE: DB_BASE, CHILD: DB_CHILD}[v]) for v in versions],
            self.folders,
            tree or self.tree,
            server or self.server,
        )

    def banks(self, **kwargs):
        return {
            DB_BASE: responses(DB_BASE, [("ID", "int"), ("A", "int"), ("B", "int")]),
            DB_CHILD: responses(DB_CHILD, [("ID", "int"), ("A", "int"), ("C", "int")]),
        }


class RunAuditTests(RunnerTestCase):
    def test_audits_a_child_with_the_inherited_scripts_and_suggests_the_fork(self):
        self.tree.set_base(CHILD, BASE, fork=2)
        run = run_audit(self.request(CHILD), connector=connector_for(self.banks()))
        (audit,) = run.versions
        self.assertEqual(audit.error, "")
        self.assertEqual(audit.scripts_total, 3)  # SCRIPT_1, SCRIPT_2 da base (ate o fork) + SCRIPT_4
        self.assertEqual([f for _v, f in audit.excluded], ["SCRIPT_3.SQL"])
        states = {(o.table, o.column): o.status for o in audit.comparison.objects}
        self.assertEqual(set(states.values()), {"COMPATIVEL"})
        hint = audit.fork_hint
        self.assertEqual((hint.candidate, hint.first_missing), (2, 3))  # B nao existe no banco da filha
        self.assertEqual(audit.server_version, "8.0.36")

    def test_records_the_sha256_of_every_script_used_with_its_origin(self):
        import hashlib

        self.tree.set_base(CHILD, BASE, fork=2)
        run = run_audit(self.request(CHILD), connector=connector_for(self.banks()))
        files = {f.script: f for f in run.versions[0].files}
        self.assertEqual(sorted(files), ["SCRIPT_1.SQL", "SCRIPT_2.SQL", "SCRIPT_4.SQL"])  # SCRIPT_3 ficou alem do fork
        self.assertEqual(files["SCRIPT_4.SQL"].sha256, hashlib.sha256(b"ALTER TABLE CLIENTES ADD C INT;").hexdigest())
        self.assertEqual((files["SCRIPT_1.SQL"].version, files["SCRIPT_1.SQL"].inherited), (BASE, True))
        self.assertEqual((files["SCRIPT_4.SQL"].version, files["SCRIPT_4.SQL"].inherited), (CHILD, False))
        self.assertTrue(all(f.certain for f in files.values()))
        exported = report.build_json(run)["versoes"][0]["scripts"]
        self.assertEqual({s["script"]: s["sha256"] for s in exported}, {n: f.sha256 for n, f in files.items()})

    def test_without_a_declared_fork_inherited_findings_are_not_firm(self):
        run = run_audit(self.request(CHILD), connector=connector_for(self.banks()))
        states = {(o.table, o.column): o.status for o in run.versions[0].comparison.objects}
        self.assertEqual(states[("CLIENTES", "B")], "INDETERMINADO")
        self.assertTrue(any("fork nao informado" in w for w in run.versions[0].warnings))

    def test_one_failing_version_does_not_stop_the_others(self):
        banks = self.banks()
        del banks[DB_CHILD]
        run = run_audit(self.request(BASE, CHILD), connector=connector_for(banks))
        self.assertEqual(run.versions[0].error, "")
        self.assertIn("nao existe no servidor", run.versions[1].error)
        self.assertIsNotNone(run.versions[0].comparison)
        self.assertIsNone(run.versions[1].comparison)

    def test_password_is_scrubbed_from_every_error(self):
        def leaking(**options):
            raise RuntimeError(f"falha com {options['password']}")

        run = run_audit(self.request(BASE), connector=leaking)
        self.assertNotIn(PASSWORD, run.versions[0].error)
        self.assertIn("***", run.versions[0].error)
        self.assertIsNone(run.versions[0].comparison)

        def unexpected(**options):
            raise ValueError(options["password"])

        run = run_audit(self.request(BASE), connector=unexpected)
        self.assertNotIn(PASSWORD, run.versions[0].error)

    def test_missing_scripts_folder_is_a_version_error(self):
        folders = dict(self.folders)
        del folders[BASE]
        request = self.request(CHILD)
        request.folders = folders
        self.tree.set_base(CHILD, BASE, fork=2)
        run = run_audit(request, connector=connector_for(self.banks()))
        self.assertIn(f"Pasta SCRIPTS da versao {BASE} nao foi encontrada", run.versions[0].error)

    def test_wrong_database_is_an_error_not_a_comparison(self):
        wrong = {DB_BASE: full_responses("OUTRO_BANCO")}
        run = run_audit(self.request(BASE), connector=connector_for(wrong))
        self.assertIn("era esperado", run.versions[0].error)

    def test_progress_is_monotonic_and_ends_at_one(self):
        events = []
        run_audit(self.request(BASE, CHILD), progress=lambda text, fraction: events.append((text, fraction)), connector=connector_for(self.banks()))
        fractions = [fraction for _text, fraction in events]
        self.assertEqual(fractions, sorted(fractions))
        self.assertEqual(fractions[-1], 1.0)
        self.assertTrue(events[0][0].startswith("[1/2]"))

    def test_cancel_before_start_runs_nothing(self):
        cancel = threading.Event()
        cancel.set()
        seen = []
        run = run_audit(self.request(BASE), cancel_event=cancel, connector=connector_for(self.banks(), seen))
        self.assertTrue(run.cancelled)
        self.assertEqual((run.versions, seen), ([], []))

    def test_cancel_in_the_middle_never_compares_a_partial_schema(self):
        cancel = threading.Event()
        seen = []

        def progress(text, fraction):
            if "consultando" in text:
                cancel.set()

        run = run_audit(self.request(BASE, CHILD), progress=progress, cancel_event=cancel, connector=connector_for(self.banks(), seen))
        self.assertTrue(run.cancelled)
        self.assertEqual(len(run.versions), 1)
        self.assertIsNone(run.versions[0].comparison)  # lido pela metade: descartado, nao comparado
        self.assertEqual(run.versions[0].error, "")

    def test_tls_options_are_passed_through(self):
        seen = []
        server = ServerConnection("h", 3306, "u", PASSWORD, "verify", "C:\\ca.pem")
        run_audit(self.request(BASE, server=server), connector=connector_for(self.banks(), seen))
        self.assertEqual(seen[0]["ssl"]["ca"], "C:\\ca.pem")
        self.assertTrue(run_audit(self.request(BASE, server=server), connector=connector_for(self.banks())).versions[0].tls_verified)

    def test_password_is_not_in_repr_or_in_the_result(self):
        self.assertNotIn(PASSWORD, repr(self.server))
        run = run_audit(self.request(BASE), connector=connector_for(self.banks()))
        self.assertNotIn(PASSWORD, json.dumps(report.build_json(run), ensure_ascii=False))


class TestConnectionTests(RunnerTestCase):
    def test_reports_version_database_and_visible_tables(self):
        ok, message = test_connection(self.server, DB_BASE, connector=connector_for(self.banks()))
        self.assertTrue(ok)
        self.assertIn("MySQL 8.0.36", message)
        self.assertIn("1 tabela(s) visivel(is)", message)
        self.assertIn("sem verificar", message)

    def test_database_without_visible_tables_is_a_failure_with_the_permission_hint(self):
        ok, message = test_connection(self.server, DB_BASE, connector=connector_for({DB_BASE: full_responses(DB_BASE) | {db_reader.SQL_TABLES: []}}))
        self.assertFalse(ok)
        self.assertIn("SELECT nas tabelas", message)

    def test_connection_failure_is_a_message_without_password(self):
        ok, message = test_connection(self.server, DB_BASE, connector=connector_for({}))
        self.assertFalse(ok)
        self.assertIn("nao existe no servidor", message)
        self.assertNotIn(PASSWORD, message)


if __name__ == "__main__":
    unittest.main()
