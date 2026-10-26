import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from app.autcom_test import (
    _closing_note,
    _select_login_window,
    _window_details_for_process,
    AutcomTestResult,
    close_autcom,
    login_points,
    resolve_test_layout,
    run_autcom_test,
    synchronize_zip_files,
    test_folder_name,
    validate_login_completion,
    validate_process_startup,
)


class AutcomTestMappingTests(unittest.TestCase):
    def test_version_selects_branch_and_sub_branch(self):
        self.assertEqual(
            test_folder_name("379.48.2.30.74.111.181"),
            "PastaTesteCopia_74_111",
        )
        self.assertEqual(
            test_folder_name("379.48.2.30.74.150.90_CLOUD"),
            "PastaTesteCopia_74_150",
        )

    def test_invalid_version_is_rejected(self):
        with self.assertRaises(ValueError):
            test_folder_name("versao-invalida")

    def test_layout_uses_version_base_directory(self):
        layout = resolve_test_layout(
            Path(r"C:\VERSOES_FECHADAS") / "379.48.2.30.74.111.181"
        )
        self.assertEqual(
            layout.root.name.lower(),
            "pastatestecopia_74_111",
        )
        self.assertEqual(layout.exe_directory.name, "exe")
        self.assertEqual(layout.bat_path.name, "CopiaArquivos.bat")
        self.assertEqual(layout.autcom_path.name, "Autcom.exe")

    def test_login_points_follow_reference_screen_proportions(self):
        points = login_points(696, 384)

        self.assertEqual(points["usuario"], (111, 157))
        self.assertEqual(points["senha"], (111, 211))
        self.assertEqual(points["empresa"], (111, 265))
        self.assertEqual(points["login"], (111, 336))

    def test_single_custom_window_can_be_used_as_login(self):
        custom_window = SimpleNamespace(
            handle=111,
            class_name="TfrmIdentificacao",
            is_hung=False,
            title="Autcom",
        )

        self.assertIs(
            _select_login_window([custom_window]),
            custom_window,
        )

    def test_principal_and_dialog_windows_are_not_used_as_login(self):
        windows = [
            SimpleNamespace(
                handle=111,
                class_name="TfrmPrincipal",
                is_hung=False,
                title="Autcom",
            ),
            SimpleNamespace(
                handle=222,
                class_name="#32770",
                is_hung=False,
                title="Aviso",
            ),
        ]

        self.assertIsNone(_select_login_window(windows))


class ZipSynchronizationTests(unittest.TestCase):
    def test_destination_receives_exact_source_zip_set(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "versao"
            destination = root / "teste" / "exe"
            source.mkdir()
            destination.mkdir(parents=True)
            (source / "Autcom.zip").write_bytes(b"novo-autcom")
            (source / "AutBan.zip").write_bytes(b"novo-autban")
            (destination / "antigo.zip").write_bytes(b"antigo")

            copied = synchronize_zip_files(source, destination)

            self.assertEqual(
                sorted(item.name for item in copied),
                ["AutBan.zip", "Autcom.zip"],
            )
            self.assertEqual(
                sorted(item.name for item in destination.glob("*.zip")),
                ["AutBan.zip", "Autcom.zip"],
            )
            self.assertEqual(
                (destination / "Autcom.zip").read_bytes(),
                b"novo-autcom",
            )

    def test_source_without_zip_is_rejected(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "versao"
            destination = root / "teste" / "exe"
            source.mkdir()
            destination.mkdir(parents=True)

            with self.assertRaisesRegex(OSError, "Nenhum arquivo ZIP"):
                synchronize_zip_files(source, destination)


class ProcessStartupTests(unittest.TestCase):
    @patch("app.autcom_test._visible_windows_for_process")
    def test_visible_responsive_window_is_success(self, windows_mock):
        windows_mock.return_value = [("AUTCOM", False)]
        process = SimpleNamespace(pid=123, poll=lambda: None)

        result = validate_process_startup(
            process,
            timeout=1,
            stability_seconds=0,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.window_title, "AUTCOM")

    def test_early_process_exit_is_failure(self):
        process = SimpleNamespace(pid=123, poll=lambda: 1)

        result = validate_process_startup(process, timeout=1)

        self.assertEqual(result.status, "FALHA")
        self.assertIn("codigo 1", result.message)

    def test_alive_process_without_window_is_inconclusive(self):
        process = SimpleNamespace(pid=123, poll=lambda: None)

        result = validate_process_startup(process, timeout=-1)

        self.assertEqual(result.status, "INCONCLUSIVO")


class LoginCompletionTests(unittest.TestCase):
    @patch("app.autcom_test._window_details_for_process")
    def test_responsive_principal_window_confirms_login(self, windows_mock):
        windows_mock.return_value = [
            SimpleNamespace(
                handle=222,
                class_name="TMainForm",
                is_hung=False,
                title="AutCom",
            )
        ]
        process = SimpleNamespace(pid=123, poll=lambda: None)

        result = validate_login_completion(
            process,
            login_handle=111,
            timeout=1,
            stability_seconds=0,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.window_title, "AutCom")

    @patch("app.autcom_test._window_details_for_process")
    def test_login_window_remaining_open_is_failure(self, windows_mock):
        windows_mock.return_value = [
            SimpleNamespace(
                handle=111,
                class_name="TParentForm",
                is_hung=False,
                title="AutCom",
            )
        ]
        process = SimpleNamespace(pid=123, poll=lambda: None)

        result = validate_login_completion(
            process,
            login_handle=111,
            timeout=-1,
        )

        self.assertEqual(result.status, "FALHA")
        self.assertIn("permaneceu aberta", result.message)


class CompleteAutcomTestTests(unittest.TestCase):
    @patch("app.autcom_test.close_autcom", return_value="fechado")
    @patch("app.autcom_test.validate_login_completion")
    @patch("app.autcom_test.automate_autcom_login")
    @patch("app.autcom_test.validate_process_startup")
    @patch("app.autcom_test.subprocess.Popen")
    @patch("app.autcom_test.subprocess.run")
    def test_bat_runs_before_autcom_and_success_is_returned(
        self,
        run_mock,
        popen_mock,
        startup_mock,
        login_mock,
        completion_mock,
        close_mock,
    ):
        with TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            project = base / "379.48.2.30.74.111.181"
            test_root = base / "PastaTesteCopia_74_111"
            exe_directory = test_root / "exe"
            project.mkdir()
            exe_directory.mkdir(parents=True)
            (project / "Autcom.zip").write_bytes(b"zip")
            (exe_directory / "CopiaArquivos.bat").write_text(
                "@echo off",
                encoding="utf-8",
            )
            (test_root / "Autcom.exe").write_bytes(b"exe")
            run_mock.return_value = SimpleNamespace(
                returncode=0,
                stdout="",
                stderr="",
            )
            popen_mock.return_value = SimpleNamespace(pid=456)
            startup_mock.return_value = AutcomTestResult(
                status="OK",
                message="login abriu",
                process_id=456,
                window_title="AUTCOM",
            )
            login_mock.return_value = 1234
            completion_mock.return_value = AutcomTestResult(
                status="OK",
                message="tela principal abriu",
                process_id=456,
                window_title="AUTCOM",
            )

            result = run_autcom_test(project)

        self.assertTrue(result.ok)
        self.assertEqual(result.process_id, 456)
        self.assertEqual(run_mock.call_args.kwargs["cwd"], exe_directory)
        self.assertEqual(popen_mock.call_args.kwargs["cwd"], test_root)
        close_mock.assert_called_once_with(popen_mock.return_value)
        self.assertIn("O Autcom foi fechado ao final do teste.", result.message)



class CloseAutcomTests(unittest.TestCase):
    def fake_process(self, poll=None, wait_error=None):
        calls = []

        class FakeProcess:
            pid = 777

            def poll(self):
                return poll

            def wait(self, timeout=None):
                calls.append(("wait", timeout))
                if wait_error is not None:
                    raise wait_error
                return 0

            def kill(self):
                calls.append(("kill",))

        return FakeProcess(), calls

    def test_already_exited_process_is_left_alone(self):
        process, calls = self.fake_process(poll=0)
        with patch("app.autcom_test._post_close_to_windows") as post_close:
            self.assertEqual(close_autcom(process), "ja_encerrado")
        post_close.assert_not_called()
        self.assertEqual(calls, [])

    def test_graceful_close_when_process_exits_after_close_request(self):
        process, calls = self.fake_process()
        with patch("app.autcom_test._post_close_to_windows") as post_close, \
             patch("app.autcom_test.subprocess.run") as run:
            self.assertEqual(close_autcom(process, grace_seconds=5), "fechado")
        post_close.assert_called_once_with(777)
        self.assertEqual(calls, [("wait", 5)])
        run.assert_not_called()  # nao precisou forcar

    def test_unresponsive_process_is_killed_with_its_children(self):
        import subprocess

        process, calls = self.fake_process(wait_error=subprocess.TimeoutExpired("autcom", 1))
        with patch("app.autcom_test._post_close_to_windows"), \
             patch("app.autcom_test.subprocess.run") as run:
            self.assertEqual(close_autcom(process, grace_seconds=1), "forcado")
        self.assertEqual(run.call_args.args[0][:3], ["taskkill", "/T", "/F"])
        self.assertIn(("kill",), calls)

    def test_never_raises(self):
        class Broken:
            pid = 1

            def poll(self):
                raise OSError("handle invalido")

        self.assertEqual(close_autcom(Broken()), "forcado")

    def test_result_note_is_appended_for_every_outcome(self):
        for closing, expected in (
            ("fechado", "foi fechado ao final"),
            ("forcado", "nao respondeu"),
            ("ja_encerrado", "ja estava encerrado"),
        ):
            self.assertIn(expected, _closing_note(closing))


class AutcomIsClosedAfterTestTests(unittest.TestCase):
    def setUp(self):
        self._temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        base = Path(self._temp.name)
        self.project = base / "379.48.2.30.74.111.181"
        test_root = base / "PastaTesteCopia_74_111"
        exe_directory = test_root / "exe"
        self.project.mkdir()
        exe_directory.mkdir(parents=True)
        (self.project / "Autcom.zip").write_bytes(b"zip")
        (exe_directory / "CopiaArquivos.bat").write_text("@echo off", encoding="utf-8")
        (test_root / "Autcom.exe").write_bytes(b"exe")

    def run_test(self, startup=None, login_error=None, completion=None, close_return="fechado"):
        process = SimpleNamespace(pid=456)
        with patch("app.autcom_test.subprocess.run") as run, \
             patch("app.autcom_test.subprocess.Popen", return_value=process), \
             patch("app.autcom_test.validate_process_startup", return_value=startup), \
             patch("app.autcom_test.automate_autcom_login", side_effect=login_error, return_value=1234), \
             patch("app.autcom_test.validate_login_completion", return_value=completion), \
             patch("app.autcom_test.close_autcom", return_value=close_return) as close:
            run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
            try:
                result = run_autcom_test(self.project)
            except Exception as error:
                return None, close, error
        return result, close, None

    def test_closed_when_test_succeeds(self):
        ok = AutcomTestResult(status="OK", message="tela principal abriu", process_id=456)
        result, close, _error = self.run_test(startup=ok, completion=ok)
        close.assert_called_once()
        self.assertTrue(result.ok)

    def test_closed_when_startup_fails(self):
        failed = AutcomTestResult(status="FALHA", message="Autcom encerrou cedo", process_id=456)
        result, close, _error = self.run_test(startup=failed)
        close.assert_called_once()
        self.assertEqual(result.status, "FALHA")
        self.assertIn("Autcom encerrou cedo", result.message)
        self.assertIn("fechado ao final", result.message)

    def test_closed_when_login_cannot_be_filled(self):
        ok = AutcomTestResult(status="OK", message="login abriu", process_id=456)
        result, close, _error = self.run_test(startup=ok, login_error=OSError("sem foco"))
        close.assert_called_once()
        self.assertEqual(result.status, "FALHA")

    def test_closed_even_when_validation_raises_unexpectedly(self):
        process = SimpleNamespace(pid=456)
        with patch("app.autcom_test.subprocess.run") as run, \
             patch("app.autcom_test.subprocess.Popen", return_value=process), \
             patch("app.autcom_test._validate_started_autcom", side_effect=RuntimeError("bug")), \
             patch("app.autcom_test.close_autcom", return_value="fechado") as close:
            run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
            with self.assertRaisesRegex(RuntimeError, "bug"):
                run_autcom_test(self.project)

        close.assert_called_once_with(process)

    def test_forced_close_is_reported_to_the_operator(self):
        ok = AutcomTestResult(status="OK", message="tela principal abriu", process_id=456)
        result, _close, _error = self.run_test(startup=ok, completion=ok, close_return="forcado")
        self.assertIn("nao respondeu ao pedido de fechamento", result.message)


@unittest.skipUnless(os.name == "nt", "Fechamento de janela so existe no Windows")
class CloseRealWindowProcessTests(unittest.TestCase):
    """Com um processo de verdade: uma janela que fecha ao receber o pedido."""

    def start(self, code):
        process = subprocess.Popen([sys.executable, "-c", code])
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process

    def wait_for_window(self, process):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if _window_details_for_process(process.pid):
                return True
            time.sleep(0.2)
        return False

    def test_window_process_is_closed_gracefully(self):
        process = self.start(
            "import tkinter as tk; r = tk.Tk(); r.title('Autcom de teste'); r.mainloop()"
        )
        if not self.wait_for_window(process):
            self.skipTest("sem sessao grafica para abrir a janela de teste")

        self.assertEqual(close_autcom(process, grace_seconds=10), "fechado")
        self.assertIsNotNone(process.poll())

    def test_process_that_ignores_close_is_killed(self):
        process = self.start("import time; time.sleep(120)")

        self.assertEqual(close_autcom(process, grace_seconds=0.5), "forcado")
        self.assertIsNotNone(process.poll())
