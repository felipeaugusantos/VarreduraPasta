"""Senha do banco guardada so no computador, criptografada para o usuario do Windows.

Uso opcional (o operador marca "Lembrar a senha"). A senha NUNCA vai para o `settings.json`,
para log, Auditoria, CSV ou relatorio: fica num arquivo proprio (`credenciais.dat`) em
`%LOCALAPPDATA%\\VarreduraSistema`, criptografada com a DPAPI do Windows (`CryptProtectData`,
so biblioteca padrao via `ctypes`). Essa protecao vale para o usuario do Windows atual
nesta maquina: outro usuario, ou o arquivo copiado para outro computador, nao a abre.
NAO protege contra quem esta logado como o mesmo usuario nem contra programa malicioso
rodando na mesma sessao: use uma conta de banco so com SELECT.

Cada senha fica presa ao servidor, a porta e ao usuario do banco (entram na "entropia" da
DPAPI): um item copiado para outra chave nao abre. Fora do Windows o armazenamento fica
indisponivel (`available` e falso) e nada e gravado.
"""

import base64
import json
import os
import sys
from pathlib import Path

from app.runtime import data_directory

FILE_NAME = "credenciais.dat"
FORMAT_VERSION = 1
ENTROPY_SUFFIX = b"|VarreduraSistema|v1"
CRYPTPROTECT_UI_FORBIDDEN = 0x1


class SecretStoreError(Exception):
    """Nao foi possivel guardar ou ler a senha (mensagem sem a senha)."""


class UnavailableBackend:
    """Sem protecao do sistema (fora do Windows): nada e guardado."""

    available = False

    def protect(self, data, entropy):
        raise SecretStoreError("O armazenamento seguro de senha so existe no Windows.")

    def unprotect(self, data, entropy):
        raise SecretStoreError("O armazenamento seguro de senha so existe no Windows.")


class DpapiBackend:
    """DPAPI do Windows via ctypes (escopo: usuario atual)."""

    available = True

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        self._ctypes = ctypes
        self._Blob = Blob
        self._crypt32 = ctypes.windll.crypt32
        self._kernel32 = ctypes.windll.kernel32
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    def _blob(self, data):
        buffer = self._ctypes.create_string_buffer(data, len(data))
        blob = self._Blob(len(data), self._ctypes.cast(buffer, self._ctypes.POINTER(self._ctypes.c_char)))
        return blob, buffer  # `buffer` precisa continuar vivo enquanto o blob e usado

    def _run(self, function, data, entropy):
        ctypes = self._ctypes
        source, keep_source = self._blob(data)
        extra, keep_extra = self._blob(entropy)
        result = self._Blob()
        ok = function(ctypes.byref(source), None, ctypes.byref(extra), None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result))
        del keep_source, keep_extra
        if not ok:
            raise SecretStoreError("O Windows recusou a operacao de protecao (DPAPI).")
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            self._kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))

    def protect(self, data, entropy):
        return self._run(self._crypt32.CryptProtectData, data, entropy)

    def unprotect(self, data, entropy):
        return self._run(self._crypt32.CryptUnprotectData, data, entropy)


def make_backend():
    """Backend do sistema: DPAPI no Windows, indisponivel nos demais."""
    if sys.platform == "win32":
        try:
            return DpapiBackend()
        except (OSError, AttributeError, ImportError):
            return UnavailableBackend()
    return UnavailableBackend()


def _why(error):
    code = getattr(error, "errno", None)
    return f"erro {code} de disco ou permissao" if code else "erro de disco ou permissao"


def _key(host, port, username):
    return f"{str(host).strip().lower()}|{int(port)}|{str(username).strip().lower()}"


class SecretStore:
    def __init__(self, path=None, backend=None):
        self.path = Path(path) if path else data_directory() / FILE_NAME
        self.backend = backend if backend is not None else make_backend()

    @property
    def available(self):
        return bool(getattr(self.backend, "available", False))

    def __repr__(self):  # nunca expor conteudo
        return f"SecretStore(path={str(self.path)!r}, available={self.available})"

    # ---- arquivo ----
    def _read(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        items = raw.get("itens") if isinstance(raw, dict) and raw.get("versao") == FORMAT_VERSION else None
        return dict(items) if isinstance(items, dict) else {}

    def _write(self, items):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f".{self.path.name}.tmp")
        try:
            temp.write_text(json.dumps({"versao": FORMAT_VERSION, "itens": items}, indent=2), encoding="utf-8")
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _entropy(key):
        return key.encode("utf-8") + ENTROPY_SUFFIX

    # ---- API ----
    def has(self, host, port, username):
        return self.available and _key(host, port, username) in self._read()

    def save(self, host, port, username, password):
        if not self.available:
            raise SecretStoreError("O armazenamento seguro de senha nao esta disponivel neste computador.")
        if not password:
            raise SecretStoreError("Nao ha senha para guardar.")
        key = _key(host, port, username)
        blob = self.backend.protect(password.encode("utf-8"), self._entropy(key))
        items = self._read()
        items[key] = base64.b64encode(blob).decode("ascii")
        try:
            self._write(items)
        except OSError as error:  # nunca repete o texto do erro: ele poderia carregar o conteudo
            raise SecretStoreError(f"Nao foi possivel gravar o arquivo de senhas ({_why(error)}).") from None

    def load(self, host, port, username):
        """A senha guardada ou None (sem item, arquivo ilegivel ou item que o Windows nao abre)."""
        if not self.available:
            return None
        key = _key(host, port, username)
        encoded = self._read().get(key)
        if not isinstance(encoded, str):
            return None
        try:
            return self.backend.unprotect(base64.b64decode(encoded, validate=True), self._entropy(key)).decode("utf-8")
        except (SecretStoreError, ValueError):
            return None

    def forget(self, host, port, username):
        """Apaga a senha guardada para a chave. True se havia algo para apagar."""
        key = _key(host, port, username)
        items = self._read()
        if key not in items:
            return False
        del items[key]
        try:
            if items:
                self._write(items)
            else:
                self.path.unlink(missing_ok=True)
        except OSError as error:
            raise SecretStoreError(f"Nao foi possivel atualizar o arquivo de senhas ({_why(error)}).") from None
        return True


def default_store():
    return SecretStore()
