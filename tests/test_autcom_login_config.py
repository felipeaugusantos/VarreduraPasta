import json
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import settings
from app.autcom_test import (
    AutcomTestResult,
    _type_character,
    _validate_started_autcom,
    login_value_error,
)


class SettingsTestCase(unittest.TestCase):
    def setUp(self):
        self._temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        original = settings.SETTINGS_FILE
        settings.SETTINGS_FILE = Path(self._temp.name) / "settings.json"
        self.addCleanup(setattr, settings, "SETTINGS_FILE", original)


class AutcomLoginSettingsTests(SettingsTestCase):
    def test_defaults_when_nothing_is_configured(self):
        self.assertEqual(
            settings.load_autcom_test_login(),
            {"username": "998", "password": "998", "company": "001"},
        )

    def test_saved_values_override_defaults_per_field(self):
        settings.save_autcom_test_login("operador7", "", "002")

        self.assertEqual(
            settings.load_autcom_test_login(),
            {"username": "operador7", "password": "998", "company": "002"},
        )
        saved = json.loads(settings.SETTINGS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(saved["autcom_test_login"], {"username": "operador7", "company": "002"})

    def test_clearing_every_field_restores_defaults_and_removes_the_key(self):
        settings.save_autcom_test_login("operador7", "abc", "002")
        settings.save_autcom_test_login("  ", "", "")

        self.assertEqual(settings.load_autcom_test_login()["username"], "998")
        saved = json.loads(settings.SETTINGS_FILE.read_text(encoding="utf-8"))
        self.assertNotIn("autcom_test_login", saved)

    def test_corrupted_value_falls_back_to_defaults(self):
        settings.SETTINGS_FILE.write_text('{"autcom_test_login": "texto"}', encoding="utf-8")
        self.assertEqual(settings.load_autcom_test_login()["company"], "001")


class LoginValidationTests(unittest.TestCase):
    def test_letters_and_numbers_are_accepted(self):
        for value in ("998", "Operador7", "ABC", "", "   "):
            self.assertEqual(login_value_error("Usuário", value), "")

    def test_spaces_accents_and_symbols_are_rejected(self):
        for value in ("ab cd", "operador*", "usuário", "9 98", "a-b"):
            self.assertIn("apenas letras e numeros", login_value_error("Usuário", value))
        self.assertTrue(login_value_error("Senha", "x!").startswith("Senha:"))


class TypeCharacterTests(unittest.TestCase):
    """Digitacao do login: respeita Shift (antes so numeros saiam corretos)."""

    def fake_user32(self, scan_codes):
        calls = []
        user32 = SimpleNamespace(
            VkKeyScanW=lambda code: scan_codes[chr(code)],
            keybd_event=lambda key, scan, flags, extra: calls.append((key, flags)),
        )
        return user32, calls

    def test_digit_and_lowercase_need_no_shift(self):
        user32, calls = self.fake_user32({"9": 0x0039, "a": 0x0041})

        _type_character(user32, "9")
        _type_character(user32, "a")

        self.assertEqual(calls, [(0x39, 0), (0x39, 2), (0x41, 0), (0x41, 2)])

    def test_uppercase_letter_is_typed_with_shift(self):
        user32, calls = self.fake_user32({"A": 0x0141})  # Shift + A

        _type_character(user32, "A")

        self.assertEqual(calls, [(0x10, 0), (0x41, 0), (0x41, 2), (0x10, 2)])

    def test_character_without_a_key_is_rejected(self):
        user32, calls = self.fake_user32({"ç": -1})
        with self.assertRaisesRegex(OSError, "nao pode ser digitado"):
            _type_character(user32, "ç")
        self.assertEqual(calls, [])


class TestUsesConfiguredLoginTests(unittest.TestCase):
    def test_login_is_filled_with_configured_values(self):
        process = SimpleNamespace(pid=456)
        ok = AutcomTestResult(status="OK", message="ok", process_id=456)
        configured = {"username": "operador7", "password": "Senha1", "company": "002"}
        with patch("app.autcom_test.load_autcom_test_login", return_value=configured), \
             patch("app.autcom_test.validate_process_startup", return_value=ok), \
             patch("app.autcom_test.automate_autcom_login", return_value=1234) as login, \
             patch("app.autcom_test.validate_login_completion", return_value=ok):
            _validate_started_autcom(process, SimpleNamespace(returncode=0), [], None, 5)

        self.assertEqual(
            login.call_args.kwargs,
            {"username": "operador7", "password": "Senha1", "company": "002"},
        )

    def test_password_is_never_written_to_the_result_message(self):
        process = SimpleNamespace(pid=456)
        ok = AutcomTestResult(status="OK", message="login abriu", process_id=456)
        configured = {"username": "u", "password": "SegredoXYZ", "company": "1"}
        with patch("app.autcom_test.load_autcom_test_login", return_value=configured), \
             patch("app.autcom_test.validate_process_startup", return_value=ok), \
             patch("app.autcom_test.automate_autcom_login", side_effect=OSError("sem foco")), \
             patch("app.autcom_test.validate_login_completion", return_value=ok):
            result = _validate_started_autcom(process, SimpleNamespace(returncode=0), [], None, 5)

        self.assertNotIn("SegredoXYZ", result.message)


class SettingsWindowSaveTests(SettingsTestCase):
    """Com Tk de verdade: Salvar valida o login antes de gravar qualquer coisa."""

    def setUp(self):
        super().setUp()
        patcher = patch.dict("os.environ", {"VARREDURA_SISTEMA_DATA_DIR": tempfile.mkdtemp()})
        patcher.start()
        self.addCleanup(patcher.stop)
        from app import interface

        self.interface = interface
        with patch.object(interface.VersionScannerApp, "refresh", lambda self: None), \
             patch.object(interface.VersionScannerApp, "check_scripts_periodically", lambda self: None), \
             patch.object(interface.VersionScannerApp, "check_for_update_periodically", lambda self: None):
            self.app = interface.VersionScannerApp()
        self.app.withdraw()
        self.addCleanup(self.app.destroy)

    def test_invalid_login_blocks_the_whole_save(self):
        with patch.object(self.interface, "messagebox") as messagebox, \
             patch.object(self.app, "refresh") as refresh:
            self.app.save_settings(
                r"D:\Outra", [], "", "", autcom_login=("ab cd", "abc*", "001")
            )

        message = messagebox.showerror.call_args.args[1]
        self.assertIn("Usuário do Autcom", message)
        self.assertIn("Senha do Autcom", message)
        self.assertNotIn("Empresa", message)
        refresh.assert_not_called()
        self.assertFalse(settings.SETTINGS_FILE.exists())  # nada foi gravado

    def test_valid_login_is_saved_with_the_rest_of_the_settings(self):
        with patch.object(self.interface, "messagebox") as messagebox, \
             patch.object(self.app, "refresh") as refresh:
            self.app.save_settings(
                r"D:\Outra", [], "", "", autcom_login=("operador7", "Senha1", "002")
            )

        messagebox.showerror.assert_not_called()
        refresh.assert_called_once()
        self.assertEqual(settings.load_autcom_test_login()["username"], "operador7")
        self.assertEqual(settings.load_base_directory(), Path(r"D:\Outra"))

    def test_window_shows_current_login_and_masks_the_password(self):
        settings.save_autcom_test_login("operador7", "Senha1", "002")
        self.app.open_settings_window()
        window = self.app.config_window
        self.app.update()

        def find(widget, cls):
            found = []
            for child in widget.winfo_children():
                if child.winfo_class() == cls:
                    found.append(child)
                found.extend(find(child, cls))
            return found

        entries = {entry.get(): entry for entry in find(window, "TEntry")}
        self.assertIn("operador7", entries)
        self.assertEqual(str(entries["Senha1"].cget("show")), "*")
        self.assertIn("002", entries)
        window.destroy()


if __name__ == "__main__":
    unittest.main()
