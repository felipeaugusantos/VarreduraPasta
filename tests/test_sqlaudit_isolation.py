"""Garantias de seguranca da auditoria: nada do texto dos scripts chega ao banco."""

import re
import unittest
from pathlib import Path

from app.sqlaudit import db_reader

PACKAGE = Path(__file__).resolve().parent.parent / "app" / "sqlaudit"
READER = "db_reader.py"
WRITE_WORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|REPLACE|RENAME|CALL|LOAD|KILL)\b", re.IGNORECASE
)


def sources():
    return {path.name: path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py")}


class OnlyTheReaderTouchesTheDatabase(unittest.TestCase):
    def test_no_other_module_connects_or_executes_sql(self):
        for name, source in sources().items():
            if name == READER:
                continue
            for forbidden in (".execute(", ".executemany(", ".cursor("):
                self.assertNotIn(forbidden, source, f"{name} usa {forbidden}")
            imports = re.findall(r"^\s*(?:from|import)\s+(\S+)", source, re.MULTILINE)
            for module in imports:
                for forbidden in ("pymysql", "app.model_db", "app.script_runner", "app.script_monitor"):
                    self.assertFalse(module.startswith(forbidden), f"{name} importa {module}")

    def test_reader_never_imports_modules_that_read_sql_text_or_execute_scripts(self):
        imports = re.findall(r"^\s*(?:from|import)\s+(\S+)", sources()[READER], re.MULTILINE)
        for module in imports:
            self.assertFalse(module.startswith("app"), f"{READER} importa {module}")

    def test_reader_has_a_single_execute_fed_only_by_the_query_helper(self):
        source = sources()[READER]
        self.assertEqual(source.count(".execute("), 1)
        self.assertNotIn(".executemany(", source)
        calls = re.findall(r"self\._query\(([^,)]*)", source)
        self.assertTrue(calls)
        for argument in calls:
            if argument.strip() == "statement":  # a propria definicao do helper
                continue
            self.assertRegex(argument.strip(), r"^SQL_[A-Z_]+$")

    def test_every_query_constant_is_a_read_or_a_session_setting(self):
        constants = {name: value for name, value in vars(db_reader).items() if name.startswith("SQL_")}
        self.assertGreaterEqual(len(constants), 5)
        for name, value in constants.items():
            self.assertRegex(value, r"^\s*(SELECT|SET SESSION)\b", name)
            self.assertIsNone(WRITE_WORDS.search(value), f"{name} tem comando de escrita")

    def test_session_is_opened_read_only(self):
        self.assertEqual(db_reader.SQL_READ_ONLY, "SET SESSION TRANSACTION READ ONLY")


class WindowIsolationTests(unittest.TestCase):
    def test_window_never_runs_scripts_or_touches_the_driver(self):
        source = (PACKAGE.parent / "sqlaudit_window.py").read_text(encoding="utf-8")
        for forbidden in ("run_scripts", "execute_scripts", "run_script(", "pymysql", "connect_readonly(", ".execute(", "script_runner"):
            self.assertNotIn(forbidden, source, forbidden)
        imports = re.findall(r"^\s*(?:from|import)\s+(\S+)", source, re.MULTILINE)
        self.assertNotIn("app.model_db", imports)  # so o dialogo de login (app.model_db_dialogs)
        self.assertIn("app.model_db_dialogs", imports)


if __name__ == "__main__":
    unittest.main()
