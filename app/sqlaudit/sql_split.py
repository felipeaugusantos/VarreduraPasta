"""Divide um script SQL em comandos guardando a posicao de cada um.

Mesma ideia do `split_statements` de `app/model_db.py` (que continua intacto,
pois serve a execucao), mas pensada para analise: cada comando guarda linha e
deslocamento no texto original, e aspas ou comentarios nao fechados ficam
sinalizados em `problem` em vez de passarem despercebidos.
"""

from bisect import bisect_right
from dataclasses import dataclass

DEFAULT_DELIMITER = ";"
_QUOTES = "'\"`"


@dataclass(frozen=True)
class SqlStatement:
    index: int
    text: str
    start: int
    end: int
    line_start: int
    line_end: int
    problem: str = ""


def _line_finder(text):
    newlines = [i for i, char in enumerate(text) if char == "\n"]
    return lambda offset: bisect_right(newlines, offset - 1) + 1


def _is_delimiter_command(text, i, n):
    """`DELIMITER x` so vale no inicio de uma linha (so espacos antes dele)."""
    if text[i : i + 9].lower() != "delimiter" or i + 9 >= n or text[i + 9] not in " \t":
        return False
    line_start = text.rfind("\n", 0, i) + 1
    return not text[line_start:i].strip()


def split_with_positions(text):
    """Lista de `SqlStatement`. Comentarios `--`, `#` e `/* */` saem do texto
    (`/*! ... */` e mantido); strings e crases sao respeitadas; `DELIMITER` e entendido."""
    statements = []
    buffer = []
    delimiter = DEFAULT_DELIMITER
    i, n = 0, len(text)
    line_of = _line_finder(text)
    state = {"start": None, "end": 0, "problem": ""}

    def add(piece, begin, end):
        buffer.append(piece)
        if state["start"] is None and piece.strip():
            state["start"] = begin + (len(piece) - len(piece.lstrip()))
        if piece.strip():
            state["end"] = end

    def flush():
        statement = "".join(buffer).strip()
        if statement:
            start, end = state["start"], state["end"]
            statements.append(
                SqlStatement(
                    len(statements) + 1,
                    statement,
                    start,
                    end,
                    line_of(start),
                    line_of(max(end - 1, start)),
                    state["problem"],
                )
            )
        buffer.clear()
        state.update(start=None, end=0, problem="")

    while i < n:
        char = text[i]
        if state["start"] is None and char in "dD" and _is_delimiter_command(text, i, n):
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
            closed = end != -1
            end = n if not closed else end + 2
            if text.startswith("/*!", i):
                add(text[i:end], i, end)
            else:
                buffer.append(" ")  # comentario separa palavras, como no MySQL
            if not closed:
                state["problem"] = "comentario /* */ nao fechado"
            i = end
            continue
        if char in _QUOTES:
            j = i + 1
            closed = False
            while j < n:
                if text[j] == "\\" and char != "`":
                    j += 2
                    continue
                if text[j] == char:
                    if j + 1 < n and text[j + 1] == char:  # aspas dobradas
                        j += 2
                        continue
                    closed = True
                    break
                j += 1
            end = min(j + 1, n)
            add(text[i:end], i, end)
            if not closed:
                state["problem"] = "aspas nao fechadas"
            i = end
            continue
        add(char, i, i + 1)
        i += 1
    flush()
    return statements
