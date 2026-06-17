import unittest

from app.audit import filter_log_entries, parse_log_entries, parse_log_line


class AuditParserTests(unittest.TestCase):
    def test_parse_standard_action_log_line(self):
        line = (
            "[2026-06-11 20:31:07] Copiar Cloud concluido | "
            "usuario=felipe.santos | projeto=379.48.2.30.74.150.45.46_CLOUD | "
            "tipo=Cloud | origem=C:\\VERSOES_FECHADAS\\projeto | "
            "destino=\\\\servidor\\destino | bat= | fileversion=48.02.30.74 | "
            "productversion=02.30.74.150 | autcom_mb=218.42 | "
            "motivo=152 arquivo(s) copiado(s)"
        )

        entry = parse_log_line(line)

        self.assertEqual(entry["timestamp"], "2026-06-11 20:31:07")
        self.assertEqual(entry["acao"], "Copiar Cloud")
        self.assertEqual(entry["resultado"], "concluido")
        self.assertEqual(entry["usuario"], "felipe.santos")
        self.assertEqual(entry["projeto"], "379.48.2.30.74.150.45.46_CLOUD")
        self.assertEqual(entry["tipo"], "Cloud")
        self.assertEqual(entry["destino"], r"\\servidor\destino")
        self.assertEqual(entry["fileversion"], "48.02.30.74")
        self.assertEqual(entry["productversion"], "02.30.74.150")
        self.assertEqual(entry["autcom_mb"], "218.42")
        self.assertEqual(entry["motivo"], "152 arquivo(s) copiado(s)")

    def test_parse_unknown_line_keeps_raw_text(self):
        entry = parse_log_line("linha antiga sem padrao")

        self.assertEqual(entry["raw"], "linha antiga sem padrao")
        self.assertEqual(entry["acao"], "")
        self.assertEqual(entry["resultado"], "")

    def test_filter_entries_by_result_and_search_text(self):
        content = "\n".join(
            [
                "[2026-06-11 10:00:00] Copiar Local concluido | projeto=ProjetoA",
                "[2026-06-11 10:01:00] Copiar Cloud bloqueado | projeto=ProjetoB",
                "[2026-06-11 10:02:00] Fechamento Local iniciado | projeto=ProjetoA",
            ]
        )
        entries = parse_log_entries(content)

        filtered = filter_log_entries(entries, "ProjetoA", "concluido")

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["acao"], "Copiar Local")
        self.assertEqual(filtered[0]["resultado"], "concluido")


if __name__ == "__main__":
    unittest.main()
