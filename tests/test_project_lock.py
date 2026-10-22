import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import project_lock
from app.project_lock import (
    LOCK_FILE_NAME,
    ProjectLock,
    ProjectLockedError,
    describe_owner,
    read_owner,
)


class ProjectLockTests(unittest.TestCase):
    def setUp(self):
        self._temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        self.project = Path(self._temp.name)

    def lock_as(self, user, action="Fechamento Local"):
        with patch.dict("os.environ", {"USERNAME": user}):
            return ProjectLock(self.project, action).acquire()

    def test_second_operator_is_blocked_with_owner_details(self):
        first = self.lock_as("joao.silva")
        self.addCleanup(first.release)

        with self.assertRaises(ProjectLockedError) as raised:
            self.lock_as("felipe.santos", "Copiar Local")

        owner = raised.exception.owner
        self.assertEqual(owner["user"], "joao.silva")
        self.assertEqual(owner["action"], "Fechamento Local")
        self.assertIn("Fechamento Local em andamento por joao.silva desde", str(raised.exception))

    def test_release_frees_project_for_others(self):
        first = self.lock_as("joao.silva")
        first.release()

        second = self.lock_as("felipe.santos")
        self.addCleanup(second.release)
        self.assertEqual(read_owner(self.project)["user"], "felipe.santos")
        self.assertFalse(second._stop.is_set())

    def test_stale_lock_from_closed_system_is_taken_over(self):
        lock_file = self.project / LOCK_FILE_NAME
        lock_file.write_text(json.dumps({"user": "joao.silva", "action": "Copiar Local", "token": "x"}))
        old = time.time() - project_lock.STALE_SECONDS - 5
        os.utime(lock_file, (old, old))

        self.assertIsNone(read_owner(self.project))
        lock = self.lock_as("felipe.santos")
        self.addCleanup(lock.release)
        self.assertEqual(read_owner(self.project)["user"], "felipe.santos")

    def test_release_does_not_remove_lock_taken_over_by_someone_else(self):
        first = self.lock_as("joao.silva")
        first._stop.set()  # simula o sistema do joao travado (sem renovar)
        (self.project / LOCK_FILE_NAME).write_text(
            json.dumps({"user": "felipe.santos", "action": "Copiar Local", "token": "outro"})
        )

        first.release()

        self.assertTrue((self.project / LOCK_FILE_NAME).exists())

    def test_heartbeat_keeps_long_operation_locked(self):
        with patch.object(project_lock, "HEARTBEAT_SECONDS", 0.05):
            lock = self.lock_as("joao.silva")
            self.addCleanup(lock.release)
            lock_file = self.project / LOCK_FILE_NAME
            old = time.time() - 1000
            os.utime(lock_file, (old, old))
            time.sleep(0.3)

        self.assertGreater(lock_file.stat().st_mtime, old + 900)
        self.assertIsNotNone(read_owner(self.project))

    def test_describe_owner_without_start_time(self):
        self.assertEqual(
            describe_owner({"user": "ana", "action": "Limpar Pasta"}),
            "Limpar Pasta em andamento por ana",
        )


if __name__ == "__main__":
    unittest.main()
