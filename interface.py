import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import re
import threading

from parser_insert import parsear_insert, DeclaracaoInsert

_MAX_LINHAS_EXIBICAO = 200  # máx. de linhas SQL exibidas na Treeview

_MANUAL = (
    "MANUAL — AJUSTE DE INSERT\n\n"
    "1) Inserir o SQL de entrada\n"
    "   • Cole um ou mais INSERTs/REPLACEs na área 'Entrada' OU\n"
    "     use 'Abrir Arquivo .sql/.txt'.\n\n"
    "2) Processar\n"
    "   • Clique em 'Processar INSERT'.\n"
    "   • O programa detecta automaticamente quantos statements existem\n"
    "     no texto e mostra o total logo abaixo.\n\n"
    "3) Visualizar Campos e Valores\n"
    "   • A lista exibe cada linha do INSERT como uma linha da tabela.\n"
    "   • Cada coluna corresponde a um campo (DDA_AGECOB, DDA_BCOCOM…).\n"
    "   • Use a barra de rolagem horizontal para navegar pelos campos\n"
    "     e a vertical para navegar entre as linhas.\n"
    "   • São exibidas no máximo 200 linhas; o INSERT gerado usa todas.\n\n"
    "4) Pesquisar na lista\n"
    "   • Digite no campo 'Pesquisar (campo ou valor)'.\n"
    "   • A lista filtra as linhas que contenham o texto buscado\n"
    "     em qualquer campo ou no nome da linha ('Ln N').\n"
    "   • Para voltar ao normal, clique em 'Limpar Pesquisa'.\n\n"
    "5) Editar valores\n"
    "   • Dê duplo clique em qualquer linha para abrir o diálogo\n"
    "     'Alterar Valor' pré-selecionando aquela linha.\n"
    "   • Regras ao salvar:\n"
    "       - Se deixar vazio → vira NULL.\n"
    "       - Se não for número e não estiver entre aspas → vira texto com aspas.\n"
    "         Ex.: abc → 'abc'\n"
    "       - Aspas internas são escapadas automaticamente.\n"
    "         Ex.: O'Reilly → 'O''Reilly'\n\n"
    "6) Excluir colunas\n"
    "   • Clique em 'Excluir Coluna(s)' para abrir o diálogo de seleção.\n"
    "   • Escolha um ou mais campos na lista e confirme.\n"
    "   • O campo e os valores correspondentes são removidos de TODAS as linhas.\n\n"
    "7) Excluir uma linha\n"
    "   • Ajuste o seletor 'Linha:' para o número desejado (0 = primeira).\n"
    "   • Clique em 'Excluir Linha'.\n\n"
    "8) Gerar novo INSERT/REPLACE\n"
    "   • Clique em 'Gerar Novo INSERT'.\n"
    "   • A saída SEMPRE é limpa antes de gerar de novo.\n"
    "   • Se o texto original tinha vários statements, a ferramenta gera um\n"
    "     bloco separado para cada linha.\n\n"
    "9) Copiar e executar\n"
    "   • Clique em 'Copiar para Clipboard' e execute o SQL no banco.\n\n"
    "Dica:\n"
    "   • Se precisar inserir funções sem aspas (ex.: NOW()), digite já no formato\n"
    "     correto — a regra de texto coloca aspas se não detectar número."
)

# Paleta de cores
_COR_PRIMARIA       = "#0063b1"
_COR_PRIMARIA_HOVER = "#004f8c"
_COR_PRIMARIA_PRESS = "#003b69"
_COR_PERIGO         = "#c42b1c"
_COR_PERIGO_HOVER   = "#a31a0d"
_COR_PERIGO_PRESS   = "#821008"
_COR_LINHA_PAR      = "#f0f4f8"
_COR_LINHA_IMPAR    = "#ffffff"
_COR_NULO           = "#aaaaaa"
_COR_STATUS         = "#555555"


def _configurar_estilos(estilo: ttk.Style) -> None:
    """Registra os estilos customizados de botão."""
    # Botão primário — ação principal
    estilo.configure(
        "Primario.TButton",
        background=_COR_PRIMARIA,
        foreground="white",
        borderwidth=1,
        bordercolor=_COR_PRIMARIA,
        lightcolor=_COR_PRIMARIA,
        darkcolor=_COR_PRIMARIA_HOVER,
        relief="raised",
        padding=(10, 5),
        font=("", 0, "bold"),
    )
    estilo.map(
        "Primario.TButton",
        background=[
            ("active", _COR_PRIMARIA_HOVER),
            ("pressed", _COR_PRIMARIA_PRESS),
            ("disabled", "#cccccc"),
        ],
        foreground=[("disabled", "#888888")],
        relief=[("pressed", "sunken")],
    )

    # Botão de perigo — ações destrutivas
    estilo.configure(
        "Perigo.TButton",
        background=_COR_PERIGO,
        foreground="white",
        borderwidth=1,
        bordercolor=_COR_PERIGO,
        lightcolor=_COR_PERIGO,
        darkcolor=_COR_PERIGO_HOVER,
        relief="raised",
        padding=(8, 4),
    )
    estilo.map(
        "Perigo.TButton",
        background=[
            ("active", _COR_PERIGO_HOVER),
            ("pressed", _COR_PERIGO_PRESS),
            ("disabled", "#cccccc"),
        ],
        foreground=[("disabled", "#888888")],
        relief=[("pressed", "sunken")],
    )


class InterfaceInsert(ttk.Frame):
    def __init__(self, mestre):
        super().__init__(mestre)
        self.mestre = mestre
        self.declaracao: DeclaracaoInsert | None = None

        self.var_total_inserts = tk.StringVar(value="Total de INSERTs/REPLACEs detectados: 0")
        self.var_pesquisa = tk.StringVar(value="")
        self.filtro_texto: str = ""
        self.var_linha_excluir = tk.IntVar(value=0)
        self.var_status = tk.StringVar(value="Pronto")

        self._editor_entry: tk.Entry | None = None
        self._editor_iid: str | None = None
        self._editor_field_idx: int | None = None
        self._editor_sql_row_idx: int | None = None

        self.btn_copiar: ttk.Button | None = None

        self._montar_interface()
        self._criar_menu()
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
        messagebox.showinfo("Sobre", (
            "Nome: Ajuste de Insert\n"
            "Versão: 1.1.0\n"
            "Criado por: Felipe Augusto dos Santos"
        ))

    def mostrar_manual(self):
        janela = tk.Toplevel(self.mestre)
        janela.title("Manual - Ajuste de Insert")
        janela.transient(self.mestre)
        janela.resizable(True, True)

        frame = ttk.Frame(janela, padding=10)
        frame.pack(fill="both", expand=True)

        # scrollbar antes do Text para reservar espaço corretamente
        scroll = ttk.Scrollbar(frame, orient="vertical")
        scroll.pack(side="right", fill="y")

        txt = tk.Text(frame, wrap="word", yscrollcommand=scroll.set)
        txt.pack(side="left", fill="both", expand=True)
        scroll.configure(command=txt.yview)

        txt.insert("1.0", _MANUAL)
        txt.configure(state="disabled")

        ttk.Button(janela, text="Fechar", command=janela.destroy).pack(pady=(0, 10))

        # Centraliza e garante que a janela apareça na frente
        janela.update_idletasks()
        w, h = 760, 540
        x = self.mestre.winfo_x() + (self.mestre.winfo_width() - w) // 2
        y = self.mestre.winfo_y() + (self.mestre.winfo_height() - h) // 2
        janela.geometry(f"{w}x{h}+{x}+{y}")
        janela.lift()
        janela.focus_force()

    # =========================
    # Montagem da interface
    # =========================
    def _montar_interface(self):
        self.mestre.title("Ajuste de Insert")
        self.pack(fill="both", expand=True, padx=10, pady=10)

        # Barra de status — reservada primeiro para garantir espaço no fundo
        ttk.Separator(self, orient="horizontal").pack(side="bottom", fill="x", pady=(4, 0))
        ttk.Label(
            self,
            textvariable=self.var_status,
            anchor="w",
            foreground=_COR_STATUS,
            font=("", 8),
        ).pack(side="bottom", fill="x", padx=4)

        # --- Entrada ---
        quadro_entrada = ttk.LabelFrame(self, text="Entrada de INSERT / REPLACE")
        quadro_entrada.pack(fill="both", expand=False)

        frame_ent_txt = ttk.Frame(quadro_entrada)
        frame_ent_txt.pack(fill="both", expand=True, padx=8, pady=6)

        self.txt_entrada = tk.Text(frame_ent_txt, height=6, wrap="word")
        scroll_ent = ttk.Scrollbar(frame_ent_txt, orient="vertical", command=self.txt_entrada.yview)
        self.txt_entrada.configure(yscrollcommand=scroll_ent.set)
        scroll_ent.pack(side="right", fill="y")
        self.txt_entrada.pack(side="left", fill="both", expand=True)

        quadro_botoes_entrada = ttk.Frame(quadro_entrada)
        quadro_botoes_entrada.pack(fill="x", padx=8, pady=(0, 4))

        ttk.Button(
            quadro_botoes_entrada, text="Colar do Clipboard", command=self.colar_clipboard
        ).pack(side="left", padx=(0, 6))

        ttk.Button(
            quadro_botoes_entrada, text="Abrir Arquivo .sql/.txt", command=self.abrir_arquivo
        ).pack(side="left")

        ttk.Button(
            quadro_botoes_entrada,
            text="Processar INSERT",
            command=self.processar_insert,
            style="Primario.TButton",
        ).pack(side="right")

        ttk.Label(quadro_entrada, textvariable=self.var_total_inserts).pack(
            anchor="w", padx=10, pady=(0, 6)
        )

        # --- Ações ---
        quadro_meio = ttk.Frame(self)
        quadro_meio.pack(fill="x", pady=8)

        ttk.Button(
            quadro_meio,
            text="Excluir Coluna(s)",
            command=self.excluir_colunas_selecionadas,
            style="Perigo.TButton",
        ).pack(side="left", padx=(0, 6))

        quadro_excl_linha = ttk.Frame(quadro_meio)
        quadro_excl_linha.pack(side="left")

        ttk.Label(quadro_excl_linha, text="Linha:").pack(side="left")
        self.spn_linha_excluir = ttk.Spinbox(
            quadro_excl_linha,
            from_=0, to=0,
            textvariable=self.var_linha_excluir,
            width=4,
            state="disabled",
        )
        self.spn_linha_excluir.pack(side="left", padx=(2, 4))

        ttk.Button(
            quadro_excl_linha,
            text="Excluir Linha",
            command=self.excluir_linha_atual,
            style="Perigo.TButton",
        ).pack(side="left")

        ttk.Separator(quadro_meio, orient="vertical").pack(
            side="left", fill="y", padx=(10, 6), pady=2
        )
        ttk.Button(
            quadro_meio,
            text="Alterar Valor",
            command=self._abrir_dialogo_alterar,
        ).pack(side="left")

        ttk.Button(
            quadro_meio,
            text="Gerar Novo INSERT",
            command=self.gerar_insert,
            style="Primario.TButton",
        ).pack(side="right", padx=(0, 8))

        # --- Pesquisa ---
        quadro_pesquisa = ttk.Frame(self)
        quadro_pesquisa.pack(fill="x", pady=(0, 4))

        ttk.Label(quadro_pesquisa, text="Pesquisar (campo ou valor):").pack(side="left")
        ttk.Entry(quadro_pesquisa, textvariable=self.var_pesquisa, width=40).pack(
            side="left", padx=6
        )
        ttk.Button(
            quadro_pesquisa, text="Limpar Pesquisa", command=self.limpar_pesquisa
        ).pack(side="left")

        ttk.Label(
            quadro_pesquisa,
            text="ℹ  Duplo clique em uma linha para editar",
            foreground="#999999",
            font=("", 8),
        ).pack(side="right", padx=(0, 4))

        # --- PanedWindow: Treeview | Saída (redimensionável) ---
        painel = ttk.PanedWindow(self, orient="vertical")
        painel.pack(fill="both", expand=True)

        # Treeview
        quadro_lista = ttk.LabelFrame(painel, text="Campos e valores do INSERT")
        painel.add(quadro_lista, weight=3)

        frame_arvore = ttk.Frame(quadro_lista)
        frame_arvore.pack(fill="both", expand=True, padx=8, pady=(6, 0))

        self.arvore = ttk.Treeview(
            frame_arvore,
            columns=("campo", "valor"),
            show="headings",
            selectmode="extended",
        )
        self.arvore.heading("campo", text="Campo")
        self.arvore.heading("valor", text="Valor")
        self.arvore.column("campo", width=220, anchor="w")
        self.arvore.column("valor", width=560, anchor="w")

        self.arvore.tag_configure("par", background=_COR_LINHA_PAR)
        self.arvore.tag_configure("impar", background=_COR_LINHA_IMPAR)
        self.arvore.tag_configure("tudo_nulo", foreground=_COR_NULO)

        self.arvore.bind("<Double-1>", self._ao_duplo_clique)

        barra_y = ttk.Scrollbar(frame_arvore, orient="vertical", command=self.arvore.yview)
        barra_x = ttk.Scrollbar(frame_arvore, orient="horizontal", command=self.arvore.xview)
        self.arvore.configure(yscrollcommand=barra_y.set, xscrollcommand=barra_x.set)

        barra_x.pack(side="bottom", fill="x")
        barra_y.pack(side="right", fill="y")
        self.arvore.pack(side="left", fill="both", expand=True)

        # Saída
        quadro_saida = ttk.LabelFrame(painel, text="INSERT gerado")
        painel.add(quadro_saida, weight=1)

        quadro_saida_btns = ttk.Frame(quadro_saida)
        quadro_saida_btns.pack(fill="x", padx=8, pady=(6, 2))

        self.btn_copiar = ttk.Button(
            quadro_saida_btns, text="Copiar para Clipboard", command=self.copiar_saida
        )
        self.btn_copiar.pack(side="left")

        frame_saida = ttk.Frame(quadro_saida)
        frame_saida.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        self.txt_saida = tk.Text(frame_saida, height=8, wrap="word", state="disabled")
        scroll_saida = ttk.Scrollbar(frame_saida, orient="vertical", command=self.txt_saida.yview)
        self.txt_saida.configure(yscrollcommand=scroll_saida.set)
        scroll_saida.pack(side="right", fill="y")
        self.txt_saida.pack(side="left", fill="both", expand=True)

    # =========================
    # Colunas dinâmicas da árvore
    # =========================
    def _configurar_colunas_arvore(self, campos: list):
        """Cria uma coluna na Treeview para cada campo SQL."""
        col_ids = ("_ln",) + tuple(f"_c{i}" for i in range(len(campos)))
        self.arvore.configure(columns=col_ids)
        self.arvore.heading("_ln", text="#")
        self.arvore.column("_ln", width=55, minwidth=10, anchor="center", stretch=False)
        for i, nome in enumerate(campos):
            cid = f"_c{i}"
            self.arvore.heading(cid, text=nome)
            self.arvore.column(cid, width=140, minwidth=10, anchor="w", stretch=False)

    def _atualizar_spinbox(self, num_linhas: int):
        if num_linhas == 0:
            self.spn_linha_excluir.configure(to=0, state="disabled")
            self.var_linha_excluir.set(0)
        else:
            self.spn_linha_excluir.configure(to=max(0, num_linhas - 1), state="normal")
            if self.var_linha_excluir.get() >= num_linhas:
                self.var_linha_excluir.set(0)

    # =========================
    # Funções utilitárias
    # =========================
    def _atualizar_status(self, mensagem: str) -> None:
        self.var_status.set(mensagem)

    def limpar_saida(self):
        self.txt_saida.configure(state="normal")
        self.txt_saida.delete("1.0", "end")
        self.txt_saida.configure(state="disabled")

    def copiar_saida(self):
        conteudo = self.txt_saida.get("1.0", "end").strip()
        if not conteudo:
            messagebox.showinfo("Info", "Nenhum INSERT gerado para copiar.")
            return
        self.mestre.clipboard_clear()
        self.mestre.clipboard_append(conteudo)
        self._atualizar_status("SQL copiado para o clipboard")
        if self.btn_copiar:
            self.btn_copiar.configure(text="✓ Copiado!")
            self.mestre.after(
                1500, lambda: self.btn_copiar.configure(text="Copiar para Clipboard")
            )

    def atualizar_total_inserts(self):
        total = self.declaracao.quantidade_inserts_origem if self.declaracao else 0
        self.var_total_inserts.set(f"Total de INSERTs/REPLACEs detectados: {total}")

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
        return bool(re.fullmatch(r"[+-]?\d+(\.\d+)?([eE][+-]?\d+)?", texto))

    def _normalizar_valor_editado(self, bruto: str) -> str:
        t = bruto.strip()
        if t == "" or t.upper() == "NULL":
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

        if (salvar
                and self.declaracao is not None
                and self._editor_field_idx is not None
                and self._editor_sql_row_idx is not None):
            bruto = self._editor_entry.get()
            novo_valor = self._normalizar_valor_editado(bruto)

            try:
                self.declaracao.linhas[self._editor_sql_row_idx][self._editor_field_idx] = novo_valor
            except Exception:
                pass

            vals = list(self.arvore.item(self._editor_iid, "values"))
            val_display_idx = self._editor_sql_row_idx + 1
            if val_display_idx < len(vals):
                vals[val_display_idx] = novo_valor
                self.arvore.item(self._editor_iid, values=vals)

        self._editor_entry.destroy()
        self._editor_entry = None
        self._editor_iid = None
        self._editor_field_idx = None
        self._editor_sql_row_idx = None

    def _ao_duplo_clique(self, evento):
        if not self.declaracao or not self.declaracao.linhas:
            return
        if not self.arvore.identify_row(evento.y):
            return
        self._abrir_dialogo_alterar()

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
                ("Todos os arquivos", "*.*"),
            ],
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
            messagebox.showwarning("Atenção", "Cole ou abra um INSERT/REPLACE primeiro.")
            return

        self._atualizar_status("Processando…")
        self.mestre.config(cursor="watch")
        self.mestre.update_idletasks()

        def _tarefa():
            try:
                resultado = parsear_insert(sql)
                erro = None
            except Exception as e:
                resultado = None
                erro = e
            self.mestre.after(0, lambda: self._finalizar_processamento(resultado, erro))

        threading.Thread(target=_tarefa, daemon=True).start()

    def _finalizar_processamento(self, resultado, erro):
        self.mestre.config(cursor="")
        if erro is not None:
            self.declaracao = None
            self.atualizar_total_inserts()
            self._atualizar_status(f"Erro: {erro}")
            messagebox.showerror("Erro ao processar", str(erro))
            return
        self.declaracao = resultado
        self.atualizar_total_inserts()
        self.atualizar_arvore()
        self.limpar_saida()
        n = self.declaracao.quantidade_inserts_origem
        num_linhas = len(self.declaracao.linhas)
        if num_linhas > _MAX_LINHAS_EXIBICAO:
            self._atualizar_status(
                f"{n} statement(s) carregado(s) — tabela {self.declaracao.tabela} — "
                f"exibindo {_MAX_LINHAS_EXIBICAO} de {num_linhas} linha(s) na visualização"
            )
        else:
            self._atualizar_status(
                f"{n} statement(s) carregado(s) — tabela {self.declaracao.tabela}"
            )

    # =========================
    # Atualização da árvore
    # =========================
    def atualizar_arvore(self):
        self._fechar_editor(salvar=True)
        self.arvore.delete(*self.arvore.get_children())

        if not self.declaracao or not self.declaracao.linhas:
            self._configurar_colunas_arvore([])
            self._atualizar_spinbox(0)
            return

        num_linhas = len(self.declaracao.linhas)
        self._atualizar_spinbox(num_linhas)

        if self.declaracao.colunas is not None:
            campos = self.declaracao.colunas
        else:
            max_len = max((len(ln) for ln in self.declaracao.linhas), default=0)
            campos = [f"col_{i+1}" for i in range(max_len)]

        self._configurar_colunas_arvore(campos)
        num_campos = len(campos)

        contador = 0
        for i, linha in enumerate(self.declaracao.linhas):
            if i >= _MAX_LINHAS_EXIBICAO:
                break

            if self.filtro_texto:
                alvo = f"ln {i} {' '.join(linha)}".lower()
                if self.filtro_texto not in alvo:
                    continue

            valores = tuple(linha[j] if j < len(linha) else "" for j in range(num_campos))
            tag_cor = "par" if contador % 2 == 0 else "impar"
            tags = [tag_cor]
            if all(v.strip().upper() == "NULL" or v.strip() == "" for v in valores):
                tags.append("tudo_nulo")

            self.arvore.insert("", "end", iid=str(i), values=(f"Ln {i}",) + valores, tags=tags)
            contador += 1

    # =========================
    # Exclusão
    # =========================
    def excluir_colunas_selecionadas(self):
        if not self.declaracao:
            return
        self._fechar_editor(salvar=True)
        if self.declaracao.colunas is not None:
            campos = self.declaracao.colunas
        else:
            max_len = max((len(ln) for ln in self.declaracao.linhas), default=0)
            campos = [f"col_{i+1}" for i in range(max_len)]
        self._abrir_dialogo_excluir_colunas(campos)

    def _abrir_dialogo_excluir_colunas(self, campos):
        janela = tk.Toplevel(self.mestre)
        janela.title("Excluir Coluna(s)")
        janela.transient(self.mestre)
        janela.grab_set()
        janela.resizable(False, True)

        frame = ttk.Frame(janela, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Selecione a(s) coluna(s) a excluir:").pack(anchor="w", pady=(0, 6))

        frame_lista = ttk.Frame(frame)
        frame_lista.pack(fill="both", expand=True)

        scroll_lb = ttk.Scrollbar(frame_lista, orient="vertical")
        scroll_lb.pack(side="right", fill="y")
        lb = tk.Listbox(
            frame_lista, selectmode="extended",
            yscrollcommand=scroll_lb.set, height=15, activestyle="none",
        )
        lb.pack(side="left", fill="both", expand=True)
        scroll_lb.configure(command=lb.yview)

        for nome in campos:
            lb.insert("end", nome)

        ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=10)

        frame_btns = ttk.Frame(frame)
        frame_btns.pack(fill="x")

        def _aplicar():
            indices = list(lb.curselection())
            if not indices:
                messagebox.showinfo("Info", "Selecione ao menos uma coluna.", parent=janela)
                return
            if self.declaracao.colunas is None:
                ok = messagebox.askyesno(
                    "Atenção",
                    "Esse INSERT não tinha lista de colunas explícita.\n"
                    "Excluir colunas pode mudar o significado dos valores no banco.\n\n"
                    "Deseja continuar?",
                    parent=janela,
                )
                if not ok:
                    return
            self.declaracao.remover_colunas_por_indices(indices)
            self.atualizar_arvore()
            self._atualizar_status(f"{len(indices)} coluna(s) excluída(s)")
            janela.destroy()

        ttk.Button(frame_btns, text="Cancelar", command=janela.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(frame_btns, text="Excluir", command=_aplicar, style="Perigo.TButton").pack(side="left")

        janela.update_idletasks()
        w, h = 380, 460
        x = self.mestre.winfo_x() + (self.mestre.winfo_width() - w) // 2
        y = self.mestre.winfo_y() + (self.mestre.winfo_height() - h) // 2
        janela.geometry(f"{w}x{h}+{x}+{y}")
        janela.lift()
        janela.focus_force()

    def excluir_linha_atual(self):
        if not self.declaracao or not self.declaracao.linhas:
            return

        self._fechar_editor(salvar=True)

        try:
            idx = int(self.var_linha_excluir.get())
        except (ValueError, tk.TclError):
            idx = 0

        total = len(self.declaracao.linhas)
        if idx < 0 or idx >= total:
            messagebox.showwarning("Atenção", f"Linha {idx} não existe. Total: {total} linha(s).")
            return

        ok = messagebox.askyesno(
            "Confirmar",
            f"Excluir a linha {idx}? ({total} linha(s) no total)",
        )
        if not ok:
            return

        self.declaracao.remover_linha(idx)

        if not self.declaracao.linhas:
            self.atualizar_arvore()
            self.limpar_saida()
            self._atualizar_status("Todas as linhas foram removidas")
            return

        self.atualizar_arvore()
        self._atualizar_status(f"Linha {idx} excluída")

    # =========================
    # Alteração em lote
    # =========================
    def _abrir_dialogo_alterar(self):
        if not self.declaracao or not self.declaracao.linhas:
            messagebox.showwarning("Atenção", "Nenhum INSERT carregado.")
            return

        if self.declaracao.colunas is not None:
            campos = self.declaracao.colunas
        else:
            max_len = max(len(ln) for ln in self.declaracao.linhas)
            campos = [f"col_{i+1}" for i in range(max_len)]

        num_linhas = len(self.declaracao.linhas)

        janela = tk.Toplevel(self.mestre)
        janela.title("Alterar Valor")
        janela.transient(self.mestre)
        janela.grab_set()
        janela.resizable(False, False)

        frame = ttk.Frame(janela, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        # Campo
        ttk.Label(frame, text="Campo:").grid(row=0, column=0, sticky="w", pady=(0, 6))
        var_campo = tk.StringVar()
        cmb_campo = ttk.Combobox(
            frame, textvariable=var_campo, values=campos, width=36, state="readonly"
        )
        cmb_campo.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(10, 0), pady=(0, 6))

        if campos:
            cmb_campo.current(0)

        # Novo valor
        ttk.Label(frame, text="Novo valor:").grid(row=1, column=0, sticky="w", pady=(0, 10))
        var_valor = tk.StringVar()
        ent_valor = ttk.Entry(frame, textvariable=var_valor, width=36)
        ent_valor.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(10, 0), pady=(0, 10))

        # Separador
        ttk.Separator(frame, orient="horizontal").grid(
            row=2, column=0, columnspan=3, sticky="ew", pady=(0, 10)
        )

        # Escopo
        ttk.Label(frame, text="Aplicar em:").grid(row=3, column=0, sticky="nw", pady=(0, 4))

        var_escopo = tk.StringVar(value="todas")

        ttk.Radiobutton(
            frame, text="Todas as linhas", variable=var_escopo, value="todas"
        ).grid(row=3, column=1, columnspan=2, sticky="w", padx=(10, 0))

        frame_linha_esp = ttk.Frame(frame)
        frame_linha_esp.grid(row=4, column=1, columnspan=2, sticky="w", padx=(10, 0), pady=(4, 14))

        ttk.Radiobutton(
            frame_linha_esp, text="Linha específica:", variable=var_escopo, value="linha"
        ).pack(side="left")

        var_linha_dlg = tk.IntVar(value=0)
        ttk.Spinbox(
            frame_linha_esp, from_=0, to=max(0, num_linhas - 1),
            textvariable=var_linha_dlg, width=4,
        ).pack(side="left", padx=(6, 0))

        ttk.Label(
            frame_linha_esp,
            text=f"(0 a {num_linhas - 1})",
            foreground="#888888",
            font=("", 8),
        ).pack(side="left", padx=(4, 0))

        # Pré-seleciona a linha específica se houver seleção na Treeview
        selecionados = self.arvore.selection()
        if selecionados:
            try:
                idx_linha_pre = int(selecionados[0])
                var_escopo.set("linha")
                var_linha_dlg.set(idx_linha_pre)
            except (ValueError, IndexError):
                pass

        # Separador
        ttk.Separator(frame, orient="horizontal").grid(
            row=5, column=0, columnspan=3, sticky="ew", pady=(0, 10)
        )

        # Botões
        frame_btns = ttk.Frame(frame)
        frame_btns.grid(row=6, column=0, columnspan=3, sticky="e")

        def _aplicar():
            campo_sel = var_campo.get()
            if not campo_sel:
                messagebox.showwarning("Atenção", "Selecione um campo.", parent=janela)
                return

            try:
                field_idx = campos.index(campo_sel)
            except ValueError:
                messagebox.showerror("Erro", "Campo não encontrado.", parent=janela)
                return

            novo_valor = self._normalizar_valor_editado(var_valor.get())
            escopo = var_escopo.get()

            if escopo == "todas":
                for ln in self.declaracao.linhas:
                    if field_idx < len(ln):
                        ln[field_idx] = novo_valor
                self._atualizar_status(
                    f"'{campo_sel}' atualizado em {num_linhas} linha(s) → {novo_valor}"
                )
            else:
                idx_linha = var_linha_dlg.get()
                if 0 <= idx_linha < num_linhas:
                    ln = self.declaracao.linhas[idx_linha]
                    if field_idx < len(ln):
                        ln[field_idx] = novo_valor
                self._atualizar_status(
                    f"'{campo_sel}' atualizado na linha {idx_linha} → {novo_valor}"
                )

            self.atualizar_arvore()
            janela.destroy()

        ttk.Button(frame_btns, text="Cancelar", command=janela.destroy).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(
            frame_btns, text="Aplicar", command=_aplicar, style="Primario.TButton"
        ).pack(side="left")

        # Atalhos e posicionamento
        ent_valor.focus_set()
        janela.bind("<Return>", lambda e: _aplicar())
        janela.bind("<Escape>", lambda e: janela.destroy())

        janela.update_idletasks()
        w = janela.winfo_width()
        h = janela.winfo_height()
        x = self.mestre.winfo_x() + (self.mestre.winfo_width() - w) // 2
        y = self.mestre.winfo_y() + (self.mestre.winfo_height() - h) // 2
        janela.geometry(f"+{x}+{y}")

    # =========================
    # Geração de saída
    # =========================
    def gerar_insert(self):
        if not self.declaracao:
            messagebox.showwarning("Atenção", "Nenhum INSERT/REPLACE carregado.")
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
                    "Isso pode gerar INSERT inválido.",
                )

        if self.declaracao.quantidade_inserts_origem > 1:
            saida = self.declaracao.para_sql_multiplos()
        else:
            saida = self.declaracao.para_sql()

        self.txt_saida.configure(state="normal")
        self.txt_saida.insert("1.0", saida)
        self.txt_saida.configure(state="disabled")
        self._atualizar_status(
            f"INSERT gerado — {len(self.declaracao.linhas)} linha(s), "
            f"{len(self.declaracao.colunas or [])} coluna(s)"
        )


def iniciar_interface():
    raiz = tk.Tk()
    estilo = ttk.Style(raiz)
    try:
        estilo.theme_use("clam")
    except Exception:
        pass
    _configurar_estilos(estilo)
    InterfaceInsert(raiz)
    raiz.minsize(980, 680)
    # Limita ao tamanho do monitor para nunca estourar a tela
    raiz.maxsize(raiz.winfo_screenwidth(), raiz.winfo_screenheight())
    try:
        raiz.mainloop()
    except KeyboardInterrupt:
        raiz.destroy()
