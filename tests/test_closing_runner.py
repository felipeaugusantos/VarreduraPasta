import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from app.closing_runner import (
    ClosingManager,
    ClosingTimeoutError,
    format_elapsed,
    pending_file_sizes,
    run_closing_bat,
    zipped_percent,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make_project(name):
    return SimpleNamespace(path=Path("C:/VERSOES_FECHADAS") / name, folder_name=name)


class ClosingManagerTests(unittest.TestCase):
    def make_manager(self, execute=None, max_concurrent=2):
        started = []
        clock = FakeClock()
        manager = ClosingManager(
            execute or (lambda job: 0),
            max_concurrent=max_concurrent,
            clock=clock,
            start_thread=lambda target, job: started.append((target, job)),
        )
        return manager, started, clock

    def test_runs_at_most_two_and_starts_next_when_one_finishes(self):
        manager, started, clock = self.make_manager()
        projects = [make_project(f"p{n}") for n in range(3)]
        for project in projects:
            self.assertTrue(manager.enqueue(project, "Fechamento Local", "x.bat"))

        self.assertEqual(len(started), 2)
        self.assertEqual(manager.progress_text(projects[2]), "Na fila")

        clock.now = 75
        self.assertEqual(manager.progress_text(projects[0]), "Em andamento 0%")

        target, job = started[0]
        job.percent = 45
        self.assertEqual(manager.progress_text(projects[0]), "Em andamento 45%")
        target(job)

        self.assertEqual(manager.progress_text(projects[0]), "Concluído 01:15")
        self.assertEqual(len(started), 3)
        self.assertEqual(manager.progress_text(projects[2]), "Em andamento 0%")
        self.assertEqual(manager.active_count(), 2)
        self.assertEqual(manager.finished_count, 1)

    def test_rejects_project_already_queued_or_running(self):
        manager, started, _clock = self.make_manager(max_concurrent=1)
        project = make_project("p1")

        self.assertTrue(manager.enqueue(project, "Fechamento Local", "x.bat"))
        self.assertFalse(manager.enqueue(project, "Fechamento Local", "x.bat"))

        target, job = started[0]
        target(job)
        self.assertTrue(manager.enqueue(project, "Fechamento Local", "x.bat"))

    def test_error_code_and_exception_are_shown_as_error(self):
        results = {"p1": 3}

        def execute(job):
            if job.project.folder_name == "p2":
                raise OSError("falhou")
            return results[job.project.folder_name]

        manager, started, clock = self.make_manager(execute=execute)
        first, second = make_project("p1"), make_project("p2")
        manager.enqueue(first, "Fechamento Local", "x.bat")
        manager.enqueue(second, "Fechamento Local", "x.bat")
        clock.now = 5
        started[1][1].percent = 80
        for target, job in list(started):
            target(job)

        # O tempo aparece so no Concluido; o erro mostra ate onde chegou.
        self.assertEqual(manager.progress_text(first), "Erro")
        self.assertEqual(manager.progress_text(second), "Erro em 80%")
        self.assertEqual(manager.job_for(second).message, "falhou")
        self.assertEqual(manager.active_count(), 0)

    def test_unknown_project_has_empty_progress(self):
        manager, _started, _clock = self.make_manager()
        self.assertEqual(manager.progress_text(make_project("p1")), "")

    def test_format_elapsed(self):
        self.assertEqual(format_elapsed(0), "00:00")
        self.assertEqual(format_elapsed(125), "02:05")
        self.assertEqual(format_elapsed(3725), "1:02:05")


class ZippedPercentTests(unittest.TestCase):
    def test_percent_is_weighted_by_size(self):
        initial = {"autcom.exe": 700, "liba.dll": 200, "libb.dll": 100}

        self.assertEqual(zipped_percent(initial, set(initial)), 0)
        # Duas DLLs prontas sao so 30% do trabalho, pois o Autcom e o maior.
        self.assertEqual(zipped_percent(initial, {"autcom.exe"}), 30)
        self.assertEqual(zipped_percent(initial, {"liba.dll", "libb.dll"}), 70)

    def test_percent_stays_below_100_while_running(self):
        initial = {"autcom.exe": 700, "liba.dll": 300}
        self.assertEqual(zipped_percent(initial, set()), 99)

    def test_ignores_new_files_and_handles_empty_folder(self):
        initial = {"liba.dll": 100}
        self.assertEqual(zipped_percent(initial, {"liba.dll", "novo.dll"}), 0)
        self.assertEqual(zipped_percent({}, set()), 0)
        self.assertEqual(zipped_percent({"vazio.dll": 0}, set()), 0)

    def test_pending_file_sizes_lists_only_exe_dll_bpl(self):
        with TemporaryDirectory() as temp_directory:
            folder = Path(temp_directory)
            (folder / "Autcom.EXE").write_bytes(b"x" * 7)
            (folder / "libA.dll").write_bytes(b"x" * 2)
            (folder / "Principal.View.bpl").write_bytes(b"x" * 3)
            (folder / "autcom.zip").write_bytes(b"x" * 50)
            (folder / "comandosCMD").mkdir()
            (folder / "pasta.dll").mkdir()

            sizes = pending_file_sizes(folder)

        self.assertEqual(
            sizes,
            {"autcom.exe": 7, "liba.dll": 2, "principal.view.bpl": 3},
        )


@unittest.skipUnless(os.name == "nt", "BAT so executa no Windows")
class RunClosingBatTests(unittest.TestCase):
    def test_runs_hidden_and_writes_output_to_log(self):
        with TemporaryDirectory() as temp_directory:
            bat_path = Path(temp_directory) / "comandosCMD" / "_FechamentoArquivos.bat"
            bat_path.parent.mkdir()
            bat_path.write_text(
                "@echo off\r\necho executando em %CD%\r\nexit /b 3\r\n",
                encoding="utf-8",
            )
            log_path = Path(temp_directory) / "logs" / "fechamento.log"

            return_code = run_closing_bat(bat_path, log_path, timeout=60)
            content = log_path.read_text(encoding="mbcs", errors="replace")

        self.assertEqual(return_code, 3)
        self.assertIn("executando em", content)
        self.assertIn("comandosCMD", content)

    def test_bat_runs_hidden_with_below_normal_priority(self):
        import subprocess
        from unittest.mock import patch

        with TemporaryDirectory() as temp_directory:
            bat_path = Path(temp_directory) / "rapido.bat"
            bat_path.write_text("@echo off\r\n", encoding="utf-8")
            with patch(
                "app.closing_runner.subprocess.Popen",
                wraps=subprocess.Popen,
            ) as popen:
                run_closing_bat(bat_path, Path(temp_directory) / "rapido.log", timeout=60)

        flags = popen.call_args.kwargs["creationflags"]
        self.assertTrue(flags & subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        self.assertTrue(flags & subprocess.CREATE_NEW_CONSOLE)
        self.assertEqual(popen.call_args.kwargs["startupinfo"].wShowWindow, 0)

    def test_timeout_command_inside_bat_really_waits(self):
        # Sem console proprio o timeout do Windows falha na hora e lacos de espera
        # viram lacos ocupados (caso real: contador de 480 "segundos" em 236 s).
        import time

        timeout_exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "timeout.exe"
        with TemporaryDirectory() as temp_directory:
            bat_path = Path(temp_directory) / "espera.bat"
            bat_path.write_text(
                "@echo off\r\n"
                f'"{timeout_exe}" /t 1 >nul\r\n'
                f'"{timeout_exe}" /t 1 >nul\r\n'
                "echo fim\r\n",
                encoding="utf-8",
            )
            log_path = Path(temp_directory) / "espera.log"
            start = time.monotonic()
            return_code = run_closing_bat(bat_path, log_path, timeout=60)
            elapsed = time.monotonic() - start
            content = log_path.read_text(encoding="mbcs", errors="replace")

        self.assertEqual(return_code, 0)
        self.assertGreaterEqual(elapsed, 1.0)
        self.assertNotIn("redirecionamento", content)
        self.assertIn("fim", content)

    def test_on_tick_runs_while_bat_waits_and_errors_are_ignored(self):
        timeout_exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "timeout.exe"
        ticks = []

        def on_tick():
            ticks.append(1)
            raise OSError("pasta indisponivel por um instante")

        with TemporaryDirectory() as temp_directory:
            bat_path = Path(temp_directory) / "espera.bat"
            bat_path.write_text(
                f'@echo off\r\n"{timeout_exe}" /t 3 /nobreak >nul\r\nexit /b 1\r\n',
                encoding="utf-8",
            )
            return_code = run_closing_bat(
                bat_path,
                Path(temp_directory) / "espera.log",
                timeout=60,
                on_tick=on_tick,
            )

        self.assertEqual(return_code, 1)
        self.assertGreaterEqual(len(ticks), 1)

    def test_timeout_stops_bat(self):
        with TemporaryDirectory() as temp_directory:
            bat_path = Path(temp_directory) / "lento.bat"
            bat_path.write_text(
                "@echo off\r\nping -n 30 127.0.0.1 >nul\r\n",
                encoding="utf-8",
            )
            log_path = Path(temp_directory) / "lento.log"

            with self.assertRaises(ClosingTimeoutError):
                run_closing_bat(bat_path, log_path, timeout=1)


if __name__ == "__main__":
    unittest.main()
