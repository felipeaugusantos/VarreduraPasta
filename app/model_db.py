"""Execucao dos scripts SQL no banco modelo (MySQL), sem Tkinter.

Usa o PyMySQL (biblioteca Python pura, embutida no exe). O usuario e a senha
sao digitados pelo operador a cada execucao e nunca sao gravados em arquivo
nem em log. Cada script roda numa transacao: se um comando falhar, o que for
DML e desfeito; comandos DDL (CREATE/ALTER/DROP) o MySQL confirma sozinho e nao
da para desfazer.
"""

from dataclasses import dataclass
from pathlib import Path

from app.settings import load_model_db

CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 1800
PREVIEW_CHARS = 90


class ModelDbError(Exception):
    """Falha com mensagem pronta para o operador."""


@dataclass(frozen=True)
class ScriptRunResult:
    name: str
    ok: bool
    message: str
    statements: int = 0


def split_statements(text):
    """Separa o script em comandos, como o cliente `mysql`.

    Ignora comentarios (`-- `, `#`, `/* */`; `/*! ... */` e mantido), respeita
    textos entre aspas/crases e entende `DELIMITER` (procedures, triggers).
    """
    statements = []
    buffer = []
    delimiter = ";"
    i, n = 0, len(text)

    def flush():
        statement = "".join(buffer).strip()
        buffer.clear()
        if statement:
            statements.append(statement)

    while i < n:
        char = text[i]
        if not "".join(buffer).strip() and text[i : i + 10].lower() == "delimiter " and (
            i == 0 or text[i - 1] in "\r\n"
        ):
            end = text.find("\n", i)
            end = n if end == -1 else end
            new_delimiter = text[i + 10 : end].strip()
            if new_delimiter:
                delimiter = new_delimiter
            buffer.clear()
            i = end + 1
            continue
        if text.startswith(delimiter, i):
            flush()
            i += len(delimiter)
            continue
        if char == "#" or (text.startswith("--", i) and (i + 2 >= n or text[i + 2] in " \t\r\n")):
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            if text.startswith("/*!", i):
                buffer.append(text[i:end])
            i = end
            continue
        if char in "'\"`":
            quote = char
            j = i + 1
            while j < n:
                if text[j] == "\\" and quote != "`":
                    j += 2
                    continue
                if text[j] == quote:
                    if j + 1 < n and text[j + 1] == quote:  # aspas dobradas
                        j += 2
                        continue
                    break
                j += 1
            buffer.append(text[i : j + 1])
            i = j + 1
            continue
        buffer.append(char)
        i += 1
    flush()
    return statements


def read_script_text(path):
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _preview(statement):
    one_line = " ".join(statement.split())
    return one_line if len(one_line) <= PREVIEW_CHARS else one_line[:PREVIEW_CHARS] + "..."


def connect(settings, username, password):
    """Abre a conexao com o banco modelo. Levanta ModelDbError com texto claro."""
    if not settings["host"] or not settings["database"]:
        raise ModelDbError("Configure o servidor e o nome do banco modelo antes de executar.")
    try:
        import pymysql
    except ImportError as error:
        raise ModelDbError(f"Biblioteca PyMySQL nao encontrada no sistema: {error}") from error
    try:
        return pymysql.connect(
            host=settings["host"],
            port=int(settings["port"]),
            user=username,
            password=password,
            database=settings["database"],
            charset="utf8mb4",
            autocommit=False,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            read_timeout=READ_TIMEOUT_SECONDS,
            # Criptografa a conexao quando o servidor oferece TLS (sem validar o
            # certificado, que costuma ser autoassinado numa rede interna).
            ssl={"verify_mode": "none"},
        )
    except pymysql.err.OperationalError as error:
        code = error.args[0] if error.args else 0
        detail = error.args[1] if len(error.args) > 1 else str(error)
        if code == 1045:
            raise ModelDbError("Usuario ou senha do banco incorretos.") from error
        if code == 1049:
            raise ModelDbError(f"Banco '{settings['database']}' nao existe no servidor.") from error
        raise ModelDbError(
            f"Nao foi possivel conectar em {settings['host']}:{settings['port']} ({detail})."
        ) from error
    except RuntimeError as error:
        # PyMySQL pede o pacote 'cryptography' para senha sha256/caching_sha2 sem TLS.
        raise ModelDbError(
            "O servidor exige autenticacao segura (caching_sha2_password) sem TLS: "
            f"habilite TLS no MySQL ou use mysql_native_password no usuario. ({error})"
        ) from error
    except OSError as error:
        raise ModelDbError(
            f"Nao foi possivel conectar em {settings['host']}:{settings['port']} ({error})."
        ) from error


def test_connection(settings, username, password, connector=connect):
    """(ok, mensagem) com a versao do servidor e o banco conectado."""
    try:
        connection = connector(settings, username, password)
    except ModelDbError as error:
        return False, str(error)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION(), DATABASE()")
            version, database = cursor.fetchone()
        return True, f"Conectado ao MySQL {version}, banco '{database}'."
    except Exception as error:
        return False, f"Conectou, mas a consulta de teste falhou: {error}"
    finally:
        connection.close()


def run_script(connection, path):
    """Executa um arquivo SQL numa transacao. Retorna a quantidade de comandos."""
    statements = split_statements(read_script_text(path))
    if not statements:
        raise ModelDbError("O script nao tem nenhum comando para executar.")
    try:
        with connection.cursor() as cursor:
            for number, statement in enumerate(statements, start=1):
                try:
                    cursor.execute(statement)
                except Exception as error:
                    connection.rollback()
                    raise ModelDbError(
                        f"Erro no comando {number} de {len(statements)}: {_describe(error)}\n"
                        f"Comando: {_preview(statement)}\n"
                        "O que ja era DDL (CREATE/ALTER/DROP) nao pode ser desfeito."
                    ) from error
        connection.commit()
    except ModelDbError:
        raise
    except Exception as error:
        connection.rollback()
        raise ModelDbError(f"Falha ao executar o script: {_describe(error)}") from error
    return len(statements)


def _describe(error):
    args = getattr(error, "args", ())
    if len(args) >= 2:
        return f"({args[0]}) {args[1]}"
    return str(error)


def run_scripts(directory, names, username, password, on_done=None, connector=connect, settings=None):
    """Executa os scripts na ordem, parando no primeiro que falhar.

    `on_done(resultado)` e chamado a cada script (para marcar/auditar na hora).
    Retorna a lista de ScriptRunResult dos scripts que chegaram a rodar.
    """
    settings = settings or load_model_db()
    results = []
    connection = connector(settings, username, password)
    try:
        for name in names:
            path = Path(directory) / name
            try:
                count = run_script(connection, path)
                result = ScriptRunResult(name, True, f"{count} comando(s) executado(s).", count)
            except (ModelDbError, OSError) as error:
                result = ScriptRunResult(name, False, str(error))
            results.append(result)
            if on_done:
                on_done(result)
            if not result.ok:
                break
    finally:
        try:
            connection.close()
        except Exception:
            pass
    return results
