import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from app import learning


def project(sequence, size):
    return SimpleNamespace(
        folder_name=f"379.48.2.30.74.150.{sequence}",
        status="OK",
        display_autcom_size_mb=size,
        file_checks=[
            SimpleNamespace(source="Pasta", found_name="Autcom.exe"),
            SimpleNamespace(source="Zip", found_name="AutBan.exe"),
        ],
    )


class LocalLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = TemporaryDirectory()
        self.original_database = learning.LEARNING_DB
        learning.LEARNING_DB = (
            Path(self.temp_directory.name) / "learning.db"
        )

    def tearDown(self):
        learning.LEARNING_DB = self.original_database
        self.temp_directory.cleanup()

    def test_identical_observation_is_not_duplicated(self):
        item = project(1, 50.0)
        self.assertEqual(learning.learn_from_projects([item]), 1)
        self.assertEqual(learning.learn_from_projects([item]), 0)

    def test_size_outside_history_generates_explainable_insight(self):
        learning.learn_from_projects(
            [project(1, 50.0), project(2, 51.0), project(3, 52.0)]
        )

        insights = learning.insights_for_project(project(4, 90.0))

        self.assertEqual(len(insights), 1)
        self.assertIn("fora do historico", insights[0].message)
        self.assertGreaterEqual(insights[0].confidence, 0.5)

    def test_invalid_project_is_not_used_for_learning(self):
        item = project(1, 50.0)
        item.status = "Versao incorreta"
        self.assertEqual(learning.learn_from_projects([item]), 0)


if __name__ == "__main__":
    unittest.main()
