import tempfile
import unittest
from pathlib import Path

from app.sqlaudit import shared_log

ROW = {
    "versao": "48.2.30.74.111", "tipo": "coluna", "tabela": "HYD_CADNFM", "coluna": "NFM_TIPQTD",
    "estado": "DIVERGENTE", "diferencas": "tipo: esperado varchar(15), encontrado varchar(20)",
}


class KnownRegistryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.control = Path(self._tmp.name)

    def write(self, *events):
        shared_log.append_events(list(events), self.control)

    def known(self):
        return shared_log.latest_known(shared_log.read_events(self.control))

    def test_marked_finding_is_found_by_its_key_and_keeps_the_reason(self):
        self.write(shared_log.known_event("ana", ROW, "coluna ja nasceu com 20; sem impacto"))
        known = self.known()
        self.assertEqual(list(known), [shared_log.row_key(ROW)])
        item = known[shared_log.row_key(ROW)]
        self.assertEqual((item.user, item.state, item.reason), ("ana", "DIVERGENTE", "coluna ja nasceu com 20; sem impacto".replace("; ", ", ")))

    def test_key_ignores_case_but_a_changed_finding_is_a_different_key(self):
        self.write(shared_log.known_event("ana", ROW))
        changed = dict(ROW, diferencas="tipo: esperado varchar(15), encontrado varchar(30)")
        lower = dict(ROW, tabela="hyd_cadnfm", coluna="nfm_tipqtd")
        self.assertIn(shared_log.row_key(lower), self.known())
        self.assertNotIn(shared_log.row_key(changed), self.known())
        self.assertNotIn(shared_log.row_key(dict(ROW, versao="48.2.30.72")), self.known())
        self.assertNotIn(shared_log.row_key(dict(ROW, estado="AUSENTE")), self.known())

    def test_last_event_wins_and_unmark_removes(self):
        self.write(shared_log.known_event("ana", ROW, "a"), shared_log.known_event("bia", ROW, "b"))
        self.assertEqual(self.known()[shared_log.row_key(ROW)].user, "bia")
        self.write(shared_log.known_event("ana", ROW, marked=False))
        self.assertEqual(self.known(), {})
        self.write(shared_log.known_event("ana", ROW, "de novo"))
        self.assertEqual(len(self.known()), 1)

    def test_pipes_newlines_and_long_reasons_never_break_the_line(self):
        self.write(shared_log.known_event("ana", ROW, "a | b\nc; d " + "x" * 500))
        events = shared_log.read_events(self.control)
        self.assertEqual(len(events), 1)
        item = self.known()[shared_log.row_key(ROW)]
        self.assertLessEqual(len(item.reason), shared_log.MAX_REASON_CHARS)
        self.assertNotIn("\n", item.reason)

    def test_other_events_are_not_mixed_up_and_garbage_is_ignored(self):
        path = shared_log.log_path(self.control)
        path.write_text("lixo sem separador\n2026-01-01 | x | 1.0 | conhecida | sem assinatura\n", encoding="utf-8")
        self.write(shared_log.fork_event("ana", "48.2.30.74.111", "48.2.30.74.150", 10))
        self.assertEqual(self.known(), {})


if __name__ == "__main__":
    unittest.main()
