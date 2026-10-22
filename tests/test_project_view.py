import unittest
from datetime import datetime
from types import SimpleNamespace

from app import project_view


def project(**overrides):
    values = {
        "folder_name": "379.48.2.30.74.150.99",
        "display_file_version": "48.02.30.74",
        "display_product_version": "02.30.74.150",
        "expected_file_version": "48.02.30.74",
        "expected_product_version": "02.30.74.150",
        "display_autcom_size_mb": 50.0,
        "zip_status": "Autcom zip OK",
        "status": "OK",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ProjectViewTests(unittest.TestCase):
    def test_search_and_filter_are_independent_from_tkinter(self):
        item = project(folder_name="379.48.2.30.74.150.99_CLOUD")
        self.assertTrue(project_view.matches_search(item, "48.02"))
        self.assertTrue(project_view.matches_filter(item, "Cloud"))
        self.assertFalse(project_view.matches_filter(item, "Local"))

    def test_summary_groups_pending_statuses(self):
        items = [project(), project(status="Autcom FileVersion incorreto")]
        summary = project_view.summary_text(
            items, 3, datetime(2026, 7, 15, 10, 30, 0)
        )
        self.assertIn("2 exibido(s) de 3 analisado(s)", summary)
        self.assertIn("1 versão incorreta", summary)
        self.assertIn("10:30:00", summary)


class DisplayHelpersTests(unittest.TestCase):
    def test_status_display_has_symbol_per_category(self):
        self.assertEqual(project_view.status_display(project()), "● OK")
        self.assertEqual(
            project_view.status_display(project(status="Ausente: libAutban.dll")),
            "✖ Arquivo ausente",
        )
        self.assertEqual(
            project_view.status_display(project(status="FileVersion esperado x; encontrado y")),
            "▲ Versão incorreta",
        )
        self.assertEqual(
            project_view.status_display(project(status="ZIP invalido: libNfe.zip")),
            "▲ Pendência ZIP",
        )

    def test_version_display_marks_only_mismatch(self):
        self.assertEqual(project_view.version_display("48.02.30.74", "48.02.30.74"), "48.02.30.74")
        self.assertEqual(project_view.version_display("48.02.30.73", "48.02.30.74"), "✗ 48.02.30.73")
        self.assertEqual(project_view.version_display("48.02.30.73", ""), "48.02.30.73")
        self.assertEqual(project_view.version_display(None, "48.02.30.74"), "")

    def test_dashboard_counts(self):
        counts = project_view.dashboard_counts(
            [project(), project(), project(status="Ausente: x")]
        )
        self.assertEqual(counts, {"total": 3, "ok": 2, "pending": 1})


if __name__ == "__main__":
    unittest.main()
