"""Tratamento global de erros inesperados.

O exe roda sem console (console=False no .spec): sem isto, um erro num botao
ou numa thread some sem deixar rastro e, para o operador, o botao "nao faz
nada". Aqui todo erro nao tratado vai para logs\\erros.log (com traceback),
gera uma linha na Auditoria e uma mensagem simples na tela.
"""

import os
import queue
import threading
import time
import traceback
from datetime import datetime
from tkinter import Tk, messagebox

from app import __version__, logging_utils
from app.runtime import logs_directory

ERROR_LOG_NAME = "erros.log"
POPUP_COOLDOWN_SECONDS = 30
POLL_MS = 1000
_WRITE_LOCK = threading.Lock()


def error_log_path():
    return logs_directory() / ERROR_LOG_NAME


def _current_user():
    return os.environ.get("USERNAME") or os.environ.get("USER") or "desconhecido"


def _summary(exc_type, exc_value):
    text = str(exc_value).replace("\r", " ").replace("\n", " ").strip()
    if len(text) > 200:
        text = text[:200] + "..."
    return f"{exc_type.__name__}: {text}" if text else exc_type.__name__


def record_error(exc_type, exc_value, exc_traceback, origin):
    """Grava o erro completo em erros.log e uma linha na Auditoria. Nunca levanta."""
    path = error_log_path()
    details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    header = (
        f"[{datetime.now():%Y-%m-%d %H:%M:%S}] versao={__version__} "
        f"usuario={_current_user()} origem={origin}"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK, path.open("a", encoding="utf-8") as log:
            log.write(f"{header}\n{details}\n")
    except OSError:
        pass
    try:
        logging_utils.write_log(
            f"Sistema erro_inesperado | usuario={_current_user()} | "
            f"origem={origin} | motivo={_summary(exc_type, exc_value)} (detalhes em {ERROR_LOG_NAME})"
        )
    except OSError:
        pass
    return path


def user_message(exc_type, exc_value, path):
    return (
        "Ocorreu um erro inesperado e a acao pode nao ter sido concluida.\n\n"
        f"Erro: {_summary(exc_type, exc_value)}\n\n"
        f"Os detalhes foram gravados em:\n{path}\n\n"
        "Se acontecer de novo, envie esse arquivo ao responsavel pelo sistema."
    )


class ErrorReporter:
    """Instala os ganchos de erro do Tk e das threads numa janela principal."""

    def __init__(self, root, show=None, clock=time.monotonic):
        self.root = root
        self._show = show or (lambda title, text: messagebox.showerror(title, text, parent=root))
        self._clock = clock
        self._last_shown = {}
        self._pending = queue.Queue()

    def install(self):
        self.root.report_callback_exception = self.on_tk_error
        threading.excepthook = self.on_thread_error
        self.root.after(POLL_MS, self._poll)
        return self

    def on_tk_error(self, exc_type, exc_value, exc_traceback):
        """Erro em botao, evento ou after(): roda na thread do Tk."""
        path = record_error(exc_type, exc_value, exc_traceback, "interface")
        self._notify(exc_type, exc_value, path)

    def on_thread_error(self, args):
        """Erro nao tratado numa thread: grava e deixa o aviso para a thread do Tk."""
        if args.exc_type is SystemExit:
            return
        origin = f"thread {getattr(args.thread, 'name', '?')}"
        path = record_error(args.exc_type, args.exc_value, args.exc_traceback, origin)
        self._pending.put((args.exc_type, args.exc_value, path))

    def _poll(self):
        try:
            while True:
                self._notify(*self._pending.get_nowait())
        except queue.Empty:
            pass
        try:
            self.root.after(POLL_MS, self._poll)
        except Exception:
            pass  # janela fechada

    def _notify(self, exc_type, exc_value, path):
        key = (exc_type.__name__, str(exc_value))
        now = self._clock()
        last = self._last_shown.get(key)
        if last is not None and now - last < POPUP_COOLDOWN_SECONDS:
            return  # mesmo erro repetido: so grava, nao abre outra janela
        self._last_shown[key] = now
        self._show("Erro inesperado", user_message(exc_type, exc_value, path))


def report_startup_error(exc_type, exc_value, exc_traceback, show=None):
    """Erro antes da janela principal existir: grava e mostra numa janela propria."""
    path = record_error(exc_type, exc_value, exc_traceback, "abertura do sistema")
    text = (
        "O sistema nao conseguiu abrir.\n\n"
        f"Erro: {_summary(exc_type, exc_value)}\n\n"
        f"Os detalhes foram gravados em:\n{path}"
    )
    if show is not None:
        show("Erro ao abrir o sistema", text)
        return path
    try:
        hidden = Tk()
        hidden.withdraw()
        messagebox.showerror("Erro ao abrir o sistema", text, parent=hidden)
        hidden.destroy()
    except Exception:
        pass
    return path
