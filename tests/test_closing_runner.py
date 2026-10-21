import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from app.closing_runner import (
    ClosingManager,
    ClosingTimeoutError,
    format_elapsed,
    run_closing_bat,
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
        self.assertEqual(manager.progress_text(projects[0]), "Em andamento 01:15")

        target, job = started[0]
        target(job)

        self.assertEqual(manager.progress_text(projects[0]), "Concluído 01:15")
        self.assertEqual(len(started), 3)
        self.assertEqual(manager.progress_text(projects[2]), "Em andamento 00:00")
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
        for target, job in list(started):
            target(job)

        self.assertEqual(manager.progress_text(first), "Erro (código 3) 00:05")
        self.assertEqual(manager.progress_text(second), "Erro 00:05")
        self.assertEqual(manager.job_for(second).message, "falhou")
        self.assertEqual(manager.active_count(), 0)

    def test_unknown_project_has_empty_progress(self):
        manager, _started, _clock = self.make_manager()
        self.assertEqual(manager.progress_text(make_project("p1")), "")

    def test_format_elapsed(self):
        self.assertEqual(format_elapsed(0), "00:00")
        self.assertEqual(format_elapsed(125), "02:05")
        self.assertEqual(format_elapsed(3725), "1:02:05")


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
