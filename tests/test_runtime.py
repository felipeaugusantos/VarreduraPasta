import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.runtime import data_directory, user_data_file


class RuntimeDataPathTests(unittest.TestCase):
    def test_data_directory_uses_local_app_data(self):
        with patch.dict(
            os.environ,
            {
                "LOCALAPPDATA": r"C:\Users\operador\AppData\Local",
                "VARREDURA_SISTEMA_DATA_DIR": "",
            },
            clear=False,
        ):
            self.assertEqual(
                data_directory(),
                Path(r"C:\Users\operador\AppData\Local\VarreduraSistema"),
            )

    def test_explicit_data_directory_has_priority(self):
        with patch.dict(
            os.environ,
            {"VARREDURA_SISTEMA_DATA_DIR": r"D:\Dados\Varredura"},
            clear=False,
        ):
            self.assertEqual(
                data_directory(), Path(r"D:\Dados\Varredura")
            )

    def test_user_data_file_can_be_seeded(self):
        with TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            seed = root / "seed.json"
            target = root / "dados"
            seed.write_text('{"padrao": true}', encoding="utf-8")
            with patch.dict(
                os.environ,
                {"VARREDURA_SISTEMA_DATA_DIR": str(target)},
                clear=False,
            ):
                destination = user_data_file(
                    "seeded_pattern.json", seed_file=seed
                )

            self.assertEqual(
                destination.read_text(encoding="utf-8"),
                '{"padrao": true}',
            )


if __name__ == "__main__":
    unittest.main()
