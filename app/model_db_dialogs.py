"""Janelas do banco modelo: login do operador e configuracao do servidor."""

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from app import model_db, theme
from app.settings import load_model_db, save_model_db


def ask_credentials(parent, username="", message=""):
    """Pede usuario e senha do banco. Retorna (usuario, senha) ou None se cancelar.

    A senha so existe em memoria durante a execucao; nada e gravado.
    """
    window = tk.Toplevel(parent)
    window.title("Banco modelo - login")
    theme.size_window(window, 400, 230 if message else 200)
    window.resizable(False, False)
    window.transient(parent)

    frame = ttk.Frame(window, padding=14)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(1, weight=1)
    row = 0
    if message:
        ttk.Label(frame, text=message, wraplength=360, justify="left").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )
        row += 1
    user_var = tk.StringVar(value=username)
    password_var = tk.StringVar()
    ttk.Label(frame, text="Usuário:").grid(row=row, column=0, sticky="w")
    user_entry = ttk.Entry(frame, textvariable=user_var)
    user_entry.grid(row=row, column=1, sticky="ew", padx=(8, 0))
    ttk.Label(frame, text="Senha:").grid(row=row + 1, column=0, sticky="w", pady=(8, 0))
    password_entry = ttk.Entry(frame, textvariable=password_var, show="*")
    password_entry.grid(row=row + 1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))
    ttk.Label(
        frame,
        text="Digitados só para esta execução; a senha não é gravada.",
        style="Muted.TLabel",
    ).grid(row=row + 2, column=0, columnspan=2, sticky="w", pady=(8, 0))

    answer = {"value": None}

    def confirm(_event=None):
        if not user_var.get().strip():
            messagebox.showwarning("Banco modelo", "Informe o usuário.", parent=window)
            return
        answer["value"] = (user_var.get().strip(), password_var.get())
        window.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=row + 3, column=0, columnspan=2, sticky="e", pady=(12, 0))
    ttk.Button(buttons, text="Continuar", style="Primary.TButton", command=confirm).grid(
        row=0, column=0, padx=(0, 8)
    )
    ttk.Button(buttons, text="Cancelar", command=window.destroy).grid(row=0, column=1)
    window.bind("<Return>", confirm)
    window.bind("<Escape>", lambda _event: window.destroy())

    (password_entry if username else user_entry).focus_set()
    window.grab_set()
    parent.wait_window(window)
    return answer["value"]


def open_db_settings(parent):
    """Servidor, porta e banco modelo, com teste de conexao."""
    current = load_model_db()
    window = tk.Toplevel(parent)
    window.title("Banco modelo - configuração")
    theme.size_window(window, 520, 260)
    window.resizable(False, False)
    window.transient(parent)

    frame = ttk.Frame(window, padding=14)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(1, weight=1)
    host_var = tk.StringVar(value=current["host"])
    port_var = tk.StringVar(value=str(current["port"]))
    database_var = tk.StringVar(value=current["database"])
    status_var = tk.StringVar(value="")

    for row, (caption, variable) in enumerate(
        (("Servidor:", host_var), ("Porta:", port_var), ("Banco modelo:", database_var))
    ):
        ttk.Label(frame, text=caption).grid(row=row, column=0, sticky="w", pady=(0 if row == 0 else 8, 0))
        ttk.Entry(frame, textvariable=variable).grid(
            row=row, column=1, sticky="ew", padx=(8, 0), pady=(0 if row == 0 else 8, 0)
        )
    ttk.Label(
        frame,
        text="Usuário e senha são pedidos a cada execução e não ficam gravados.",
        style="Muted.TLabel",
    ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
    ttk.Label(frame, textvariable=status_var, wraplength=480, justify="left").grid(
        row=4, column=0, columnspan=2, sticky="w", pady=(10, 0)
    )

    def values():
        port = port_var.get().strip()
        if not port.isdigit() or not (0 < int(port) < 65536):
            messagebox.showwarning("Banco modelo", "Porta inválida.", parent=window)
            return None
        return host_var.get().strip(), int(port), database_var.get().strip()

    def save():
        data = values()
        if data is None:
            return False
        save_model_db(data[0], data[1], data[2], load_model_db()["username"])
        return True

    def test():
        data = values()
        if data is None:
            return
        credentials = ask_credentials(window, load_model_db()["username"], "Informe um usuário do banco para testar.")
        if credentials is None:
            return
        settings = {"host": data[0], "port": data[1], "database": data[2]}
        status_var.set("Testando conexão...")
        result_queue = queue.Queue()

        def worker():
            try:
                result_queue.put(model_db.test_connection(settings, *credentials))
            except Exception as error:
                result_queue.put((False, f"Falha no teste: {error}"))

        def poll():
            if not window.winfo_exists():
                return
            try:
                ok, message = result_queue.get_nowait()
            except queue.Empty:
                window.after(100, poll)
                return
            status_var.set(("✔ " if ok else "✖ ") + message)

        threading.Thread(target=worker, daemon=True).start()
        window.after(100, poll)

    def save_and_close():
        if save():
            window.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(14, 0))
    ttk.Button(buttons, text="Testar conexão", command=test).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(buttons, text="Salvar", style="Primary.TButton", command=save_and_close).grid(
        row=0, column=1, padx=(0, 8)
    )
    ttk.Button(buttons, text="Cancelar", command=window.destroy).grid(row=0, column=2)
    window.grab_set()
    return window
