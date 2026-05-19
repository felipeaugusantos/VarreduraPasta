import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import re

from parser_insert import parsear_insert, DeclaracaoInsert


class InterfaceInsert(ttk.Frame):
    def __init__(self, mestre):
        super().__init__(mestre)
        self.mestre = mestre
        self.declaracao: DeclaracaoInsert | None = None
        self.linha_atual: int = 0  # sempre 0 agora (sem navegação)

        # variáveis de interface
        self.var_total_inserts = tk.StringVar(value="Total de INSERTs detectados: 0")

        # pesquisa/filtro
        self.var_pesquisa = tk.StringVar(value="")
        self.filtro_texto: str = ""

        # editor inline (Entry sobre a célula)
        self._editor_entry: tk.Entry | None = None
        self._editor_iid: str | None = None
        self._editor_col_idx: int | None = None

        self._montar_interface()
        self._criar_menu()

        # aplica filtro automaticamente ao digitar
        self.var_pesquisa.trace_add("write", lambda *_: self.aplicar_filtro())

    # =========================
    # Menu Ajuda
    # =========================
    def _criar_menu(self):
        barra_menu = tk.Menu(self.mestre)

        menu_ajuda = tk.Menu(barra_menu, tearoff=0)
        menu_ajuda.add_command(label="Sobre", command=self.mostrar_sobre)
        menu_ajuda.add_command(label="Manual", command=self.mostrar_manual)

        barra_menu.add_cascade(label="Ajuda", menu=menu_ajuda)
        self.mestre.config(menu=barra_menu)

    def mostrar_sobre(self):
        texto = (
            "Nome: Ajuste de Insert\n"
            "Versão: 1.0.0.0\n"
            "Criado por: Felipe Augusto dos Santos"
        )
        messagebox.showinfo("Sobre", texto)

    def mostrar_manual(self):
        manual = (
            "MANUAL — AJUSTE DE INSERT\n\n"
            "1) Inserir o SQL de entrada\n"
            "   • Cole um ou mais INSERTs na área 'Entrada de INSERT' OU\n"
            "     use 'Abrir Arquivo .sql/.txt'.\n\n"
            "2) Processar\n"
            "   • Clique em 'Processar INSERT'.\n"
            "   • O programa detecta automaticamente quantos INSERTs existem\n"
            "     no texto e mostra o total logo abaixo.\n\n"
            "3) Visualizar Campos e Valores\n"
            "   • A lista mostra os campos e valores da PRIMEIRA linha do INSERT.\n"
            "   • Se o SQL tiver várias linhas, elas são mantidas internamente.\n\n"
            "4) Pesquisar na lista\n"
            "   • Digite no campo 'Pesquisar (campo ou valor)'.\n"
            "   • A lista filtra automaticamente conforme você digita.\n"
            "   • Para voltar ao normal, clique em 'Limpar Pesquisa'.\n\n"
            "5) Editar valores\n"
            "   • Dê duplo clique em um valor na coluna 'Valor'.\n"
            "   • Regras ao salvar:\n"
            "       - Se deixar vazio → vira NULL.\n"
            "       - Se não for número e não estiver entre aspas → vira texto com aspas.\n"
            "         Ex.: abc → 'abc'\n"
            "       - Aspas internas são escapadas automaticamente.\n"
            "         Ex.: O'Reilly → 'O''Reilly'\n\n"
            "6) Excluir colunas\n"
            "   • Selecione uma ou mais linhas (colunas) na lista.\n"
            "   • Clique em 'Excluir Coluna(s)'.\n"
            "   • A coluna e o valor correspondente são removidos de TODAS as linhas.\n\n"
            "7) Excluir a linha atual\n"
            "   • Clique em 'Excluir Linha Atual' para remover a primeira linha (linha 0).\n\n"
            "8) Gerar novo INSERT\n"
            "   • Clique em 'Gerar Novo INSERT'.\n"
            "   • A saída SEMPRE é limpa antes de gerar de novo.\n"
            "   • Se o texto original tinha vários INSERTs, a ferramenta gera um\n"
            "     bloco separado para cada INSERT.\n\n"
            "9) Copiar e executar\n"
            "   • Copie o SQL gerado na área 'INSERT gerado' e execute no banco.\n\n"
            "Dica:\n"
            "   • Se precisar inserir funções sem aspas (ex.: NOW()), digite já no formato\n"
            "     correto e com cuidado, pois a regra de texto coloca aspas se não detectar número."
        )

        janela = tk.Toplevel(self.mestre)
        janela.title("Manual - Ajuste de Insert")
        janela.geometry("760x520")
        janela.transient(self.mestre)

        frame = ttk.Frame(janela, padding=10)
        frame.pack(fill="both", expand=True)

        txt = tk.Text(frame, wrap="word")
        txt.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        scroll.pack(side="right", fill="y")
        txt.configure(yscrollcommand=scroll.set)

        txt.insert("1.0", manual)
        txt.configure(state="disabled")

        ttk.Button(janela, text="Fechar", command=janela.destroy).pack(pady=(0, 10))

    # =========================
    # Montagem da interface
    # =========================
    def _montar_interface(self):
        self.mestre.title("Ajuste de Insert")
        self.pack(fill="both", expand=True, padx=10, pady=10)

        # --- Área superior: entrada ---
        quadro_entrada = ttk.LabelFrame(self, text="Entrada de INSERT")
        quadro_entrada.pack(fill="both", expand=False)

        self.txt_entrada = tk.Text(quadro_entrada, height=6, wrap="word")
        self.txt_entrada.pack(fill="both", expand=True, padx=8, pady=6)

        quadro_botoes_entrada = ttk.Frame(quadro_entrada)
        quadro_botoes_entrada.pack(fill="x", padx=8, pady=(0, 4))

        ttk.Button(
            quadro_botoes_entrada,
            text="Colar do Clipboard",
            command=self.colar_clipboard
        ).pack(side="left", padx=(0, 6))

        ttk.Button(
            quadro_botoes_entrada,
            text="Abrir Arquivo .sql/.txt",
            command=self.abrir_arquivo
        ).pack(side="left")

        ttk.Button(
            quadro_botoes_entrada,
            text="Processar INSERT",
            command=self.processar_insert
        ).pack(side="right")

        # --- label total inserts detectados ---
        lbl_total = ttk.Label(
            quadro_entrada,
            textvariable=self.var_total_inserts
        )
        lbl_total.pack(anchor="w", padx=10, pady=(0, 6))

        # --- Meio: ações (sem Linha/setas) ---
        quadro_meio = ttk.Frame(self)
        quadro_meio.pack(fill="x", pady=8)

        ttk.Button(
            quadro_meio,
            text="Excluir Coluna(s)",
            command=self.excluir_colunas_selecionadas
        ).pack(side="left", padx=(0, 6))

        ttk.Button(
            quadro_meio,
            text="Excluir Linha Atual",
            command=self.excluir_linha_atual
        ).pack(side="left")

        ttk.Button(
            quadro_meio,
            text="Gerar Novo INSERT",
            command=self.gerar_insert
        ).pack(side="right", padx=(0, 8))

        # --- Pesquisa ---
        quadro_pesquisa = ttk.Frame(self)
        quadro_pesquisa.pack(fill="x", pady=(0, 4))

        ttk.Label(quadro_pesquisa, text="Pesquisar (campo ou valor):").pack(side="left")
        self.ent_pesquisa = ttk.Entry(quadro_pesquisa, textvariable=self.var_pesquisa, width=45)
        self.ent_pesquisa.pack(side="left", padx=6)

        ttk.Button(
            quadro_pesquisa,
            text="Limpar Pesquisa",
            command=self.limpar_pesquisa
        ).pack(side="left")

        # --- Árvore de campos/valores ---
        quadro_lista = ttk.LabelFrame(self, text="Campos e valores da primeira linha do INSERT")
        quadro_lista.pack(fill="both", expand=True)

        self.arvore = ttk.Treeview(
            quadro_lista,
            columns=("campo", "valor"),
            show="headings",
            selectmode="extended"
        )
        self.arvore.heading("campo", text="Campo")
        self.arvore.heading("valor", text="Valor")
        self.arvore.column("campo", width=260, anchor="w")
        self.arvore.column("valor", width=520, anchor="w")

        # duplo clique para editar valor
        self.arvore.bind("<Double-1>", self._ao_duplo_clique)

        barra_y = ttk.Scrollbar(quadro_lista, orient="vertical", command=self.arvore.yview)
        self.arvore.configure(yscrollcommand=barra_y.set)

        self.arvore.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=6)
        barra_y.pack(side="right", fill="y", padx=(0, 8), pady=6)

        # --- Saída ---
        quadro_saida = ttk.LabelFrame(self, text="INSERT gerado")
        quadro_saida.pack(fill="both", expand=False, pady=(8, 0))

        self.txt_saida = tk.Text(quadro_saida, height=8, wrap="word")
        self.txt_saida.pack(fill="both", expand=True, padx=8, pady=6)

    # =========================
    # Funções utilitárias
    # =========================
    def limpar_saida(self):
        self.txt_saida.delete("1.0", "end")

    def atualizar_total_inserts(self):
        total = 0
        if self.declaracao:
            total = self.declaracao.quantidade_inserts_origem
        self.var_total_inserts.set(f"Total de INSERTs detectados: {total}")

    def aplicar_filtro(self):
        self.filtro_texto = self.var_pesquisa.get().strip().lower()
        self.atualizar_arvore()

    def limpar_pesquisa(self):
        self.var_pesquisa.set("")
        self.filtro_texto = ""
        self.atualizar_arvore()

    # =========================
    # Normalização de valores editados
    # =========================
    def _eh_numero_sql(self, texto: str) -> bool:
        return bool(re.fullmatch(r"[+-]?\d+(\.\d+)?", texto))

    def _normalizar_valor_editado(self, bruto: str) -> str:
        t = bruto.strip()

        if t == "":
            return "NULL"

        if t.upper() == "NULL":
            return "NULL"

        if len(t) >= 2 and t[0] == "'" and t[-1] == "'":
            return t

        if self._eh_numero_sql(t):
            return t

        t_esc = t.replace("'", "''")
        return f"'{t_esc}'"

    # =========================
    # Editor inline
    # =========================
    def _fechar_editor(self, salvar: bool):
        if not self._editor_entry:
            return

        if salvar and self.declaracao is not None and self._editor_col_idx is not None:
            bruto = self._editor_entry.get()
            novo_valor = self._normalizar_valor_editado(bruto)

            try:
                self.declaracao.linhas[self.linha_atual][self._editor_col_idx] = novo_valor
            except Exception:
                pass

            vals = list(self.arvore.item(self._editor_iid, "values"))
            if len(vals) >= 2:
                vals[1] = novo_valor
                self.arvore.item(self._editor_iid, values=vals)

        self._editor_entry.destroy()
        self._editor_entry = None
        self._editor_iid = None
        self._editor_col_idx = None

    def _ao_duplo_clique(self, evento):
        if not self.declaracao or not self.declaracao.linhas:
            return

        coluna = self.arvore.identify_column(evento.x)
        if coluna != "#2":
            return

        iid = self.arvore.identify_row(evento.y)
        if not iid:
            return

        self._fechar_editor(salvar=True)

        try:
            col_idx = int(iid)
        except ValueError:
            return

        x, y, w, h = self.arvore.bbox(iid, column=coluna)
        if w <= 0 or h <= 0:
            return

        valor_atual = self.arvore.item(iid, "values")[1]

        entry = tk.Entry(self.arvore)
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, valor_atual)
        entry.focus_set()
        entry.select_range(0, tk.END)

        entry.bind("<Return>", lambda e: self._fechar_editor(salvar=True))
        entry.bind("<Escape>", lambda e: self._fechar_editor(salvar=False))
        entry.bind("<FocusOut>", lambda e: self._fechar_editor(salvar=True))

        self._editor_entry = entry
        self._editor_iid = iid
        self._editor_col_idx = col_idx

    # =========================
    # Entrada de dados
    # =========================
    def colar_clipboard(self):
        try:
            texto = self.mestre.clipboard_get()
        except tk.TclError:
            messagebox.showwarning("Atenção", "Clipboard vazio ou inacessível.")
            return

        self.txt_entrada.delete("1.0", "end")
        self.txt_entrada.insert("1.0", texto)

    def abrir_arquivo(self):
        caminho = filedialog.askopenfilename(
            title="Selecionar arquivo",
            filetypes=[
                ("Arquivos SQL", "*.sql"),
                ("Arquivos texto", "*.txt"),
                ("Todos os arquivos", "*.*")
            ]
        )
        if not caminho:
            return

        try:
            with open(caminho, "r", encoding="utf-8") as f:
                texto = f.read()
        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao ler arquivo:\n{e}")
            return

        self.txt_entrada.delete("1.0", "end")
        self.txt_entrada.insert("1.0", texto)

    def processar_insert(self):
        sql = self.txt_entrada.get("1.0", "end").strip()
        if not sql:
            messagebox.showwarning("Atenção", "Cole ou abra um INSERT primeiro.")
            return

        try:
            self.declaracao = parsear_insert(sql)
        except Exception as e:
            self.declaracao = None
            self.atualizar_total_inserts()
            messagebox.showerror("Erro ao processar", str(e))
            return

        self.atualizar_total_inserts()

        self.linha_atual = 0
        self.atualizar_arvore()
        self.limpar_saida()

        if len(self.declaracao.linhas) > 1:
            messagebox.showinfo(
                "Info",
                "O INSERT possui várias linhas.\n"
                "A interface mostra apenas a primeira linha (linha 0)."
            )

    # =========================
    # Atualização da árvore (com filtro)
    # =========================
    def atualizar_arvore(self):
        self._fechar_editor(salvar=True)
        self.arvore.delete(*self.arvore.get_children())

        if not self.declaracao or not self.declaracao.linhas:
            return

        linha = self.declaracao.linhas[self.linha_atual]

        if self.declaracao.colunas is None:
            colunas = [f"col_{i+1}" for i in range(len(linha))]
        else:
            colunas = self.declaracao.colunas

        for i, (campo, valor) in enumerate(zip(colunas, linha)):
            if self.filtro_texto:
                alvo = f"{campo} {valor}".lower()
                if self.filtro_texto not in alvo:
                    continue

            self.arvore.insert("", "end", iid=str(i), values=(campo, valor))

    # =========================
    # Exclusão
    # =========================
    def excluir_colunas_selecionadas(self):
        if not self.declaracao:
            return

        self._fechar_editor(salvar=True)

        selecionados = self.arvore.selection()
        if not selecionados:
            messagebox.showinfo("Info", "Selecione uma ou mais colunas na lista.")
            return

        if self.declaracao.colunas is None:
            ok = messagebox.askyesno(
                "Atenção",
                "Esse INSERT não tinha lista de colunas explícita.\n"
                "Excluir colunas pode mudar o significado dos valores no banco.\n\n"
                "Deseja continuar?"
            )
            if not ok:
                return

        try:
            indices = sorted({int(iid) for iid in selecionados})
        except ValueError:
            return

        if not indices:
            return

        self.declaracao.remover_colunas_por_indices(indices)
        self.atualizar_arvore()

    def excluir_linha_atual(self):
        if not self.declaracao or not self.declaracao.linhas:
            return

        self._fechar_editor(salvar=True)

        ok = messagebox.askyesno(
            "Confirmar",
            "Excluir a primeira linha (linha 0)?"
        )
        if not ok:
            return

        self.declaracao.remover_linha(self.linha_atual)

        if not self.declaracao.linhas:
            self.atualizar_arvore()
            self.limpar_saida()
            return

        self.linha_atual = 0
        self.atualizar_arvore()

    # =========================
    # Geração de saída
    # =========================
    def gerar_insert(self):
        if not self.declaracao:
            messagebox.showwarning("Atenção", "Nenhum INSERT carregado.")
            return

        self._fechar_editor(salvar=True)
        self.limpar_saida()

        if self.declaracao.colunas is not None:
            tam_col = len(self.declaracao.colunas)
            ruins = [
                i for i, ln in enumerate(self.declaracao.linhas)
                if len(ln) != tam_col
            ]
            if ruins:
                messagebox.showwarning(
                    "Aviso",
                    "Existem linhas com quantidade de valores diferente das colunas.\n"
                    f"Linhas problemáticas: {ruins}\n\n"
                    "Isso pode gerar INSERT inválido."
                )

        if self.declaracao.quantidade_inserts_origem > 1:
            saida = self.declaracao.para_sql_multiplos()
        else:
            saida = self.declaracao.para_sql()

        self.txt_saida.insert("1.0", saida)


def iniciar_interface():
    raiz = tk.Tk()
    estilo = ttk.Style(raiz)
    try:
        estilo.theme_use("clam")
    except Exception:
        pass

    InterfaceInsert(raiz)
    raiz.minsize(980, 680)
    raiz.mainloop()
