import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import copy
import threading
import sys
from pathlib import Path

from app.core.exceptions import LinhasInvalidasError
from app.core.models import DeclaracaoInsert
from app.core.parser_insert import parsear_insert
from app.core.sql_formatador import eh_numero_sql, normalizar_valor_editado
from app.version import APP_AUTHOR, APP_BUILD, APP_NAME, APP_RELEASE_DATE, APP_VERSION
from app.ui.styles import (
    COR_FUNDO as _COR_FUNDO,
    COR_LINHA_IMPAR as _COR_LINHA_IMPAR,
    COR_LINHA_PAR as _COR_LINHA_PAR,
    COR_NULO as _COR_NULO,
    COR_PAINEL as _COR_PAINEL,
    COR_SELECAO as _COR_SELECAO,
    COR_TEXTO as _COR_TEXTO,
    FONTE_MONO as _FONTE_MONO,
    configurar_estilos,
)

_MAX_LINHAS_EXIBICAO = 200  # máx. de linhas SQL exibidas na Treeview
_MAX_HISTORICO = 30

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
_COR_FUNDO          = "#eef2f7"
_COR_PAINEL         = "#ffffff"
_COR_BORDA          = "#cbd5e1"
_COR_TEXTO          = "#111827"
_COR_TEXTO_SUAVE    = "#64748b"
_COR_PRIMARIA       = "#0f62a9"
_COR_PRIMARIA_HOVER = "#0b4f86"
_COR_PRIMARIA_PRESS = "#083a63"
_COR_PERIGO         = "#b42318"
_COR_PERIGO_HOVER   = "#912018"
_COR_PERIGO_PRESS   = "#7a1a14"
_COR_LINHA_PAR      = "#f8fafc"
_COR_LINHA_IMPAR    = "#ffffff"
_COR_SELECAO        = "#dbeafe"
_COR_NULO           = "#94a3b8"
_COR_STATUS         = "#475569"
_FONTE_PADRAO       = ("Segoe UI", 9)
_FONTE_TITULO       = ("Segoe UI Semibold", 14)
_FONTE_MONO         = ("Consolas", 9)
_CAMINHO_ICONE      = Path("assets") / "icone.ico"


def _caminho_recurso(relativo: Path) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / relativo


def _configurar_estilos(estilo: ttk.Style) -> None:
    """Registra os estilos customizados de botão."""
    # Botão primário — ação principal
    estilo.configure(".", font=_FONTE_PADRAO)
    estilo.configure("App.TFrame", background=_COR_FUNDO)
    estilo.configure("Panel.TFrame", background=_COR_PAINEL)
    estilo.configure("Toolbar.TFrame", background=_COR_PAINEL)
    estilo.configure("TLabel", background=_COR_FUNDO, foreground=_COR_TEXTO)
    estilo.configure("Panel.TLabel", background=_COR_PAINEL, foreground=_COR_TEXTO)
    estilo.configure("Muted.TLabel", background=_COR_PAINEL, foreground=_COR_TEXTO_SUAVE)
    estilo.configure("Status.TLabel", background=_COR_FUNDO, foreground=_COR_STATUS, font=("Segoe UI", 8))
    estilo.configure("Title.TLabel", background=_COR_FUNDO, foreground=_COR_TEXTO, font=_FONTE_TITULO)
    estilo.configure(
        "Card.TLabelframe",
        background=_COR_PAINEL,
        bordercolor=_COR_BORDA,
        relief="solid",
        padding=8,
    )
    estilo.configure(
        "Card.TLabelframe.Label",
        background=_COR_FUNDO,
        foreground=_COR_TEXTO,
        font=("Segoe UI Semibold", 9),
    )
    estilo.configure(
        "Treeview",
        background=_COR_PAINEL,
        fieldbackground=_COR_PAINEL,
        foreground=_COR_TEXTO,
        bordercolor=_COR_BORDA,
        rowheight=24,
        font=_FONTE_MONO,
    )
    estilo.configure(
        "Treeview.Heading",
        background="#e2e8f0",
        foreground=_COR_TEXTO,
        bordercolor=_COR_BORDA,
        relief="flat",
        font=("Segoe UI Semibold", 8),
        padding=(6, 5),
    )
    estilo.map(
        "Treeview",
        background=[("selected", _COR_SELECAO)],
        foreground=[("selected", _COR_TEXTO)],
    )
    estilo.configure("TButton", padding=(9, 5))
    estilo.configure("TEntry", padding=(6, 4))
    estilo.configure("TSpinbox", padding=(4, 3))

    estilo.configure(
        "Primario.TButton",
        background=_COR_PRIMARIA,
        foreground="white",
        borderwidth=1,
        bordercolor=_COR_PRIMARIA,
        lightcolor=_COR_PRIMARIA,
        darkcolor=_COR_PRIMARIA_HOVER,
        relief="flat",
        padding=(12, 6),
        font=("Segoe UI Semibold", 9),
    )
    estilo.map(
        "Primario.TButton",
        background=[
            ("active", _COR_PRIMARIA_HOVER),
            ("pressed", _COR_PRIMARIA_PRESS),
            ("disabled", "#cccccc"),
        ],
        foreground=[("disabled", "#888888")],
        relief=[("pressed", "flat")],
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
        relief="flat",
        padding=(10, 5),
        font=("Segoe UI", 9),
    )
    estilo.map(
        "Perigo.TButton",
        background=[
            ("active", _COR_PERIGO_HOVER),
            ("pressed", _COR_PERIGO_PRESS),
            ("disabled", "#cccccc"),
        ],
        foreground=[("disabled", "#888888")],
        relief=[("pressed", "flat")],
    )


class InterfaceInsert(ttk.Frame):
    def __init__(self, mestre):
        super().__init__(mestre, style="App.TFrame")
        self.mestre = mestre
        self.declaracao: DeclaracaoInsert | None = None

        self.var_total_inserts = tk.StringVar(value="Total de INSERTs/REPLACEs detectados: 0")
        self.var_resumo = tk.StringVar(value="Nenhum INSERT carregado")
        self.var_pesquisa = tk.StringVar(value="")
        self.filtro_texto: str = ""
        self.var_linha_excluir = tk.IntVar(value=0)
        self.var_status = tk.StringVar(value="Pronto")

        self._editor_entry: tk.Entry | None = None
        self._editor_iid: str | None = None
        self._editor_field_idx: int | None = None
        self._editor_sql_row_idx: int | None = None

        self.btn_copiar: ttk.Button | None = None
        self.btn_salvar_saida: ttk.Button | None = None
        self.btn_desfazer: ttk.Button | None = None
        self.btn_refazer: ttk.Button | None = None
        self.historico_desfazer: list[DeclaracaoInsert] = []
        self.historico_refazer: list[DeclaracaoInsert] = []

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
            f"Nome: {APP_NAME}\n"
            f"Versão: {APP_VERSION}\n"
            f"Build: {APP_BUILD}\n"
            f"Data da release: {APP_RELEASE_DATE}\n"
            f"Autor: {APP_AUTHOR}\n\n"
            "Ferramenta para carregar, validar, ajustar e gerar comandos "
            "INSERT/REPLACE SQL com apoio visual."
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
        self.mestre.configure(bg=_COR_FUNDO)
        self.pack(fill="both", expand=True, padx=14, pady=12)

        cabecalho = ttk.Frame(self, style="App.TFrame")
        cabecalho.pack(fill="x", pady=(0, 10))
        ttk.Label(cabecalho, text="Ajuste de Insert", style="Title.TLabel").pack(side="left")
        ttk.Label(
            cabecalho,
            textvariable=self.var_total_inserts,
            style="Status.TLabel",
        ).pack(side="right", padx=(12, 0))
        ttk.Label(
            self,
            textvariable=self.var_resumo,
            style="Status.TLabel",
            anchor="w",
        ).pack(fill="x", pady=(0, 8))

        # Barra de status — reservada primeiro para garantir espaço no fundo
        ttk.Separator(self, orient="horizontal").pack(side="bottom", fill="x", pady=(4, 0))
        ttk.Label(
            self,
            textvariable=self.var_status,
            anchor="w",
            style="Status.TLabel",
        ).pack(side="bottom", fill="x", padx=4)

        # --- Entrada ---
        quadro_entrada = ttk.LabelFrame(self, text="Entrada de INSERT / REPLACE", style="Card.TLabelframe")
        quadro_entrada.pack(fill="x", expand=False, pady=(0, 8))

        frame_ent_txt = ttk.Frame(quadro_entrada, style="Panel.TFrame")
        frame_ent_txt.pack(fill="both", expand=True, padx=8, pady=(8, 6))

        self.txt_entrada = tk.Text(frame_ent_txt, height=6, wrap="none")
        scroll_ent = ttk.Scrollbar(frame_ent_txt, orient="vertical", command=self.txt_entrada.yview)
        scroll_ent_x = ttk.Scrollbar(frame_ent_txt, orient="horizontal", command=self.txt_entrada.xview)
        self.txt_entrada.configure(yscrollcommand=scroll_ent.set, xscrollcommand=scroll_ent_x.set)
        scroll_ent_x.pack(side="bottom", fill="x")
        scroll_ent.pack(side="right", fill="y")
        self.txt_entrada.pack(side="left", fill="both", expand=True)
        self._estilizar_texto(self.txt_entrada)

        quadro_botoes_entrada = ttk.Frame(quadro_entrada, style="Toolbar.TFrame")
        quadro_botoes_entrada.pack(fill="x", padx=8, pady=(0, 8))

        ttk.Button(
            quadro_botoes_entrada, text="Colar", command=self.colar_clipboard
        ).pack(side="left", padx=(0, 8))

        ttk.Button(
            quadro_botoes_entrada, text="Abrir SQL/TXT", command=self.abrir_arquivo
        ).pack(side="left")

        ttk.Button(
            quadro_botoes_entrada,
            text="Processar INSERT",
            command=self.processar_insert,
            style="Primario.TButton",
        ).pack(side="right")

        # --- Ações ---
        quadro_meio = ttk.Frame(self, style="App.TFrame")
        quadro_meio.pack(fill="x", pady=(0, 8))

        ttk.Button(
            quadro_meio,
            text="Excluir Coluna(s)",
            command=self.excluir_colunas_selecionadas,
            style="Perigo.TButton",
        ).pack(side="left", padx=(0, 6))

        quadro_excl_linha = ttk.Frame(quadro_meio, style="App.TFrame")
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
        self.btn_desfazer = ttk.Button(
            quadro_meio,
            text="Desfazer",
            command=self.desfazer,
            state="disabled",
        )
        self.btn_desfazer.pack(side="left", padx=(0, 6))

        self.btn_refazer = ttk.Button(
            quadro_meio,
            text="Refazer",
            command=self.refazer,
            state="disabled",
        )
        self.btn_refazer.pack(side="left", padx=(0, 10))

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
        quadro_pesquisa = ttk.Frame(self, style="App.TFrame")
        quadro_pesquisa.pack(fill="x", pady=(0, 8))

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
            style="Status.TLabel",
        ).pack(side="right", padx=(0, 4))

        # --- PanedWindow: Treeview | Saída (redimensionável) ---
        painel = ttk.PanedWindow(self, orient="vertical")
        painel.pack(fill="both", expand=True)

        # Treeview
        quadro_lista = ttk.LabelFrame(painel, text="Campos e valores do INSERT", style="Card.TLabelframe")
        painel.add(quadro_lista, weight=3)

        frame_arvore = ttk.Frame(quadro_lista, style="Panel.TFrame")
        frame_arvore.pack(fill="both", expand=True, padx=8, pady=(6, 0))

        self.arvore = ttk.Treeview(
            frame_arvore,
            columns=("campo", "valor"),
            show="headings",
            selectmode="extended",
        )
        self.arvore.heading("campo", text="Campo")
        self.arvore.heading("valor", text="Valor")
        self.arvore.column("campo", width=220, anchor="w", stretch=False)
        self.arvore.column("valor", width=560, anchor="w", stretch=False)

        self.arvore.tag_configure("par", background=_COR_LINHA_PAR)
        self.arvore.tag_configure("impar", background=_COR_LINHA_IMPAR)
        self.arvore.tag_configure("tudo_nulo", foreground=_COR_NULO)
        self.arvore.tag_configure("problema", background="#fff1f2", foreground="#991b1b")

        self.arvore.bind("<Double-1>", self._ao_duplo_clique)

        barra_y = ttk.Scrollbar(frame_arvore, orient="vertical", command=self.arvore.yview)
        barra_x = ttk.Scrollbar(frame_arvore, orient="horizontal", command=self.arvore.xview)
        self.arvore.configure(yscrollcommand=barra_y.set, xscrollcommand=barra_x.set)

        barra_x.pack(side="bottom", fill="x")
        barra_y.pack(side="right", fill="y")
        self.arvore.pack(side="left", fill="both", expand=True)

        # Saída
        quadro_saida = ttk.LabelFrame(painel, text="INSERT gerado", style="Card.TLabelframe")
        painel.add(quadro_saida, weight=1)

        quadro_saida_btns = ttk.Frame(quadro_saida, style="Toolbar.TFrame")
        quadro_saida_btns.pack(fill="x", padx=8, pady=(6, 2))

        self.btn_copiar = ttk.Button(
            quadro_saida_btns, text="Copiar para Clipboard", command=self.copiar_saida
        )
        self.btn_copiar.pack(side="left")
        self.btn_salvar_saida = ttk.Button(
            quadro_saida_btns, text="Salvar .sql", command=self.salvar_saida
        )
        self.btn_salvar_saida.pack(side="left", padx=(8, 0))

        frame_saida = ttk.Frame(quadro_saida, style="Panel.TFrame")
        frame_saida.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        self.txt_saida = tk.Text(frame_saida, height=8, wrap="none", state="disabled")
        scroll_saida = ttk.Scrollbar(frame_saida, orient="vertical", command=self.txt_saida.yview)
        scroll_saida_x = ttk.Scrollbar(frame_saida, orient="horizontal", command=self.txt_saida.xview)
        self.txt_saida.configure(yscrollcommand=scroll_saida.set, xscrollcommand=scroll_saida_x.set)
        scroll_saida_x.pack(side="bottom", fill="x")
        scroll_saida.pack(side="right", fill="y")
        self.txt_saida.pack(side="left", fill="both", expand=True)
        self._estilizar_texto(self.txt_saida)

    # =========================
    # Colunas dinâmicas da árvore
    # =========================
    def _configurar_colunas_arvore(self, campos: list):
        """Cria uma coluna na Treeview para cada campo SQL."""
        col_ids = ("_ln",) + tuple(f"_c{i}" for i in range(len(campos)))
        self.arvore.configure(columns=col_ids)
        self.arvore.heading("_ln", text="#")
        self.arvore.column("_ln", width=55, anchor="center", stretch=False)
        for i, nome in enumerate(campos):
            cid = f"_c{i}"
            self.arvore.heading(cid, text=nome)
            self.arvore.column(cid, width=140, anchor="w", stretch=False)

    def _estilizar_texto(self, widget: tk.Text) -> None:
        widget.configure(
            background=_COR_PAINEL,
            foreground=_COR_TEXTO,
            insertbackground=_COR_TEXTO,
            selectbackground=_COR_SELECAO,
            selectforeground=_COR_TEXTO,
            font=_FONTE_MONO,
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=6,
        )

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

    def _atualizar_botoes_historico(self) -> None:
        if self.btn_desfazer:
            self.btn_desfazer.configure(
                state="normal" if self.historico_desfazer else "disabled"
            )
        if self.btn_refazer:
            self.btn_refazer.configure(
                state="normal" if self.historico_refazer else "disabled"
            )

    def _limpar_historico(self) -> None:
        self.historico_desfazer.clear()
        self.historico_refazer.clear()
        self._atualizar_botoes_historico()

    def _registrar_estado_para_desfazer(self) -> None:
        if self.declaracao is None:
            return
        self.historico_desfazer.append(copy.deepcopy(self.declaracao))
        if len(self.historico_desfazer) > _MAX_HISTORICO:
            self.historico_desfazer.pop(0)
        self.historico_refazer.clear()
        self._atualizar_botoes_historico()

    def desfazer(self) -> None:
        if not self.historico_desfazer or self.declaracao is None:
            return
        self.historico_refazer.append(copy.deepcopy(self.declaracao))
        self.declaracao = self.historico_desfazer.pop()
        self.atualizar_total_inserts()
        self.atualizar_resumo()
        self.atualizar_arvore()
        self.limpar_saida()
        self._atualizar_botoes_historico()
        self._atualizar_status("Alteração desfeita")

    def refazer(self) -> None:
        if not self.historico_refazer or self.declaracao is None:
            return
        self.historico_desfazer.append(copy.deepcopy(self.declaracao))
        if len(self.historico_desfazer) > _MAX_HISTORICO:
            self.historico_desfazer.pop(0)
        self.declaracao = self.historico_refazer.pop()
        self.atualizar_total_inserts()
        self.atualizar_resumo()
        self.atualizar_arvore()
        self.limpar_saida()
        self._atualizar_botoes_historico()
        self._atualizar_status("Alteração refeita")

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

    def salvar_saida(self):
        conteudo = self.txt_saida.get("1.0", "end").strip()
        if not conteudo:
            messagebox.showinfo("Info", "Nenhum INSERT gerado para salvar.")
            return
        caminho = filedialog.asksaveasfilename(
            title="Salvar INSERT gerado",
            defaultextension=".sql",
            filetypes=[
                ("Arquivos SQL", "*.sql"),
                ("Arquivos texto", "*.txt"),
                ("Todos os arquivos", "*.*"),
            ],
        )
        if not caminho:
            return
        try:
            with open(caminho, "w", encoding="utf-8", newline="\n") as f:
                f.write(conteudo)
                f.write("\n")
        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao salvar arquivo:\n{e}")
            return
        self._atualizar_status(f"SQL salvo em {caminho}")

    def atualizar_total_inserts(self):
        total = self.declaracao.quantidade_inserts_origem if self.declaracao else 0
        self.var_total_inserts.set(f"Total de INSERTs/REPLACEs detectados: {total}")

    def atualizar_resumo(self):
        if not self.declaracao:
            self.var_resumo.set("Nenhum INSERT carregado")
            return
        resumo = self.declaracao.resumo()
        problemas = self.declaracao.indices_linhas_com_quantidade_invalida()
        if problemas:
            amostra = ", ".join(str(i) for i in problemas[:10])
            if len(problemas) > 10:
                amostra += ", ..."
            resumo += f" | Conferir linhas: {amostra}"
        self.var_resumo.set(resumo)

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
        return eh_numero_sql(texto)

    def _normalizar_valor_editado(self, bruto: str) -> str:
        return normalizar_valor_editado(bruto)

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
            val_display_idx = self._editor_field_idx + 1
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
        iid = self.arvore.identify_row(evento.y)
        coluna = self.arvore.identify_column(evento.x)
        if not iid or not coluna:
            return
        try:
            idx_linha = int(iid)
            idx_coluna_visual = int(coluna.replace("#", ""))
        except ValueError:
            return

        # A primeira coluna visual e a coluna "Ln"; os campos SQL comecam na #2.
        idx_campo = idx_coluna_visual - 2
        if idx_campo < 0:
            return

        linha = self.declaracao.linhas[idx_linha]
        valor_atual = linha[idx_campo] if idx_campo < len(linha) else ""
        self.arvore.selection_set(iid)
        self._abrir_dialogo_alterar(
            campo_inicial_idx=idx_campo,
            linha_inicial_idx=idx_linha,
            valor_atual=valor_atual,
        )

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
        texto = None
        erros = []
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
            try:
                with open(caminho, "r", encoding=encoding) as f:
                    texto = f.read()
                break
            except UnicodeDecodeError as e:
                erros.append(f"{encoding}: {e}")
            except Exception as e:
                messagebox.showerror("Erro", f"Falha ao ler arquivo:\n{e}")
                return
        if texto is None:
            detalhe = "\n".join(erros[:4])
            messagebox.showerror(
                "Erro",
                "Nao foi possivel ler o arquivo com os encodings suportados "
                "(utf-8-sig, utf-8, cp1252, latin1).\n\n"
                f"Detalhes:\n{detalhe}",
            )
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
            self._limpar_historico()
            self.atualizar_total_inserts()
            self.atualizar_resumo()
            self._atualizar_status(f"Erro: {erro}")
            messagebox.showerror("Erro ao processar", str(erro))
            return
        self.declaracao = resultado
        self._limpar_historico()
        self.atualizar_total_inserts()
        self.atualizar_resumo()
        self.atualizar_arvore()
        self.limpar_saida()
        n = self.declaracao.quantidade_inserts_origem
        num_linhas = len(self.declaracao.linhas)
        problemas = self.declaracao.indices_linhas_com_quantidade_invalida()
        if problemas:
            self._atualizar_status(
                f"{n} statement(s) carregado(s) — tabela {self.declaracao.tabela} — "
                f"{len(problemas)} linha(s) com quantidade de valores divergente"
            )
            return
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
        linhas_problematicas = set(self.declaracao.indices_linhas_com_quantidade_invalida())

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
            if i in linhas_problematicas:
                tags.append("problema")
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
            self._registrar_estado_para_desfazer()
            self.declaracao.remover_colunas_por_indices(indices)
            self.atualizar_arvore()
            self.atualizar_resumo()
            self.limpar_saida()
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

        self._registrar_estado_para_desfazer()
        self.declaracao.remover_linha(idx)

        if not self.declaracao.linhas:
            self.atualizar_arvore()
            self.atualizar_resumo()
            self.limpar_saida()
            self._atualizar_status("Todas as linhas foram removidas")
            return

        self.atualizar_arvore()
        self.atualizar_resumo()
        self.limpar_saida()
        self._atualizar_status(f"Linha {idx} excluída")

    # =========================
    # Alteração em lote
    # =========================
    def _abrir_dialogo_alterar(
            self,
            campo_inicial_idx: int | None = None,
            linha_inicial_idx: int | None = None,
            valor_atual: str = ""):
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
            idx_campo_inicial = campo_inicial_idx if campo_inicial_idx is not None else 0
            cmb_campo.current(idx_campo_inicial if 0 <= idx_campo_inicial < len(campos) else 0)

        # Valor atual
        ttk.Label(frame, text="Valor atual:").grid(row=1, column=0, sticky="w", pady=(0, 10))
        var_valor_atual = tk.StringVar(value=valor_atual)
        ent_valor_atual = ttk.Entry(frame, textvariable=var_valor_atual, width=36, state="readonly")
        ent_valor_atual.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(10, 0), pady=(0, 10))

        # Novo valor
        ttk.Label(frame, text="Novo valor:").grid(row=2, column=0, sticky="w", pady=(0, 10))
        var_valor = tk.StringVar(value="''")
        ent_valor = ttk.Entry(frame, textvariable=var_valor, width=36)
        ent_valor.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(10, 0), pady=(0, 10))

        # Separador
        ttk.Separator(frame, orient="horizontal").grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=(0, 10)
        )

        # Escopo
        ttk.Label(frame, text="Aplicar em:").grid(row=4, column=0, sticky="nw", pady=(0, 4))

        var_escopo = tk.StringVar(value="linha" if linha_inicial_idx is not None else "todas")

        ttk.Radiobutton(
            frame, text="Todas as linhas", variable=var_escopo, value="todas"
        ).grid(row=4, column=1, columnspan=2, sticky="w", padx=(10, 0))

        frame_linha_esp = ttk.Frame(frame)
        frame_linha_esp.grid(row=5, column=1, columnspan=2, sticky="w", padx=(10, 0), pady=(4, 14))

        ttk.Radiobutton(
            frame_linha_esp, text="Linha específica:", variable=var_escopo, value="linha"
        ).pack(side="left")

        var_linha_dlg = tk.IntVar(value=linha_inicial_idx or 0)
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

        def _atualizar_valor_atual_dialogo(*_):
            try:
                idx_linha = int(var_linha_dlg.get())
                idx_campo = campos.index(var_campo.get())
            except (ValueError, tk.TclError):
                var_valor_atual.set("")
                return
            if 0 <= idx_linha < num_linhas:
                linha = self.declaracao.linhas[idx_linha]
                var_valor_atual.set(linha[idx_campo] if idx_campo < len(linha) else "")

        cmb_campo.bind("<<ComboboxSelected>>", _atualizar_valor_atual_dialogo)
        var_linha_dlg.trace_add("write", _atualizar_valor_atual_dialogo)
        _atualizar_valor_atual_dialogo()

        # Separador
        ttk.Separator(frame, orient="horizontal").grid(
            row=6, column=0, columnspan=3, sticky="ew", pady=(0, 10)
        )

        # Botões
        frame_btns = ttk.Frame(frame)
        frame_btns.grid(row=7, column=0, columnspan=3, sticky="e")

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
                ok = messagebox.askyesno(
                    "Confirmar troca",
                    f"Você confirma a troca do valor da coluna {campo_sel} "
                    f"para o valor {novo_valor} em {num_linhas} linha(s)?",
                    parent=janela,
                )
                if not ok:
                    return
                self._registrar_estado_para_desfazer()
                for ln in self.declaracao.linhas:
                    if field_idx < len(ln):
                        ln[field_idx] = novo_valor
                self._atualizar_status(
                    f"'{campo_sel}' atualizado em {num_linhas} linha(s) → {novo_valor}"
                )
            else:
                idx_linha = var_linha_dlg.get()
                if idx_linha < 0 or idx_linha >= num_linhas:
                    messagebox.showwarning(
                        "Atenção",
                        f"Linha {idx_linha} não existe. Total: {num_linhas} linha(s).",
                        parent=janela,
                    )
                    return
                ok = messagebox.askyesno(
                    "Confirmar troca",
                    f"Você confirma a troca do valor da coluna {campo_sel} "
                    f"para o valor {novo_valor} na linha {idx_linha}?",
                    parent=janela,
                )
                if not ok:
                    return
                self._registrar_estado_para_desfazer()
                if 0 <= idx_linha < num_linhas:
                    ln = self.declaracao.linhas[idx_linha]
                    if field_idx < len(ln):
                        ln[field_idx] = novo_valor
                self._atualizar_status(
                    f"'{campo_sel}' atualizado na linha {idx_linha} → {novo_valor}"
                )

            self.atualizar_arvore()
            self.atualizar_resumo()
            self.limpar_saida()
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

        try:
            self.declaracao.validar_para_geracao()
        except LinhasInvalidasError as e:
            messagebox.showwarning(
                "Aviso",
                f"{e}\n\nCorrija o INSERT antes de gerar o SQL.",
            )
            self._atualizar_status("Geracao bloqueada: existem linhas invalidas")
            return

        if self.declaracao.quantidade_inserts_origem > 1:
            saida = self.declaracao.para_sql_multiplos()
        else:
            saida = self.declaracao.para_sql()

        self.txt_saida.configure(state="normal")
        self.txt_saida.insert("1.0", saida)
        self.txt_saida.configure(state="disabled")
        self._atualizar_status(
            f"INSERT gerado — {len(self.declaracao.linhas)} linha(s), "
            f"{len(self.declaracao.nomes_campos())} coluna(s)"
        )


def iniciar_interface():
    raiz = tk.Tk()
    raiz.geometry("1280x720+80+40")
    caminho_icone = _caminho_recurso(_CAMINHO_ICONE)
    if caminho_icone.exists():
        try:
            raiz.iconbitmap(str(caminho_icone))
        except tk.TclError:
            pass
    estilo = ttk.Style(raiz)
    try:
        estilo.theme_use("clam")
    except Exception:
        pass
    configurar_estilos(estilo)
    InterfaceInsert(raiz)
    raiz.minsize(980, 680)
    try:
        raiz.state("zoomed")
    except tk.TclError:
        pass
    try:
        raiz.mainloop()
    except KeyboardInterrupt:
        raiz.destroy()
