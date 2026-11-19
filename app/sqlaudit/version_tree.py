"""Arvore de versoes (qual versao deriva de qual) e scripts efetivos de cada uma.

Um banco novo nasce de dump e restauracao do banco da versao-base. Entao o
estado esperado de uma versao = scripts da base ate o ponto do dump (o FORK) +
scripts da propria versao.

`fork` e o maior numero `SCRIPT_<n>` da BASE que ja estava no dump. So o
operador sabe esse valor (os dumps nao sao guardados): sem ele os scripts da
base entram como INCERTOS, nunca como certos.
"""

from dataclasses import dataclass, field

from app.sqlaudit.file_scanner import sort_key
from app.sqlaudit.version_discovery import version_sort_key


class TreeError(ValueError):
    """Configuracao invalida da arvore (ciclo, base igual a versao...)."""


@dataclass
class VersionNode:
    version: str
    base: str | None = None
    fork: int | None = None  # herda scripts da base com numero <= fork


def suggest_base(version, known_versions):
    """Maior versao conhecida que seja prefixo (por partes) de `version`.

    `74.150.45` sugere `74.150`; `74.150` com so `74` ausente nao sugere nada.
    """
    parts = version.split(".")
    known = set(known_versions)
    for size in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:size])
        if candidate in known:
            return candidate
    return None


class VersionTree:
    def __init__(self):
        self.nodes = {}

    @classmethod
    def build(cls, versions, saved=None):
        """Sugere a base pelo nome e aplica o que o operador ja configurou (`saved`)."""
        tree = cls()
        for version in sorted(set(versions), key=version_sort_key):
            tree.nodes[version] = VersionNode(version, suggest_base(version, versions))
        for version, data in (saved or {}).items():
            if version not in tree.nodes or not isinstance(data, dict):
                continue
            base = str(data.get("base") or "").strip() or None
            fork = data.get("fork")
            try:
                tree.set_base(version, base, int(fork) if fork not in (None, "") else None)
            except (TreeError, TypeError, ValueError):
                continue  # configuracao salva invalida: fica a sugestao
        return tree

    def set_base(self, version, base, fork=None):
        if version not in self.nodes:
            raise TreeError(f"Versao desconhecida: {version}")
        if base is not None and base not in self.nodes:
            raise TreeError(f"Base desconhecida: {base}")
        if base == version:
            raise TreeError("Uma versao nao pode ser base de si mesma.")
        if fork is not None and (not isinstance(fork, int) or fork < 0):
            raise TreeError("O ponto de fork deve ser um numero inteiro >= 0.")
        if base is None:
            fork = None
        previous = self.nodes[version]
        self.nodes[version] = VersionNode(version, base, fork)
        try:
            self.chain(version)
        except TreeError:
            self.nodes[version] = previous
            raise

    def chain(self, version):
        """Da raiz da cadeia ate `version`. Levanta TreeError se houver ciclo."""
        if version not in self.nodes:
            raise TreeError(f"Versao desconhecida: {version}")
        chain, seen = [], set()
        current = version
        while current is not None:
            if current in seen:
                raise TreeError(f"Ciclo na arvore de versoes envolvendo {current}.")
            seen.add(current)
            chain.append(current)
            current = self.nodes[current].base
        return list(reversed(chain))

    def to_saved(self):
        return {
            node.version: {"base": node.base or "", "fork": node.fork}
            for node in self.nodes.values()
        }


@dataclass(frozen=True)
class EffectiveScript:
    version: str  # pasta de onde o arquivo vem
    script: object  # ScriptFile
    inherited: bool
    certain: bool  # False: pode nao ter chegado a este banco (fork ou ordem desconhecidos)


@dataclass
class EffectiveScripts:
    target: str
    scripts: list = field(default_factory=list)
    excluded: list = field(default_factory=list)  # (versao, arquivo) alem do fork
    warnings: list = field(default_factory=list)


def effective_scripts(tree, scripts_by_version, target):
    """Scripts que formam o estado esperado de `target`, na ordem de aplicacao.

    Ordem: pela cadeia (da base mais antiga ate a versao) e, dentro de cada
    pasta, pelo numero. Um script da versao identico (mesmo numero e SHA-256) a
    um ja herdado nao e aplicado duas vezes.
    """
    chain = tree.chain(target)
    result = EffectiveScripts(target=target)
    included = {}  # (numero, sha256) -> EffectiveScript
    numbers_seen = {}  # numero -> (sha256, versao)
    for level, version in enumerate(chain):
        is_target = level == len(chain) - 1
        limit = None if is_target else tree.nodes[chain[level + 1]].fork
        for script in sorted(scripts_by_version.get(version, ()), key=sort_key):
            if not is_target and limit is not None and script.number is not None and script.number > limit:
                result.excluded.append((version, script.relative))
                continue
            key = (script.number, script.sha256)
            if script.number is not None and key in included:
                first = included[key]
                result.warnings.append(
                    f"{script.relative} de {version} repete o de {first.version} (mesmo numero e conteudo): aplicado uma vez."
                )
                if is_target and not first.certain and script.number is not None:
                    # a copia na pasta da propria versao e evidencia de que vale para ela
                    upgraded = EffectiveScript(first.version, first.script, first.inherited, True)
                    result.scripts[result.scripts.index(first)] = upgraded
                    included[key] = upgraded
                continue
            if script.number is not None and script.number in numbers_seen:
                other_hash, other_version = numbers_seen[script.number]
                if other_hash != script.sha256:
                    result.warnings.append(
                        f"Numero {script.number} existe em {other_version} e em {version} com conteudo diferente."
                    )
            certain = script.number is not None and (is_target or limit is not None)
            effective = EffectiveScript(version, script, inherited=not is_target, certain=certain)
            result.scripts.append(effective)
            if script.number is not None:
                included[key] = effective
                numbers_seen.setdefault(script.number, (script.sha256, version))
    uncertain_parents = sorted({item.version for item in result.scripts if item.inherited and not item.certain})
    if uncertain_parents:
        result.warnings.append(
            "Ponto de fork nao informado: scripts herdados de "
            + ", ".join(uncertain_parents)
            + " entram como INCERTOS."
        )
    return result
