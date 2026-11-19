"""Senha lembrada: criptografada e presa a servidor/porta/usuario; nunca em texto no disco."""

import base64
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import saved_login
from app.secret_store import FILE_NAME, SecretStore, SecretStoreError, UnavailableBackend, make_backend

PASSWORD = "SenhaSecreta#42"


class FakeBackend:
    """Imita a DPAPI: cifra (XOR) e amarra o item a `entropy`; entropia errada nao abre."""

    available = True

    @staticmethod
    def _mask(entropy, size):
        stream = hashlib.sha256(entropy).digest()
        return bytes(stream[i % len(stream)] for i in range(size))

    def protect(self, data, entropy):
        tag = hashlib.sha256(b"tag" + entropy).digest()[:8]
        return tag + bytes(a ^ b for a, b in zip(data, self._mask(entropy, len(data))))

    def unprotect(self, data, entropy):
        if data[:8] != hashlib.sha256(b"tag" + entropy).digest()[:8]:
            raise SecretStoreError("entropia diferente")
        body = data[8:]
        return bytes(a ^ b for a, b in zip(body, self._mask(entropy, len(body))))


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / FILE_NAME
        self.store = SecretStore(self.path, FakeBackend())


class SecretStoreTests(StoreTestCase):
    def test_roundtrip_and_the_file_never_holds_the_password_in_clear_or_plain_base64(self):
        self.store.save("10.0.0.35", 43309, "leitura", PASSWORD)
        self.assertEqual(self.store.load("10.0.0.35", 43309, "leitura"), PASSWORD)
        raw = self.path.read_bytes()
        self.assertNotIn(PASSWORD.encode(), raw)
        self.assertNotIn(base64.b64encode(PASSWORD.encode()), raw)
        self.assertNotIn(PASSWORD.encode("utf-16-le"), raw)
        self.assertTrue(self.store.has("10.0.0.35", 43309, "leitura"))

    def test_the_key_ignores_case_and_surrounding_spaces_of_host_and_user(self):
        self.store.save(" Servidor.EXEMPLO ", 3306, " LEITURA ", PASSWORD)
        self.assertEqual(self.store.load("servidor.exemplo", 3306, "leitura"), PASSWORD)
        self.assertIsNone(self.store.load("servidor.exemplo", 3307, "leitura"))  # outra porta
        self.assertIsNone(self.store.load("servidor.exemplo", 3306, "outro"))  # outro usuario
        self.assertIsNone(self.store.load("outro.exemplo", 3306, "leitura"))  # outro servidor

    def test_an_item_copied_to_another_key_does_not_open(self):
        self.store.save("a", 1, "u", PASSWORD)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        (blob,) = data["itens"].values()
        data["itens"]["b|2|u"] = blob  # o mesmo blob preso a outra chave
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(self.store.load("b", 2, "u"))
        self.assertEqual(self.store.load("a", 1, "u"), PASSWORD)

    def test_several_items_overwrite_and_forget(self):
        self.store.save("h", 1, "u1", "um")
        self.store.save("h", 1, "u2", "dois")
        self.store.save("h", 1, "u1", "novo")
        self.assertEqual((self.store.load("h", 1, "u1"), self.store.load("h", 1, "u2")), ("novo", "dois"))
        self.assertTrue(self.store.forget("h", 1, "u1"))
        self.assertIsNone(self.store.load("h", 1, "u1"))
        self.assertEqual(self.store.load("h", 1, "u2"), "dois")
        self.assertFalse(self.store.forget("h", 1, "u1"))  # nada a apagar
        self.assertTrue(self.store.forget("h", 1, "u2"))
        self.assertFalse(self.path.exists())  # sem itens, o arquivo some

    def test_unreadable_files_and_items_are_just_missing_never_a_crash(self):
        self.path.write_text("{nao e json", encoding="utf-8")
        self.assertIsNone(self.store.load("h", 1, "u"))
        self.path.write_text(json.dumps({"versao": 99, "itens": {"h|1|u": "AAAA"}}), encoding="utf-8")
        self.assertIsNone(self.store.load("h", 1, "u"))
        self.path.write_text(json.dumps({"versao": 1, "itens": {"h|1|u": "%%%nao-base64%%%"}}), encoding="utf-8")
        self.assertIsNone(self.store.load("h", 1, "u"))
        self.path.write_text(json.dumps({"versao": 1, "itens": {"h|1|u": 123}}), encoding="utf-8")
        self.assertIsNone(self.store.load("h", 1, "u"))
        self.store.save("h", 1, "u", PASSWORD)  # e volta a funcionar por cima de um arquivo ruim
        self.assertEqual(self.store.load("h", 1, "u"), PASSWORD)

    def test_empty_password_is_refused_and_failures_do_not_leak_it(self):
        with self.assertRaises(SecretStoreError):
            self.store.save("h", 1, "u", "")
        with patch("app.secret_store.os.replace", side_effect=OSError(f"disco cheio {PASSWORD}")):
            with self.assertRaises(SecretStoreError) as raised:
                self.store.save("h", 1, "u", PASSWORD)
        self.assertNotIn(PASSWORD, str(raised.exception))
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [])  # nada de arquivo temporario sobrando

    def test_repr_and_str_never_show_secrets(self):
        self.store.save("h", 1, "u", PASSWORD)
        self.assertNotIn(PASSWORD, repr(self.store) + str(self.store))

    def test_write_is_atomic_and_leaves_no_temporary_file(self):
        self.store.save("h", 1, "u", PASSWORD)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [FILE_NAME])


class ContainmentTests(unittest.TestCase):
    def test_only_the_secret_store_touches_the_system_encryption_and_settings_never_hold_passwords(self):
        root = Path(__file__).resolve().parent.parent / "app"
        users = [
            path.relative_to(root).as_posix()
            for path in root.rglob("*.py")
            if "CryptProtectData" in path.read_text(encoding="utf-8") or "CryptUnprotectData" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(users, ["secret_store.py"])
        settings_source = (root / "settings.py").read_text(encoding="utf-8").lower()
        for save in ("def save_model_db", "def save_sqlaudit_server", "def save_sqlaudit_config"):
            body = settings_source[settings_source.index(save):].split("\ndef ", 1)[0]
            self.assertNotIn("password", body, save)
            self.assertNotIn("senha", body, save)


class UnavailableTests(unittest.TestCase):
    def test_without_secure_storage_nothing_is_written_read_or_offered(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / FILE_NAME
            store = SecretStore(path, UnavailableBackend())
            self.assertFalse(store.available)
            with self.assertRaises(SecretStoreError):
                store.save("h", 1, "u", PASSWORD)
            self.assertIsNone(store.load("h", 1, "u"))
            self.assertFalse(store.has("h", 1, "u"))
            self.assertFalse(path.exists())

    @unittest.skipIf(sys.platform == "win32", "so fora do Windows")
    def test_outside_windows_the_default_backend_is_unavailable(self):
        self.assertFalse(make_backend().available)


@unittest.skipUnless(sys.platform == "win32", "DPAPI so existe no Windows")
class WindowsDpapiTests(unittest.TestCase):
    def test_real_dpapi_roundtrip_and_entropy_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / FILE_NAME
            store = SecretStore(path, make_backend())
            self.assertTrue(store.available)
            store.save("10.0.0.35", 43309, "leitura", PASSWORD)
            self.assertEqual(store.load("10.0.0.35", 43309, "leitura"), PASSWORD)
            self.assertNotIn(PASSWORD.encode(), path.read_bytes())
            data = json.loads(path.read_text(encoding="utf-8"))
            (blob,) = data["itens"].values()
            data["itens"]["outro|1|u"] = blob
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertIsNone(store.load("outro", 1, "u"))


class SavedLoginTests(StoreTestCase):
    def ask_factory(self, answer):
        calls = []

        def ask(parent, username, message="", title="", can_remember=False):
            calls.append({"username": username, "can_remember": can_remember})
            return answer

        return ask, calls

    def login(self, ask, hint="leitura", store=None):
        return saved_login.get_login(None, "h", 1, hint, "msg", "titulo", ask, store=store or self.store)

    def test_asks_when_nothing_is_saved_and_offers_to_remember_only_when_the_store_exists(self):
        ask, calls = self.ask_factory(("leitura", PASSWORD))
        login = self.login(ask)
        self.assertEqual((login.username, login.password, login.from_saved, login.remembered_now), ("leitura", PASSWORD, False, False))
        self.assertEqual(calls, [{"username": "leitura", "can_remember": True}])
        none = SecretStore(self.path, UnavailableBackend())
        ask2, calls2 = self.ask_factory(("leitura", PASSWORD))
        self.login(ask2, store=none)
        self.assertFalse(calls2[0]["can_remember"])

    def test_remember_saves_and_the_next_time_it_does_not_ask(self):
        ask, calls = self.ask_factory(("leitura", PASSWORD, True))
        first = self.login(ask)
        self.assertTrue(first.remembered_now)
        self.assertEqual(self.store.load("h", 1, "leitura"), PASSWORD)
        ask2, calls2 = self.ask_factory(("x", "y", False))
        second = self.login(ask2)
        self.assertEqual(calls2, [])  # nao perguntou
        self.assertEqual((second.username, second.password, second.from_saved), ("leitura", PASSWORD, True))

    def test_not_ticking_remember_or_a_two_value_answer_saves_nothing(self):
        for answer in (("leitura", PASSWORD, False), ("leitura", PASSWORD)):
            ask, _ = self.ask_factory(answer)
            login = self.login(ask)
            self.assertFalse(login.remembered_now)
            self.assertFalse(self.path.exists())

    def test_cancel_returns_none_and_a_failed_save_still_lets_the_operator_in(self):
        ask, _ = self.ask_factory(None)
        self.assertIsNone(self.login(ask))
        ask2, _ = self.ask_factory(("leitura", PASSWORD, True))
        with patch("app.secret_store.os.replace", side_effect=OSError("sem permissao")):
            login = self.login(ask2)
        self.assertEqual((login.password, login.remembered_now), (PASSWORD, False))

    def test_the_saved_password_is_only_used_for_the_saved_user_server_and_port(self):
        self.store.save("h", 1, "leitura", PASSWORD)
        ask, calls = self.ask_factory(("outro", "x", False))
        login = self.login(ask, hint="outro")  # o usuario sugerido e outro: pergunta
        self.assertEqual(len(calls), 1)
        self.assertFalse(login.from_saved)
        ask3, calls3 = self.ask_factory(("leitura", "y", False))
        saved_login.get_login(None, "h", 2, "leitura", "m", "t", ask3, store=self.store)  # outra porta: pergunta
        self.assertEqual(len(calls3), 1)

    def test_forget_has_saved_and_auth_failure_detection_and_repr_hides_the_password(self):
        self.store.save("h", 1, "leitura", PASSWORD)
        self.assertTrue(saved_login.has_saved("h", 1, "leitura", self.store))
        self.assertFalse(saved_login.has_saved("h", 1, "", self.store))
        self.assertTrue(saved_login.forget("h", 1, "leitura", self.store))
        self.assertFalse(saved_login.forget("h", 1, "", self.store))
        self.assertTrue(saved_login.is_auth_failure("Usuario ou senha do banco incorretos."))
        self.assertFalse(saved_login.is_auth_failure("Conexao recusada"))
        self.assertNotIn(PASSWORD, repr(saved_login.Login("leitura", PASSWORD)))


if __name__ == "__main__":
    unittest.main()
