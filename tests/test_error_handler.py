import sys
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from app import error_handler
from app.error_handler import ErrorReporter, record_error, report_startup_error


def raise_and_capture(error):
    try:
        raise error
    except type(error):
        return sys.exc_info()


class FakeRoot:
    def __init__(self):
        self.after_calls = []

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class RecordErrorTests(unittest.TestCase):
    def test_writes_traceback_to_error_log_and_audit_line(self):
        exc_info = raise_and_capture(KeyError("campo_x"))

        with patch.dict("os.environ", {"USERNAME": "felipe.santos"}), \
             patch("app.error_handler.logging_utils.write_log") as write_log:
            path = record_error(*exc_info, "interface")

        content = path.read_text(encoding="utf-8")
        self.assertEqual(path.name, "erros.log")
        self.assertIn("usuario=felipe.santos origem=interface", content)
        self.assertIn(f"versao={error_handler.__version__}", content)
        self.assertIn("Traceback", content)
        self.assertIn("KeyError: 'campo_x'", content)
        audit = write_log.call_args.args[0]
        self.assertIn("Sistema erro_inesperado | usuario=felipe.santos", audit)
        self.assertIn("motivo=KeyError: 'campo_x' (detalhes em erros.log)", audit)

    def test_never_raises_when_log_folder_is_unavailable(self):
        exc_info = raise_and_capture(ValueError("x"))
        with patch("app.error_handler.error_log_path") as log_path, \
             patch("app.error_handler.logging_utils.write_log", side_effect=OSError("disco cheio")):
            log_path.return_value.parent.mkdir.side_effect = OSError("sem permissao")
            record_error(*exc_info, "interface")


class ErrorReporterTests(unittest.TestCase):
    def setUp(self):
        self.shown = []
        self.clock = FakeClock()
        self.reporter = ErrorReporter(
            FakeRoot(), show=lambda title, text: self.shown.append(text), clock=self.clock
        )
        patcher = patch("app.error_handler.logging_utils.write_log")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_interface_error_is_shown_with_log_path(self):
        self.reporter.on_tk_error(*raise_and_capture(ZeroDivisionError("division by zero")))

        self.assertEqual(len(self.shown), 1)
        self.assertIn("Erro: ZeroDivisionError: division by zero", self.shown[0])
        self.assertIn("erros.log", self.shown[0])

    def test_same_error_repeated_shows_only_once_within_cooldown(self):
        for _ in range(5):
            self.reporter.on_tk_error(*raise_and_capture(ValueError("mesmo")))
        self.reporter.on_tk_error(*raise_and_capture(ValueError("outro")))
        self.assertEqual(len(self.shown), 2)

        self.clock.now += error_handler.POPUP_COOLDOWN_SECONDS + 1
        self.reporter.on_tk_error(*raise_and_capture(ValueError("mesmo")))
        self.assertEqual(len(self.shown), 3)

    def test_thread_error_is_shown_later_by_the_interface_thread(self):
        exc_type, exc_value, exc_tb = raise_and_capture(RuntimeError("na thread"))
        args = threading.ExceptHookArgs((exc_type, exc_value, exc_tb, threading.current_thread()))

        self.reporter.on_thread_error(args)
        self.assertEqual(self.shown, [])  # nao chama o Tk fora da thread dele
        self.reporter._poll()
        self.assertEqual(len(self.shown), 1)
        self.assertIn("RuntimeError: na thread", self.shown[0])

    def test_system_exit_in_thread_is_ignored(self):
        exc_info = raise_and_capture(SystemExit(0))
        args = threading.ExceptHookArgs((*exc_info, threading.current_thread()))
        with patch("app.error_handler.record_error") as record:
            self.reporter.on_thread_error(args)
        record.assert_not_called()

    def test_startup_error_is_recorded_and_shown(self):
        shown = []
        path = report_startup_error(
            *raise_and_capture(ImportError("modulo faltando")),
            show=lambda title, text: shown.append((title, text)),
        )
        self.assertEqual(shown[0][0], "Erro ao abrir o sistema")
        self.assertIn("ImportError: modulo faltando", shown[0][1])
        self.assertIn("origem=abertura do sistema", path.read_text(encoding="utf-8"))


class RealTkIntegrationTests(unittest.TestCase):
    """Com Tk de verdade: erro num callback e numa thread chegam ao aviso."""

    def setUp(self):
        original_hook = threading.excepthook
        self.addCleanup(setattr, threading, "excepthook", original_hook)
        patcher = patch("app.error_handler.logging_utils.write_log")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.shown = []
        ErrorReporter(self.root, show=lambda title, text: self.shown.append(text)).install()

    def pump(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(0.02)

    def test_error_in_button_callback_is_reported(self):
        button = tk.Button(self.root, command=lambda: 1 / 0)
        button.invoke()

        self.assertEqual(len(self.shown), 1)
        self.assertIn("ZeroDivisionError", self.shown[0])

    def test_error_in_background_thread_is_reported(self):
        def worker():
            raise LookupError("falha na thread")

        thread = threading.Thread(target=worker, name="varredura-teste")
        thread.start()
        thread.join()
        self.pump(error_handler.POLL_MS / 1000 + 0.5)

        self.assertEqual(len(self.shown), 1)
        self.assertIn("LookupError: falha na thread", self.shown[0])


if __name__ == "__main__":
    unittest.main()
