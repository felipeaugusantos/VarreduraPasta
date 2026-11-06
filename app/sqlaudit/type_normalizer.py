"""Normaliza tipos de coluna do MySQL para comparar script x banco.

O mesmo tipo aparece de formas diferentes: `INTEGER` no script e `int` no banco;
`int(11)` no MySQL 5.7 e `int` no 8.0.19+; `BOOLEAN` vira `tinyint(1)`;
`decimal` vira `decimal(10,0)`. Duas formas equivalentes NAO podem ser
sinalizadas como divergencia. Tipo desconhecido volta como `recognized=False`:
o comparador trata como inconclusivo, nunca como divergente.
"""

import re
from dataclasses import dataclass

INTEGER_TYPES = {"tinyint", "smallint", "mediumint", "int", "bigint"}
SIMPLE_TYPES = {
    "float", "double", "tinytext", "text", "mediumtext", "longtext",
    "tinyblob", "blob", "mediumblob", "longblob", "date", "json", "year",
    "geometry", "point", "linestring", "polygon",
}
LENGTH_TYPES = {"char", "varchar", "binary", "varbinary", "bit"}
TIME_TYPES = {"time", "datetime", "timestamp"}
LIST_TYPES = {"enum", "set"}
_SYNONYMS = {
    "integer": "int", "int4": "int", "int8": "bigint", "int2": "smallint", "int1": "tinyint",
    "middleint": "mediumint", "dec": "decimal", "numeric": "decimal", "fixed": "decimal",
    "real": "double", "double precision": "double", "float8": "double", "float4": "float",
}
_SHAPE = re.compile(r"^(?P<base>[a-z0-9]+(?: precision)?)\s*(?P<args>\(.*\))?\s*(?P<rest>[a-z ]*)$", re.DOTALL)


@dataclass(frozen=True)
class NormalizedType:
    text: str
    recognized: bool


def _attributes(rest):
    words = rest.split()
    attributes = []
    if "unsigned" in words:
        attributes.append("unsigned")
    if "zerofill" in words:
        attributes.append("zerofill")
    unknown = [word for word in words if word not in ("unsigned", "signed", "zerofill")]
    return attributes, unknown


def _numbers(args):
    parts = [part.strip() for part in args.strip("()").split(",")]
    return parts if all(part.isdigit() for part in parts) and parts != [""] else None


def normalize_type(text):
    raw = " ".join(str(text or "").split())
    lowered = raw.lower()
    if not lowered:
        return NormalizedType("", False)
    base_match = re.match(r"^(enum|set)\s*\((.*)\)\s*$", raw, re.IGNORECASE | re.DOTALL)
    if base_match:  # os valores mantem a caixa: o MySQL guarda como foi escrito
        values = re.sub(r"'\s*,\s*'", "','", base_match.group(2).strip())
        return NormalizedType(f"{base_match.group(1).lower()}({values})", True)
    match = _SHAPE.match(lowered)
    if not match:
        return NormalizedType(lowered, False)
    base = _SYNONYMS.get(match.group("base"), match.group("base"))
    args = match.group("args")
    attributes, unknown = _attributes(match.group("rest"))
    numbers = _numbers(args) if args else []
    if base in ("bool", "boolean"):
        base, numbers, args = "tinyint", ["1"], "(1)"
    suffix = (" " + " ".join(attributes)) if attributes else ""
    if unknown or numbers is None:
        return NormalizedType(lowered, False)

    if base in INTEGER_TYPES:
        if len(numbers) > 1:
            return NormalizedType(lowered, False)
        shown = "(1)" if base == "tinyint" and numbers == ["1"] else ""
        return NormalizedType(f"{base}{shown}{suffix}", True)
    if base == "decimal":
        precision = numbers[0] if numbers else "10"
        scale = numbers[1] if len(numbers) > 1 else "0"
        return NormalizedType(f"decimal({precision},{scale}){suffix}", True)
    if base in ("float", "double"):
        shown = f"({','.join(numbers)})" if len(numbers) == 2 else ""
        return NormalizedType(f"{base}{shown}{suffix}", len(numbers) in (0, 2))
    if base in ("char", "binary", "bit"):
        length = numbers[0] if numbers else "1"
        return NormalizedType(f"{base}({length})", len(numbers) <= 1)
    if base in ("varchar", "varbinary"):
        return NormalizedType(f"{base}({numbers[0]})" if len(numbers) == 1 else lowered, len(numbers) == 1)
    if base in TIME_TYPES:
        precision = "" if not numbers or numbers == ["0"] else f"({numbers[0]})"
        return NormalizedType(f"{base}{precision}", len(numbers) <= 1)
    if base == "year":
        return NormalizedType("year", len(numbers) <= 1)
    if base in SIMPLE_TYPES:
        return NormalizedType(base, not numbers)
    return NormalizedType(lowered, False)
