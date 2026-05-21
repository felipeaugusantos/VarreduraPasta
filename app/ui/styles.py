from tkinter import ttk

COR_FUNDO = "#eef2f7"
COR_PAINEL = "#ffffff"
COR_BORDA = "#cbd5e1"
COR_TEXTO = "#111827"
COR_TEXTO_SUAVE = "#64748b"
COR_PRIMARIA = "#0f62a9"
COR_PRIMARIA_HOVER = "#0b4f86"
COR_PRIMARIA_PRESS = "#083a63"
COR_PERIGO = "#b42318"
COR_PERIGO_HOVER = "#912018"
COR_PERIGO_PRESS = "#7a1a14"
COR_LINHA_PAR = "#f8fafc"
COR_LINHA_IMPAR = "#ffffff"
COR_SELECAO = "#dbeafe"
COR_NULO = "#94a3b8"
COR_STATUS = "#475569"
FONTE_PADRAO = ("Segoe UI", 9)
FONTE_TITULO = ("Segoe UI Semibold", 14)
FONTE_MONO = ("Consolas", 9)


def configurar_estilos(estilo: ttk.Style) -> None:
    estilo.configure(".", font=FONTE_PADRAO)
    estilo.configure("App.TFrame", background=COR_FUNDO)
    estilo.configure("Panel.TFrame", background=COR_PAINEL)
    estilo.configure("Toolbar.TFrame", background=COR_PAINEL)
    estilo.configure("TLabel", background=COR_FUNDO, foreground=COR_TEXTO)
    estilo.configure("Panel.TLabel", background=COR_PAINEL, foreground=COR_TEXTO)
    estilo.configure("Muted.TLabel", background=COR_PAINEL, foreground=COR_TEXTO_SUAVE)
    estilo.configure("Status.TLabel", background=COR_FUNDO, foreground=COR_STATUS, font=("Segoe UI", 8))
    estilo.configure("Title.TLabel", background=COR_FUNDO, foreground=COR_TEXTO, font=FONTE_TITULO)
    estilo.configure(
        "Card.TLabelframe",
        background=COR_PAINEL,
        bordercolor=COR_BORDA,
        relief="solid",
        padding=8,
    )
    estilo.configure(
        "Card.TLabelframe.Label",
        background=COR_FUNDO,
        foreground=COR_TEXTO,
        font=("Segoe UI Semibold", 9),
    )
    estilo.configure(
        "Treeview",
        background=COR_PAINEL,
        fieldbackground=COR_PAINEL,
        foreground=COR_TEXTO,
        bordercolor=COR_BORDA,
        rowheight=24,
        font=FONTE_MONO,
    )
    estilo.configure(
        "Treeview.Heading",
        background="#e2e8f0",
        foreground=COR_TEXTO,
        bordercolor=COR_BORDA,
        relief="flat",
        font=("Segoe UI Semibold", 8),
        padding=(6, 5),
    )
    estilo.map(
        "Treeview",
        background=[("selected", COR_SELECAO)],
        foreground=[("selected", COR_TEXTO)],
    )
    estilo.configure("TButton", padding=(9, 5))
    estilo.configure("TEntry", padding=(6, 4))
    estilo.configure("TSpinbox", padding=(4, 3))

    estilo.configure(
        "Primario.TButton",
        background=COR_PRIMARIA,
        foreground="white",
        borderwidth=1,
        bordercolor=COR_PRIMARIA,
        lightcolor=COR_PRIMARIA,
        darkcolor=COR_PRIMARIA_HOVER,
        relief="flat",
        padding=(12, 6),
        font=("Segoe UI Semibold", 9),
    )
    estilo.map(
        "Primario.TButton",
        background=[
            ("active", COR_PRIMARIA_HOVER),
            ("pressed", COR_PRIMARIA_PRESS),
            ("disabled", "#cccccc"),
        ],
        foreground=[("disabled", "#888888")],
        relief=[("pressed", "flat")],
    )

    estilo.configure(
        "Perigo.TButton",
        background=COR_PERIGO,
        foreground="white",
        borderwidth=1,
        bordercolor=COR_PERIGO,
        lightcolor=COR_PERIGO,
        darkcolor=COR_PERIGO_HOVER,
        relief="flat",
        padding=(10, 5),
        font=("Segoe UI", 9),
    )
    estilo.map(
        "Perigo.TButton",
        background=[
            ("active", COR_PERIGO_HOVER),
            ("pressed", COR_PERIGO_PRESS),
            ("disabled", "#cccccc"),
        ],
        foreground=[("disabled", "#888888")],
        relief=[("pressed", "flat")],
    )
