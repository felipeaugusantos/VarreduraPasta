import gc
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.sqlaudit import audit_store, report, shared_log
from app.sqlaudit.audit_runner import AuditRun, ScriptRecord, VersionAudit
from app.sqlaudit.audit_store import AuditStore
from app.sqlaudit.comparator import ComparisonResult, Difference, ObjectResult
from app.sqlaudit.expected_model import Provenance

SHA_A, SHA_B = "a" * 64, "b" * 64


@contextmanager
def gc_only_in_the_main_thread():
    """Testes com threads criam lixo; se o coletor rodar numa delas e apanhar um objeto Tk
    esquecido por outro teste, o Tcl aborta o processo inteiro (`Tcl_AsyncDelete`)."""
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        gc.enable()
        gc.collect()


def provenance(script="SCRIPT_1.SQL", line=1, certain=True):
    return Provenance("v", script, 1, SHA_A, 1, line, "add_column", certain)


def obj(table, column, status, differences=(), notes=(), script="SCRIPT_1.SQL"):
    return ObjectResult("coluna" if column else "tabela", table, column, status, tuple(differences), tuple(notes), provenance(script))


def version_audit(version="48.2.30.72", objects=(), files=(), database=None, error=""):
    audit = VersionAudit(version, database or f"BANCO_{version}", server_version="8.0.36", scripts_total=len(files), error=error)
    if not error:
        audit.comparison = ComparisonResult(version, audit.database, "8.0.36", list(objects))
    audit.files = [ScriptRecord("v", name, number, sha, 10, True, False) for name, number, sha in files]
    return audit


def make_run(*audits, day=1, user="felipe", cancelled=False):
    started = datetime(2026, 11, day, 10, 0, 0)
    return AuditRun(started, user, Path("C:/versoes/379.48.2.30"), list(audits), started, cancelled)


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "sqlaudit.db"
        self.store = AuditStore(self.path)

    def count(self, table):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class SaveAndLoadTests(StoreTestCase):
    def sample(self, day=1):
        return make_run(
            version_audit(
                objects=[obj("CLIENTES", "", "COMPATIVEL"), obj("CLIENTES", "EMAIL", "DIVERGENTE", [Difference("tipo", "varchar(200)", "varchar(100)")], ["nota"])],
                files=[("SCRIPT_1.SQL", 1, SHA_A), ("SCRIPT_2.SQL", 2, SHA_B)],
            ),
            version_audit("48.2.30.74.150", error="sem conexao"),
            day=day,
        )

    def test_roundtrip_matches_the_report_rows(self):
        run = self.sample()
        run_id = self.store.save_run(run)
        self.assertEqual(self.store.load_objects(run_id), report.object_rows(run))
        files = self.store.load_files(run_id)
        self.assertEqual([(f["script"], f["sha256"], f["numero"]) for f in files], [("SCRIPT_1.SQL", SHA_A, 1), ("SCRIPT_2.SQL", SHA_B, 2)])

    def test_list_runs_newest_first_with_versions_and_summary(self):
        first = self.store.save_run(self.sample(day=1))
        second = self.store.save_run(self.sample(day=2))
        runs = self.store.list_runs()
        self.assertEqual([r.id for r in runs], [second, first])
        self.assertEqual(runs[0].versions, ("48.2.30.72", "48.2.30.74.150"))
        self.assertEqual((runs[0].user, runs[0].started), ("felipe", "2026-11-02T10:00:00"))
        self.assertIn("1 divergente", runs[0].summary)
        self.assertIn("1 com erro", runs[0].summary)

    def test_version_filter_and_version_results(self):
        run_id = self.store.save_run(self.sample())
        self.assertEqual(len(self.store.load_objects(run_id, "48.2.30.72")), 2)
        self.assertEqual(self.store.load_objects(run_id, "48.2.30.74.150"), [])
        results = self.store.version_results(run_id)
        self.assertEqual(results["48.2.30.72"][2], 2)
        self.assertEqual(results["48.2.30.74.150"][1], "sem conexao")

    def test_cancelled_run_is_saved_and_marked(self):
        run_id = self.store.save_run(make_run(version_audit(objects=[obj("T", "", "COMPATIVEL")]), cancelled=True))
        self.assertIn("cancelada", self.store.get_run(run_id).summary)

    def test_retention_keeps_only_the_latest_runs_and_cascades(self):
        ids = [self.store.save_run(self.sample(day=d), keep=3) for d in range(1, 7)]
        self.assertEqual([r.id for r in self.store.list_runs()], ids[:2:-1])
        self.assertEqual(self.count("audit_run"), 3)
        self.assertEqual(self.count("audit_version"), 6)
        self.assertEqual(self.count("audit_object"), 6)
        self.assertEqual(self.count("audit_file"), 6)

    def test_delete_run_and_persistence_across_instances(self):
        run_id = self.store.save_run(self.sample())
        self.assertEqual(AuditStore(self.path).get_run(run_id).id, run_id)
        self.store.delete_run(run_id)
        self.assertEqual(self.store.list_runs(), [])
        self.assertEqual(self.count("audit_object"), 0)

    def test_unicode_and_empty_run(self):
        run_id = self.store.save_run(make_run(version_audit(objects=[obj("AÇÃO", "NÚMERO", "AUSENTE")])))
        self.assertEqual(self.store.load_objects(run_id)[0]["tabela"], "AÇÃO")
        empty = self.store.save_run(make_run())
        self.assertEqual(self.store.load_objects(empty), [])

    def test_newer_schema_is_refused_instead_of_corrupted(self):
        self.store.save_run(self.sample())
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("PRAGMA user_version = 99")
        with self.assertRaises(RuntimeError):
            self.store.list_runs()

    def test_default_path_follows_the_data_directory(self):
        with patch.dict("os.environ", {"VARREDURA_SISTEMA_DATA_DIR": self._tmp.name}):
            self.assertEqual(audit_store.default_path(), Path(self._tmp.name) / "sqlaudit.db")

    def test_missing_run_in_compare_is_an_error(self):
        with self.assertRaises(LookupError):
            self.store.compare(1, 2)


class CompareTests(StoreTestCase):
    def save(self, objects, files=(), day=1, **kwargs):
        return self.store.save_run(make_run(version_audit(objects=objects, files=files, **kwargs), day=day))

    def test_worse_better_new_removed_and_detail_changes(self):
        old = self.save([
            obj("T", "A", "COMPATIVEL"), obj("T", "B", "AUSENTE"), obj("T", "C", "COMPATIVEL"),
            obj("T", "D", "DIVERGENTE", [Difference("tipo", "int", "bigint")]), obj("T", "IGUAL", "COMPATIVEL"),
        ])
        new = self.save([
            obj("T", "A", "DIVERGENTE", [Difference("tipo", "int", "text")]), obj("T", "B", "COMPATIVEL"),
            obj("T", "D", "DIVERGENTE", [Difference("nulabilidade", "NOT NULL", "NULL")]), obj("T", "IGUAL", "COMPATIVEL"),
            obj("T", "E", "AUSENTE"),
        ], day=2)
        diff = self.store.compare(old, new)
        kinds = {c.label: c.kind for c in diff.changes}
        self.assertEqual(kinds, {"T.A": "piorou", "T.B": "melhorou", "T.C": "removido", "T.D": "detalhe", "T.E": "novo"})
        self.assertNotIn("T.IGUAL", kinds)
        self.assertEqual(diff.counts(), {"piorou": 1, "melhorou": 1, "removido": 1, "detalhe": 1, "novo": 1})
        worse = next(c for c in diff.changes if c.label == "T.A")
        self.assertEqual((worse.old_status, worse.new_status), ("COMPATIVEL", "DIVERGENTE"))
        self.assertIn("esperado int, encontrado text", worse.new_detail)

    def test_regression_is_listed_before_improvements(self):
        old = self.save([obj("T", "A", "AUSENTE"), obj("T", "B", "COMPATIVEL")])
        new = self.save([obj("T", "A", "COMPATIVEL"), obj("T", "B", "AUSENTE")], day=2)
        self.assertEqual([c.kind for c in self.store.compare(old, new).changes], ["piorou", "melhorou"])

    def test_same_state_means_no_changes(self):
        objects = [obj("T", "A", "COMPATIVEL")]
        diff = self.store.compare(self.save(objects), self.save(objects, day=2))
        self.assertEqual((diff.changes, diff.script_changes), ([], []))

    def test_script_changes_use_the_hash_not_the_file_date(self):
        old = self.save([obj("T", "A", "COMPATIVEL")], files=[("SCRIPT_1.SQL", 1, SHA_A), ("SCRIPT_2.SQL", 2, SHA_A), ("SCRIPT_3.SQL", 3, SHA_A)])
        new = self.save([obj("T", "A", "COMPATIVEL")], files=[("SCRIPT_1.SQL", 1, SHA_A), ("SCRIPT_2.SQL", 2, SHA_B), ("SCRIPT_4.SQL", 4, SHA_A)], day=2)
        changes = {(c.kind, c.script) for c in self.store.compare(old, new).script_changes}
        self.assertEqual(changes, {("alterado", "SCRIPT_2.SQL"), ("removido", "SCRIPT_3.SQL"), ("adicionado", "SCRIPT_4.SQL")})
        altered = next(c for c in self.store.compare(old, new).script_changes if c.kind == "alterado")
        self.assertEqual((altered.old_sha256, altered.new_sha256), (SHA_A, SHA_B))

    def test_version_without_result_is_not_compared_object_by_object(self):
        old = self.store.save_run(make_run(version_audit(error="sem rede")))
        new = self.save([obj("T", "A", "AUSENTE")], day=2)
        diff = self.store.compare(old, new)
        self.assertEqual(diff.changes, [])  # nao inventa 'novo' para tudo
        self.assertTrue(any("sem resultado" in n for n in diff.notes))

    def test_different_database_and_versions_in_only_one_run_are_noted(self):
        old = self.store.save_run(make_run(version_audit(objects=[obj("T", "A", "COMPATIVEL")], database="BANCO_X"), version_audit("outra", objects=[obj("T", "A", "COMPATIVEL")])))
        new = self.save([obj("T", "A", "COMPATIVEL")], day=2, database="BANCO_Y")
        notes = " ".join(self.store.compare(old, new).notes)
        self.assertIn("banco diferente", notes)
        self.assertIn("so na anterior", notes)

    def test_version_filter(self):
        two = lambda day, status: self.store.save_run(make_run(
            version_audit("v1", objects=[obj("T", "A", status)]), version_audit("v2", objects=[obj("T", "A", status)]), day=day))
        old, new = two(1, "COMPATIVEL"), two(2, "AUSENTE")
        self.assertEqual({c.version for c in self.store.compare(old, new).changes}, {"v1", "v2"})
        self.assertEqual({c.version for c in self.store.compare(old, new, version="v2").changes}, {"v2"})

    def test_case_differences_in_names_match_the_same_object(self):
        old = self.save([obj("Clientes", "Email", "COMPATIVEL")])
        new = self.save([obj("CLIENTES", "EMAIL", "AUSENTE")], day=2)
        self.assertEqual([c.kind for c in self.store.compare(old, new).changes], ["piorou"])


class SharedLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.control = Path(self._tmp.name) / "controle"

    def test_format_parse_roundtrip_and_pipe_is_sanitized(self):
        event = shared_log.SharedEvent("2026-11-06 10:00:00", "ana", "48.2.30.72", "fork", "base=a|b; fork=5\nx")
        line = shared_log.format_line(event)
        self.assertEqual(line.count(" | "), 4)
        self.assertNotIn("\n", line)
        parsed = shared_log.parse_line(line)
        self.assertEqual((parsed.user, parsed.version, parsed.event), ("ana", "48.2.30.72", "fork"))
        self.assertIsNone(shared_log.parse_line("lixo"))
        self.assertIsNone(shared_log.parse_line("a | b | | d"))

    def test_append_creates_the_folder_and_never_rewrites(self):
        first = shared_log.fork_event("ana", "v1", "v0", 5)
        second = shared_log.fork_event("bia", "v2", "v1", 9)
        shared_log.append_events([first], self.control)
        shared_log.append_events([second], self.control)
        events = shared_log.read_events(self.control)
        self.assertEqual([e.user for e in events], ["ana", "bia"])
        shared_log.append_events([], self.control)
        self.assertEqual(len(shared_log.read_events(self.control)), 2)

    def test_missing_file_and_garbage_lines(self):
        self.assertEqual(shared_log.read_events(self.control), [])
        shared_log.append_events([shared_log.fork_event("ana", "v1", "v0", 5)], self.control)
        with shared_log.log_path(self.control).open("a", encoding="utf-8") as handle:
            handle.write("linha quebrada\n\n")
        self.assertEqual(len(shared_log.read_events(self.control)), 1)

    def test_latest_fork_wins_and_empty_fork_is_none(self):
        shared_log.append_events([
            shared_log.fork_event("ana", "v1", "v0", 5, "BANCO_1"),
            shared_log.fork_event("bia", "v1", "v0", 9, "BANCO_1"),
            shared_log.fork_event("ana", "v2", "v1", None),
            shared_log.SharedEvent("2026-11-06 10:00:00", "x", "v3", "auditoria", "banco=B"),
        ], self.control)
        forks = shared_log.latest_forks(shared_log.read_events(self.control))
        self.assertEqual((forks["v1"].fork, forks["v1"].user, forks["v1"].base, forks["v1"].database), (9, "bia", "v0", "BANCO_1"))
        self.assertIsNone(forks["v2"].fork)
        self.assertNotIn("v3", forks)

    def test_audit_event_has_counts_and_never_sql_or_secrets(self):
        audit = version_audit(objects=[obj("T", "A", "AUSENTE"), obj("T", "B", "COMPATIVEL"), obj("T", "C", "COMPATIVEL")], files=[("S.SQL", 1, SHA_A)])
        event = shared_log.audit_event("ana", audit)
        self.assertEqual(event.event, "auditoria")
        self.assertIn("objetos=ausente:1,compativel:2", event.detail)
        self.assertIn("mysql=8.0.36", event.detail)
        failed = shared_log.audit_event("ana", version_audit(error="falhou " + "x" * 500))
        self.assertIn("erro=falhou", failed.detail)
        self.assertLess(len(failed.detail), 300)

    def test_permission_error_is_retried_then_raised(self):
        real_open = Path.open
        calls = []

        def flaky(self_path, *args, **kwargs):
            if self_path.name == shared_log.FILE_NAME and len(calls) < 2:
                calls.append(1)
                raise PermissionError("em uso")
            return real_open(self_path, *args, **kwargs)

        with patch.object(Path, "open", flaky), patch("app.sqlaudit.shared_log.time.sleep"):
            shared_log.append_events([shared_log.fork_event("ana", "v1", "v0", 5)], self.control)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(shared_log.read_events(self.control)), 1)

        def always(self_path, *args, **kwargs):
            if self_path.name == shared_log.FILE_NAME:
                raise PermissionError("em uso")
            return real_open(self_path, *args, **kwargs)

        with patch.object(Path, "open", always), patch("app.sqlaudit.shared_log.time.sleep"):
            with self.assertRaises(PermissionError):
                shared_log.append_events([shared_log.fork_event("ana", "v1", "v0", 5)], self.control)

    def test_concurrent_writers_never_interleave_lines(self):
        def writer(name):
            for i in range(25):
                shared_log.append_events([shared_log.fork_event(name, f"v{i}", "base", i)], self.control)

        threads = [threading.Thread(target=writer, args=(f"op{n}",)) for n in range(4)]
        with gc_only_in_the_main_thread():
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        events = shared_log.read_events(self.control)
        self.assertEqual(len(events), 100)
        self.assertTrue(all(e.event == "fork" and e.detail.startswith("base=base; fork=") for e in events))

    def test_concurrent_processes_never_lose_or_mix_lines(self):
        """Processos de verdade, como varios operadores: no Windows exercita o msvcrt real."""
        import os
        import subprocess
        import sys

        code = (
            "import sys; from pathlib import Path; from app.sqlaudit import shared_log\n"
            "name, folder = sys.argv[1], Path(sys.argv[2])\n"
            "for i in range(30):\n"
            "    shared_log.append_events([shared_log.fork_event(name, f'v{i}', 'base', i)], folder)\n"
        )
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent), VARREDURA_SEM_AUDITORIA_COMPARTILHADA="1")
        processes = [
            subprocess.Popen([sys.executable, "-c", code, f"op{n}", str(self.control)], env=env, stderr=subprocess.PIPE)
            for n in range(4)
        ]
        errors = [process.communicate(timeout=120)[1].decode("utf-8", "replace") for process in processes]
        self.assertEqual([process.returncode for process in processes], [0] * 4, errors)
        events = shared_log.read_events(self.control)
        self.assertEqual(len(events), 120)
        for name in ("op0", "op1", "op2", "op3"):
            self.assertEqual(sorted(int(shared_log._fields(e.detail)["fork"]) for e in events if e.user == name), list(range(30)))
        self.assertTrue(all(e.event == "fork" and e.detail.startswith("base=base; fork=") for e in events))

    def test_a_writer_holding_the_lock_does_not_block_readers(self):
        shared_log.append_events([shared_log.fork_event("ana", "v1", "v0", 1)], self.control)
        with shared_log.log_path(self.control).open("ab") as handle:
            shared_log._lock(handle)
            try:
                self.assertEqual(len(shared_log.read_events(self.control)), 1)  # a trava fica fora da area dos dados
            finally:
                shared_log._unlock(handle)

    def test_the_lock_is_released_even_when_the_write_fails(self):
        shared_log.append_events([shared_log.fork_event("ana", "v1", "v0", 1)], self.control)
        with patch("app.sqlaudit.shared_log.os.SEEK_END", "invalido"):
            with self.assertRaises(Exception):
                shared_log.append_events([shared_log.fork_event("ana", "v2", "v0", 2)], self.control)
        shared_log.append_events([shared_log.fork_event("ana", "v3", "v0", 3)], self.control)  # nao ficou travado
        self.assertEqual([e.version for e in shared_log.read_events(self.control)], ["v1", "v3"])


class FakeMsvcrt:
    """msvcrt falso com exclusao mutua real, para exercitar o ramo do Windows em qualquer sistema."""

    LK_UNLCK, LK_LOCK, LK_NBLCK = 0, 1, 2

    def __init__(self, busy_first=0):
        self._guard = threading.Lock()
        self._held = threading.Lock()
        self.calls = []
        self.offsets = []
        self.busy_first = busy_first

    def locking(self, fd, mode, nbytes):
        with self._guard:
            self.calls.append(mode)
            if mode == self.LK_UNLCK:
                self._held.release()
                return
            if self.busy_first > 0:
                self.busy_first -= 1
                raise PermissionError(13, "Permission denied")
            if not self._held.acquire(blocking=False):
                raise PermissionError(13, "Permission denied")


class WindowsBranchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.control = Path(self._tmp.name) / "controle"

    def windows(self, fake):
        import sys

        offsets = fake.offsets
        real_lock = shared_log._lock

        def tracking_lock(handle):
            real_lock(handle)
            offsets.append(handle.tell())

        stack = [
            patch.object(shared_log, "_WINDOWS", True),
            patch.dict(sys.modules, {"msvcrt": fake}),
            patch.object(shared_log, "_lock", tracking_lock),
            patch("app.sqlaudit.shared_log.time.sleep"),
        ]
        for item in stack:
            item.start()
            self.addCleanup(item.stop)

    def test_threads_never_overwrite_each_other_and_lock_beyond_the_data(self):
        fake = FakeMsvcrt()
        self.windows(fake)

        def writer(name):
            for i in range(25):
                shared_log.append_events([shared_log.fork_event(name, f"v{i}", "base", i)], self.control)

        threads = [threading.Thread(target=writer, args=(f"op{n}",)) for n in range(4)]
        with gc_only_in_the_main_thread():
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(len(shared_log.read_events(self.control)), 100)
        self.assertEqual(fake.calls.count(FakeMsvcrt.LK_UNLCK), 100)  # toda trava foi liberada
        self.assertGreaterEqual(min(fake.offsets), 1 << 30)  # trava muito alem de qualquer dado real: leitores nao sao bloqueados

    def test_contention_is_retried_until_the_lock_is_free(self):
        fake = FakeMsvcrt(busy_first=3)
        self.windows(fake)
        shared_log.append_events([shared_log.fork_event("ana", "v1", "v0", 1)], self.control)
        self.assertEqual(len(shared_log.read_events(self.control)), 1)
        self.assertGreaterEqual(fake.calls.count(FakeMsvcrt.LK_NBLCK), 4)

    def test_a_lock_that_never_frees_times_out_once_and_is_not_retried_by_the_open_loop(self):
        fake = FakeMsvcrt(busy_first=10**9)
        self.windows(fake)
        ticks = iter(range(0, 10**6))
        with patch("app.sqlaudit.shared_log.time.monotonic", lambda: next(ticks) * 0.5):
            with self.assertRaises(shared_log.LockTimeout) as raised:
                shared_log.append_events([shared_log.fork_event("ana", "v1", "v0", 1)], self.control)
        self.assertIsInstance(raised.exception, OSError)  # a janela ja trata OSError
        self.assertEqual(shared_log.read_events(self.control), [])
        self.assertLess(fake.calls.count(FakeMsvcrt.LK_NBLCK), 100)  # esperou so um ciclo de timeout


if __name__ == "__main__":
    unittest.main()
