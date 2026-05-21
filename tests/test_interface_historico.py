import unittest

import tkinter as tk
from tkinter import ttk
from unittest import mock

from app.core.parser_insert import parsear_insert
from app.ui.main_window import InterfaceInsert
from app.ui.styles import configurar_estilos


class InterfaceHistoricoTest(unittest.TestCase):
    def setUp(self):
        self.raiz = tk.Tk()
        self.raiz.withdraw()
        estilo = ttk.Style(self.raiz)
        try:
            estilo.theme_use("clam")
        except tk.TclError:
            pass
        configurar_estilos(estilo)
        self.app = InterfaceInsert(self.raiz)
        self.app.declaracao = parsear_insert(
            "INSERT INTO clientes (id, nome) VALUES (1, 'Ana'), (2, 'Bia');"
        )
        self.app.atualizar_total_inserts()
        self.app.atualizar_resumo()
        self.app.atualizar_arvore()

    def tearDown(self):
        self.raiz.destroy()

    def test_desfazer_e_refazer_alteracao(self):
        self.app._registrar_estado_para_desfazer()
        self.app.declaracao.linhas[0][1] = "'Clara'"

        self.app.desfazer()
        self.assertEqual("'Ana'", self.app.declaracao.linhas[0][1])

        self.app.refazer()
        self.assertEqual("'Clara'", self.app.declaracao.linhas[0][1])

    def test_novo_processamento_limpa_historico(self):
        self.app._registrar_estado_para_desfazer()
        self.assertTrue(self.app.historico_desfazer)

        self.app._limpar_historico()

        self.assertFalse(self.app.historico_desfazer)
        self.assertFalse(self.app.historico_refazer)

    def test_gerar_insert_bloqueia_linhas_invalidas_e_limpa_saida(self):
        self.app.declaracao = parsear_insert("INSERT INTO clientes (id, nome) VALUES (1);")
        self.app.txt_saida.configure(state="normal")
        self.app.txt_saida.insert("1.0", "SQL ANTIGO")
        self.app.txt_saida.configure(state="disabled")

        with mock.patch("app.ui.main_window.messagebox.showwarning") as aviso:
            self.app.gerar_insert()

        self.assertEqual("", self.app.txt_saida.get("1.0", "end").strip())
        mensagem = aviso.call_args.args[1]
        self.assertIn("Total de linhas problematicas: 1", mensagem)
        self.assertIn("Amostra dos indices: 0", mensagem)
        self.assertIn("SQL nao foi gerado por seguranca", mensagem)

    def test_gerar_insert_bloqueia_sem_linhas_e_limpa_saida(self):
        self.app.declaracao.linhas.clear()
        self.app.txt_saida.configure(state="normal")
        self.app.txt_saida.insert("1.0", "SQL ANTIGO")
        self.app.txt_saida.configure(state="disabled")

        with mock.patch("app.ui.main_window.messagebox.showwarning") as aviso:
            self.app.gerar_insert()

        self.assertEqual("", self.app.txt_saida.get("1.0", "end").strip())
        self.assertIn("Nao ha linhas para gerar SQL", aviso.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
