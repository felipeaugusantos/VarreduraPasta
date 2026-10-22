"""Tema visual da aplicacao (somente ttk/tkinter da biblioteca padrao)."""

from tkinter import font as tkfont
from tkinter import ttk


COLORS = {
    "bg": "#f3f5f9",
    "surface": "#ffffff",
    "border": "#d8dee8",
    "text": "#1f2937",
    "muted": "#6b7280",
    "primary": "#2563eb",
    "primary_active": "#1d4ed8",
    "primary_disabled": "#93b4f5",
    "header": "#1e293b",
    "header_text": "#f8fafc",
    "header_muted": "#94a3b8",
    "heading_bg": "#eef2f7",
    "button_hover": "#eef2f7",
    "ok": "#15803d",
    "warn": "#b45309",
    "error": "#b91c1c",
    "closing": "#2563eb",
    "zip": "#a16207",
    # Fundos das linhas da tabela (discretos: a cor forte fica no simbolo do Status).
    "row_warning": "#fff7e6",
    "row_error": "#fdf0f0",
    "row_zip_below": "#ffe8a3",
    "selection": "#2563eb",
}

FONT_FAMILY = "Segoe UI"


def apply_theme(root):
    """Configura fontes e estilos ttk. Deve ser chamado uma vez, no Tk principal."""
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
        tkfont.nametofont(name).configure(family=FONT_FAMILY, size=10)
    tkfont.nametofont("TkHeadingFont").configure(family=FONT_FAMILY, size=10, weight="bold")

    fonts = {
        "title": tkfont.Font(root, family=FONT_FAMILY, size=15, weight="bold"),
        "subtitle": tkfont.Font(root, family=FONT_FAMILY, size=9),
        "card_value": tkfont.Font(root, family=FONT_FAMILY, size=18, weight="bold"),
        "card_label": tkfont.Font(root, family=FONT_FAMILY, size=9),
        "section": tkfont.Font(root, family=FONT_FAMILY, size=10, weight="bold"),
        "panel_title": tkfont.Font(root, family=FONT_FAMILY, size=11, weight="bold"),
    }
    root.configure(background=COLORS["bg"])

    style = ttk.Style(root)
    style.theme_use("clam")
    c = COLORS

    style.configure(".", background=c["bg"], foreground=c["text"], bordercolor=c["border"])
    style.configure("TFrame", background=c["bg"])
    style.configure("TLabel", background=c["bg"], foreground=c["text"])
    style.configure("Muted.TLabel", foreground=c["muted"])

    # Cabecalho escuro.
    style.configure("Header.TFrame", background=c["header"])
    style.configure("Header.TLabel", background=c["header"], foreground=c["header_text"])
    style.configure("HeaderTitle.TLabel", background=c["header"], foreground=c["header_text"], font=fonts["title"])
    style.configure("HeaderMuted.TLabel", background=c["header"], foreground=c["header_muted"], font=fonts["subtitle"])

    # Cartoes de contadores e painel de detalhes.
    style.configure("Card.TFrame", background=c["surface"], relief="solid", borderwidth=1)
    style.configure("Card.TLabel", background=c["surface"], foreground=c["text"])
    style.configure("CardMuted.TLabel", background=c["surface"], foreground=c["muted"], font=fonts["card_label"])
    style.configure("CardValue.TLabel", background=c["surface"], font=fonts["card_value"])
    style.configure("PanelTitle.TLabel", background=c["surface"], foreground=c["text"], font=fonts["panel_title"])
    style.configure("PanelSection.TLabel", background=c["surface"], foreground=c["muted"], font=fonts["section"])
    style.configure("CardBody.TFrame", background=c["surface"])
    for name in ("ok", "warn", "error", "closing", "zip", "primary"):
        style.configure(f"Card{name.title()}.TLabel", background=c["surface"], foreground=c[name])
        style.configure(
            f"CardValue{name.title()}.TLabel",
            background=c["surface"],
            foreground=c[name],
            font=fonts["card_value"],
        )

    # Grupos de botoes.
    style.configure("Group.TLabelframe", background=c["bg"], relief="flat", borderwidth=0)
    style.configure("Group.TLabelframe.Label", background=c["bg"], foreground=c["muted"], font=fonts["section"])

    # Botoes.
    style.configure(
        "TButton",
        background=c["surface"],
        foreground=c["text"],
        bordercolor=c["border"],
        lightcolor=c["surface"],
        darkcolor=c["surface"],
        focuscolor=c["surface"],
        padding=(12, 6),
    )
    style.map(
        "TButton",
        background=[("disabled", c["bg"]), ("pressed", c["border"]), ("active", c["button_hover"])],
        foreground=[("disabled", "#a3abb8")],
        bordercolor=[("disabled", "#e3e7ee")],
    )
    style.configure(
        "Primary.TButton",
        background=c["primary"],
        foreground="#ffffff",
        bordercolor=c["primary"],
        lightcolor=c["primary"],
        darkcolor=c["primary"],
        focuscolor=c["primary"],
    )
    style.map(
        "Primary.TButton",
        background=[("disabled", c["primary_disabled"]), ("pressed", c["primary_active"]), ("active", c["primary_active"])],
        foreground=[("disabled", "#eef3fe")],
        bordercolor=[("disabled", c["primary_disabled"]), ("active", c["primary_active"])],
    )

    # Campos.
    style.configure("TEntry", fieldbackground=c["surface"], bordercolor=c["border"], padding=(6, 4))
    style.configure("TCombobox", fieldbackground=c["surface"], background=c["surface"], bordercolor=c["border"], padding=(6, 4))
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", c["surface"])],
        background=[("active", c["button_hover"])],
    )
    style.configure(
        "TCombobox",
        arrowcolor=c["muted"],
        arrowsize=12,
        lightcolor=c["surface"],
        darkcolor=c["surface"],
    )
    for orientation in ("Vertical", "Horizontal"):
        style.configure(
            f"{orientation}.TScrollbar",
            background=c["heading_bg"],
            troughcolor=c["surface"],
            bordercolor=c["border"],
            lightcolor=c["heading_bg"],
            darkcolor=c["heading_bg"],
            arrowcolor=c["muted"],
            gripcount=0,
        )
        style.map(f"{orientation}.TScrollbar", background=[("active", c["border"])])

    # Widgets tk classicos (Listbox/Text) usados em janelas secundarias.
    for widget in ("Listbox", "Text"):
        root.option_add(f"*{widget}.background", c["surface"])
        root.option_add(f"*{widget}.foreground", c["text"])
        root.option_add(f"*{widget}.relief", "solid")
        root.option_add(f"*{widget}.borderWidth", 1)
        root.option_add(f"*{widget}.highlightThickness", 0)
        root.option_add(f"*{widget}.selectBackground", c["selection"])
        root.option_add(f"*{widget}.selectForeground", "#ffffff")

    # Tabela.
    style.configure(
        "Treeview",
        background=c["surface"],
        fieldbackground=c["surface"],
        foreground=c["text"],
        bordercolor=c["border"],
        rowheight=28,
    )
    style.map(
        "Treeview",
        background=[("selected", c["selection"])],
        foreground=[("selected", "#ffffff")],
    )
    style.configure(
        "Treeview.Heading",
        background=c["heading_bg"],
        foreground=c["text"],
        bordercolor=c["border"],
        relief="flat",
        padding=(6, 6),
    )
    style.map("Treeview.Heading", background=[("active", c["border"])])

    style.configure("Status.TFrame", background=c["surface"])
    style.configure("Status.TLabel", background=c["surface"], foreground=c["muted"])
    return fonts


MAX_GROWTH = 1.3


def fitted_dimension(base, requested, screen_limit, max_growth=MAX_GROWTH):
    """Cresce ate o pedido quando falta pouco (botoes cortados pelo tema).

    Pedidos muito maiores vem de tabelas largas/altas, que ja rolam: nesse caso
    mantem o tamanho original. Nunca passa do limite da tela.
    """
    if base < requested <= base * max_growth:
        return min(requested, screen_limit)
    return min(base, screen_limit)


def size_window(window, width, height, margin=40):
    """Define o tamanho inicial e, apos montar a janela, ajusta ao conteudo."""
    window.geometry(f"{width}x{height}")

    def fit_to_content():
        try:
            if not window.winfo_exists():
                return
            window.update_idletasks()
            fitted_width = fitted_dimension(
                width, window.winfo_reqwidth(), window.winfo_screenwidth() - margin
            )
            fitted_height = fitted_dimension(
                height, window.winfo_reqheight(), window.winfo_screenheight() - margin * 2
            )
            window.geometry(f"{fitted_width}x{fitted_height}")
        except Exception:
            # Janela fechada antes do ajuste; nada a fazer.
            pass

    window.after_idle(fit_to_content)
