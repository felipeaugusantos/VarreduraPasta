import unittest

from app.theme import fitted_dimension


class FittedDimensionTests(unittest.TestCase):
    def test_grows_when_content_needs_a_little_more(self):
        # Caso real: confirmacao do Copiar Local pedia 460 px numa janela de 430.
        self.assertEqual(fitted_dimension(430, 460, 1000), 460)

    def test_keeps_size_when_content_fits(self):
        self.assertEqual(fitted_dimension(860, 790, 1900), 860)

    def test_keeps_size_for_wide_tables_that_scroll(self):
        # Caso real: tabela da Auditoria pedia 1880 px numa janela de 1180.
        self.assertEqual(fitted_dimension(1180, 1880, 1900), 1180)

    def test_never_exceeds_screen(self):
        self.assertEqual(fitted_dimension(700, 800, 760), 760)
        self.assertEqual(fitted_dimension(1200, 1200, 1000), 1000)


if __name__ == "__main__":
    unittest.main()
