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


class DashboardCardsTests(unittest.TestCase):
    def projects(self):
        return [
            project(folder_name="379.1"),
            project(folder_name="379.2_CLOUD"),
            project(folder_name="379.3", status="Ausente: libAutban.dll"),
            project(folder_name="379.4", status="FileVersion esperado x; encontrado y"),
            project(folder_name="379.5", status="ZIP invalido: libNfe.zip"),
        ]

    def test_cards_show_value_and_useful_detail(self):
        cards = project_view.dashboard_cards(
            self.projects(), closing_running=1, closing_queued=2, zip_alerts=1, pending_scripts=4
        )

        self.assertEqual(cards["total"], ("5", "4 Local • 1 Cloud", True))
        self.assertEqual(cards["ok"], ("2", "40% do total", True))
        self.assertEqual(cards["pending"], ("3", "1 versão • 1 ausente • 1 ZIP", True))
        self.assertEqual(cards["closing"], ("3", "1 rodando • 2 na fila", True))
        self.assertEqual(cards["zip_alert"], ("1", "menos ZIPs que o máximo", True))
        self.assertEqual(cards["scripts"], ("4", "clique para ver ›", True))

    def test_zero_alerts_are_not_emphasized(self):
        cards = project_view.dashboard_cards([project()], pending_scripts=0)

        self.assertEqual(cards["pending"], ("0", "nenhuma pendência", False))
        self.assertEqual(cards["closing"], ("0", "nenhum agora", False))
        self.assertEqual(cards["zip_alert"], ("0", "nenhum alerta", False))
        self.assertEqual(cards["scripts"], ("0", "nenhum pendente", False))
        # Total e OK sempre ficam com cor.
        self.assertTrue(cards["total"][2])
        self.assertTrue(cards["ok"][2])

    def test_loading_and_unknown_states_do_not_show_zero(self):
        cards = project_view.dashboard_cards([], loaded=False)
        for key in ("total", "ok", "pending", "zip_alert"):
            self.assertEqual(cards[key][:2], ("…", "analisando…"))
        self.assertEqual(cards["scripts"], ("—", "verificando…", False))

        offline = project_view.dashboard_cards([], scripts_error="Pasta de scripts nao encontrada")
        self.assertEqual(offline["scripts"], ("—", "sem acesso à pasta", False))
        self.assertEqual(offline["ok"], ("0", "nenhum projeto", True))

    def test_every_project_card_maps_to_a_filter(self):
        self.assertEqual(
            project_view.CARD_FILTERS,
            {
                "total": "Todos",
                "ok": "Somente OK",
                "pending": "Com erro",
                "closing": "Em fechamento",
                "zip_alert": "Alerta de ZIPs",
            },
        )


if __name__ == "__main__":
    unittest.main()
