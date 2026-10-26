import time
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import logging_utils, shared_audit
from app.audit import parse_log_entries
from app.shared_audit import SharedAuditWriter, list_shared_log_files, shared_log_name


def line(now, text):
    return f"[{now:%Y-%m-%d %H:%M:%S}] {text}\n"


class SharedAuditWriterTests(unittest.TestCase):
    def setUp(self):
        self._temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        self.directory = Path(self._temp.name) / "_CONTROLE_VARREDURA"

    def make_writer(self, provider=None, retry_seconds=0.05):
        writer = SharedAuditWriter(provider or (lambda: self.directory), retry_seconds=retry_seconds)
        self.addCleanup(writer.stop)
        return writer

    def test_appends_lines_from_several_operators_to_monthly_file(self):
        writer = self.make_writer()
        october = datetime(2026, 10, 26, 9, 14, 3)
        november = datetime(2026, 11, 2, 8, 0, 0)

        writer.enqueue(october, line(october, "Fechamento Local concluido | usuario=joao.silva | projeto=379.1"))
        writer.enqueue(october, line(october, "Copiar Local concluido | usuario=ana.souza | projeto=379.2"))
        writer.enqueue(november, line(november, "Fechamento Cloud iniciado | usuario=joao.silva | projeto=379.3"))
        self.assertTrue(writer.flush(5))

        files = list_shared_log_files(self.directory)
        self.assertEqual([path.name for path in files], ["auditoria-2026-10.log", "auditoria-2026-11.log"])
        entries = parse_log_entries(files[0].read_text(encoding="utf-8"))
        self.assertEqual([entry["usuario"] for entry in entries], ["joao.silva", "ana.souza"])
        self.assertEqual(shared_log_name(november), "auditoria-2026-11.log")

    def test_unavailable_folder_keeps_lines_and_writes_them_in_order_later(self):
        blocker = Path(self._temp.name) / "bloqueio"
        blocker.write_text("arquivo no lugar da pasta", encoding="utf-8")
        target = {"directory": blocker / "sub"}  # mkdir falha: pai e um arquivo
        writer = self.make_writer(provider=lambda: target["directory"])
        now = datetime(2026, 10, 26, 10, 0, 0)

        for number in range(3):
            writer.enqueue(now, line(now, f"Acao {number} ok | usuario=ana"))
        self.assertFalse(writer.flush(0.4))  # rede fora: nada gravado, nada perdido

        target["directory"] = self.directory  # a rede volta
        self.assertTrue(writer.flush(5))

        content = (self.directory / "auditoria-2026-10.log").read_text(encoding="utf-8")
        self.assertEqual([entry["acao"] for entry in parse_log_entries(content)], ["Acao 0", "Acao 1", "Acao 2"])

    def test_queue_is_bounded_when_the_share_stays_down(self):
        writer = self.make_writer(provider=lambda: Path(self._temp.name) / "x" / "y", retry_seconds=60)
        blocker = Path(self._temp.name) / "x"
        blocker.write_text("x", encoding="utf-8")
        now = datetime(2026, 10, 26, 10, 0, 0)

        for number in range(shared_audit.MAX_PENDING + 500):
            writer.enqueue(now, line(now, f"Acao {number} ok"))
        time.sleep(0.2)

        self.assertLessEqual(len(writer._pending), shared_audit.MAX_PENDING)

    def test_enqueue_never_blocks_the_caller_on_a_slow_share(self):
        def slow_directory():
            time.sleep(2)
            return self.directory

        writer = self.make_writer(provider=slow_directory)
        now = datetime(2026, 10, 26, 10, 0, 0)

        start = time.monotonic()
        writer.enqueue(now, line(now, "Acao ok"))
        self.assertLess(time.monotonic() - start, 0.5)


class WriteLogIntegrationTests(unittest.TestCase):
    def test_every_audit_line_is_forwarded_to_the_shared_audit(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            original = logging_utils.LOG_FILE
            logging_utils.LOG_FILE = Path(temp) / "local.log"
            self.addCleanup(setattr, logging_utils, "LOG_FILE", original)
            with patch("app.logging_utils.shared_audit.enqueue") as enqueue:
                logging_utils.write_log("Fechamento Local concluido | usuario=ana | projeto=379.1")

            local_line = (Path(temp) / "local.log").read_text(encoding="utf-8")

        sent_now, sent_line = enqueue.call_args.args
        self.assertEqual(sent_line, local_line)
        self.assertIsInstance(sent_now, datetime)

    def test_disabled_in_tests_so_nothing_reaches_the_network(self):
        writer = shared_audit._default_writer
        before = len(writer._pending)
        shared_audit.enqueue(datetime.now(), "[2026-10-26 10:00:00] Acao ok\n")
        self.assertEqual(len(writer._pending), before)
        self.assertIsNone(writer._thread)


if __name__ == "__main__":
    unittest.main()
