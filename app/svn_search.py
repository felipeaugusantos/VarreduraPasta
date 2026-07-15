import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SvnLogEntry:
    revision: str
    author: str
    date: str
    line_count: str
    paths: list[str]
    message_lines: list[str]
    raw_lines: list[str]

    @property
    def requirement(self):
        for line in self.message_lines:
            normalized = _clean_log_line(line)
            if normalized.lower().startswith("req"):
                return normalized
        return ""

    @property
    def display_date(self):
        if " -0300" in self.date:
            return self.date.split(" -0300", 1)[0]
        if " (" in self.date:
            return self.date.split(" (", 1)[0]
        return self.date

    @property
    def summary(self):
        for line in self.message_lines:
            normalized = _clean_log_line(line)
            if not normalized:
                continue
            normalized_key = _normalized_text(normalized)
            if normalized.startswith("-") or normalized_key.startswith("analise"):
                continue
            if normalized_key.startswith("req"):
                continue
            return normalized[:140]
        return ""

    @property
    def changed_paths_text(self):
        if not self.paths:
            return ""
        first_path = self.paths[0]
        if len(self.paths) == 1:
            return first_path
        return f"{first_path} (+{len(self.paths) - 1})"

    @property
    def first_changed_path(self):
        if not self.paths:
            return ""
        path = self.paths[0]
        if len(path) > 2 and path[0] in {"A", "M", "D", "R"} and path[1] == " ":
            return path[2:].strip()
        return path

    @property
    def changed_version(self):
        path = self.first_changed_path.rstrip("/")
        if not path:
            return ""
        return path.rsplit("/", 1)[-1]

    @property
    def changed_parent_path(self):
        path = self.first_changed_path.rstrip("/")
        if "/" not in path:
            return ""
        return path.rsplit("/", 1)[0]

    @property
    def detail_text(self):
        parts = [
            f"Revisao: {self.revision}",
            f"Autor: {self.author}",
            f"Data: {self.date}",
            f"Requisito: {self.requirement or '-'}",
            "",
            "Caminhos alterados:",
        ]
        if self.paths:
            parts.extend(f"  {path}" for path in self.paths)
        else:
            parts.append("  -")

        parts.extend(["", "Descricao:"])
        if self.message_lines:
            parts.extend(_clean_log_line(line) for line in self.message_lines)
        else:
            parts.append("-")
        return "\n".join(parts)


@dataclass
class SvnSearchResult:
    ok: bool
    base_path: str
    revision: str
    requirement: str
    source: str
    lines: list[str]
    entries: list[SvnLogEntry]
    error: str


def search_svn(base_path, revision="", requirement="", limit=200, timeout=60):
    base_path = str(base_path or "").strip()
    revision = str(revision or "").strip()
    requirement = str(requirement or "").strip()

    if not base_path:
        return _error(base_path, revision, requirement, "Informe o caminho base do SVN.")
    if not revision and not requirement:
        return _error(
            base_path,
            revision,
            requirement,
            "Informe uma revisao, um requisito ou os dois para consultar.",
        )

    svn_result = _search_with_svn_cli(base_path, revision, requirement, limit, timeout)
    if svn_result is not None:
        return svn_result

    if requirement and Path(base_path).exists():
        return _search_local_files(base_path, revision, requirement, limit)

    return _error(
        base_path,
        revision,
        requirement,
        "Nao foi possivel executar o comando svn. Verifique se o SVN esta instalado "
        "e se o caminho base esta correto.",
    )


def _search_with_svn_cli(base_path, revision, requirement, limit, timeout):
    command = ["svn", "log", base_path, "-v"]
    if revision:
        command.extend(["-r", revision])
    if requirement and not revision:
        command.extend(["--search", requirement, "-l", str(limit)])

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return _error(
            base_path,
            revision,
            requirement,
            "A consulta SVN demorou demais e foi interrompida.",
        )

    output = (completed.stdout or "").splitlines()
    error_output = (completed.stderr or "").strip()
    if completed.returncode != 0:
        return _error(
            base_path,
            revision,
            requirement,
            error_output or f"svn log retornou codigo {completed.returncode}.",
        )

    entries = parse_svn_log(output)
    entries_were_filtered = False
    if revision and requirement:
        entries_were_filtered = True
        entries = [
            entry for entry in entries if _entry_matches_requirement(entry, requirement)
        ]

    if entries:
        lines = [
            line
            for entry in entries
            for line in entry.raw_lines
            if line.strip()
        ]
    elif entries_were_filtered:
        lines = ["Nenhum resultado encontrado."]
    else:
        lines = [line.rstrip() for line in output if line.rstrip()]
    if not lines:
        lines = ["Nenhum resultado encontrado."]

    return SvnSearchResult(
        ok=True,
        base_path=base_path,
        revision=revision,
        requirement=requirement,
        source="svn",
        lines=lines,
        entries=entries,
        error="",
    )


def _search_local_files(base_path, revision, requirement, limit):
    base_directory = Path(base_path)
    matches = []
    requirement_lower = requirement.lower()

    try:
        for path in base_directory.rglob("*"):
            if len(matches) >= limit:
                break
            if not path.is_file():
                continue
            relative_path = _relative_path(path, base_directory)
            if requirement_lower in relative_path.lower():
                matches.append(relative_path)
                continue
            if _file_contains(path, requirement_lower):
                matches.append(relative_path)
    except OSError as error:
        return _error(base_path, revision, requirement, f"Erro ao buscar na pasta: {error}")

    lines = matches or ["Nenhum arquivo encontrado com o requisito informado."]
    return SvnSearchResult(
        ok=True,
        base_path=base_path,
        revision=revision,
        requirement=requirement,
        source="arquivos",
        lines=lines,
        entries=[],
        error="",
    )


def parse_svn_log(lines):
    entries = []
    current = []
    for line in lines:
        if line.startswith("--------"):
            if current:
                entry = _parse_entry(current)
                if entry:
                    entries.append(entry)
                current = []
            continue
        current.append(line.rstrip())

    if current:
        entry = _parse_entry(current)
        if entry:
            entries.append(entry)

    return entries


def _parse_entry(lines):
    header_index = None
    for index, line in enumerate(lines):
        if line.startswith("r") and " | " in line:
            header_index = index
            break
    if header_index is None:
        return None

    header_parts = [part.strip() for part in lines[header_index].split("|")]
    revision = header_parts[0] if len(header_parts) > 0 else ""
    author = header_parts[1] if len(header_parts) > 1 else ""
    date = header_parts[2] if len(header_parts) > 2 else ""
    line_count = header_parts[3] if len(header_parts) > 3 else ""

    paths = []
    message_lines = []
    in_changed_paths = False
    for line in lines[header_index + 1:]:
        stripped = line.strip()
        if stripped == "Changed paths:":
            in_changed_paths = True
            continue
        if in_changed_paths and not stripped:
            in_changed_paths = False
            continue
        if in_changed_paths and _is_changed_path_line(stripped):
            paths.append(stripped)
            continue
        if in_changed_paths:
            in_changed_paths = False
        message_lines.append(line)

    return SvnLogEntry(
        revision=revision,
        author=author,
        date=date,
        line_count=line_count,
        paths=paths,
        message_lines=[line for line in message_lines if line.strip()],
        raw_lines=lines,
    )


def _file_contains(path, term):
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as file:
            for line in file:
                if term in line.lower():
                    return True
    except OSError:
        return False
    return False


def _is_changed_path_line(line):
    if len(line) < 3:
        return False
    return line[0] in {"A", "M", "D", "R"} and line[1:].lstrip().startswith("/")


def _clean_log_line(line):
    return str(line or "").replace("?", "").strip()


def _entry_matches_requirement(entry, requirement):
    term = str(requirement or "").strip().lower()
    if not term:
        return True
    searchable = "\n".join(entry.raw_lines).lower()
    return term in searchable


def _normalized_text(value):
    text = _clean_log_line(value).lower()
    return "".join(
        char
        for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )


def _relative_path(path, base_directory):
    try:
        return str(path.relative_to(base_directory))
    except ValueError:
        return str(path)


def _error(base_path, revision, requirement, message):
    return SvnSearchResult(
        ok=False,
        base_path=base_path,
        revision=revision,
        requirement=requirement,
        source="",
        lines=[],
        entries=[],
        error=message,
    )
