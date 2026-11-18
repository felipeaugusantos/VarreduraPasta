"""Login do banco com senha lembrada (opcional), sem Tkinter.

Junta o dialogo de login (`ask`) com o `SecretStore`: se o operador ja escolheu lembrar a senha
daquele servidor/usuario, ela e usada sem perguntar; senao o dialogo aparece, com a caixa
"Lembrar" quando o armazenamento seguro existe. A senha so sai daqui para quem vai conectar:
nunca e logada nem guardada fora do `SecretStore`.
"""

from dataclasses import dataclass, field

from app.secret_store import SecretStoreError, default_store

AUTH_FAILED_TEXT = "Usuario ou senha do banco incorretos"


@dataclass(frozen=True)
class Login:
    username: str
    password: str = field(repr=False)
    from_saved: bool = False  # usou a senha lembrada (se falhar, descarta)
    remembered_now: bool = False  # o operador acabou de pedir para lembrar


def get_login(parent, host, port, username_hint, message, title, ask, store=None):
    """Login para `host:port`. `ask(parent, usuario, message=, title=, can_remember=)` e o dialogo.

    Devolve `Login` ou None (cancelou).
    """
    store = store if store is not None else default_store()
    if store.available and username_hint:
        saved = store.load(host, port, username_hint)
        if saved is not None:
            return Login(username_hint, saved, from_saved=True)
    answer = ask(parent, username_hint, message=message, title=title, can_remember=store.available)
    if answer is None:
        return None
    username, password = answer[0], answer[1]
    remember = bool(answer[2]) if len(answer) > 2 else False
    remembered = False
    if remember and store.available:
        try:
            store.save(host, port, username, password)
            remembered = True
        except SecretStoreError:
            remembered = False  # nao impede o uso: so nao fica lembrada
    return Login(username, password, remembered_now=remembered)


def has_saved(host, port, username, store=None):
    store = store if store is not None else default_store()
    return bool(username) and store.has(host, port, username)


def forget(host, port, username, store=None):
    """Apaga a senha lembrada. True se havia."""
    store = store if store is not None else default_store()
    if not username:
        return False
    return store.forget(host, port, username)


def is_auth_failure(text):
    return AUTH_FAILED_TEXT.lower() in str(text).lower()
