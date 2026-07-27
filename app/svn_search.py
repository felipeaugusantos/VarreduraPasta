import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path


SVN_INTERRUPTED_EXIT_CODE = 0xC000013A
SVN_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class _SvnSearchCancelled(Exception):
    pass


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
        version_path = _split_version_path(self.first_changed_path)
        if version_path:
            return version_path[0]
        path = self.first_changed_path.rstrip("/")
        return path.rsplit("/", 1)[-1] if path else ""

    @property
    def changed_parent_path(self):
        version_path = _split_version_path(self.first_changed_path)
        if version_path:
            return version_path[1]
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


def search_svn(
    base_path,
    revision="",
    requirement="",
    limit=200,
    timeout=60,
    progress_callback=None,
    cancellation_event=None,
    find_all=False,
):
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

    svn_result = _search_with_svn_cli(
        base_path,
        revision,
        requirement,
        limit,
        timeout,
        progress_callback,
        cancellation_event,
        find_all,
    )
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


def _search_with_svn_cli(
    base_path,
    revision,
    requirement,
    limit,
    timeout,
    progress_callback=None,
    cancellation_event=None,
    find_all=False,
):
    if requirement and not revision:
        return _search_requirement_in_batches(
            base_path,
            requirement,
            limit,
            timeout,
            progress_callback,
            cancellation_event,
            find_all,
        )

    command = ["svn", "log", base_path, "-v"]
    if revision:
        command.extend(["-r", revision])

    try:
        completed = _run_svn_command(command, timeout, cancellation_event)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return _error(
            base_path,
            revision,
            requirement,
            "A consulta SVN demorou demais e foi interrompida.",
        )
    except _SvnSearchCancelled:
        return _cancelled(base_path, revision, requirement)

    output = (completed.stdout or "").splitlines()
    error_output = (completed.stderr or "").strip()
    if completed.returncode != 0:
        if _svn_process_was_interrupted(completed.returncode):
            return _error(
                base_path,
                revision,
                requirement,
                "O processo SVN foi interrompido pelo Windows. Tente novamente.",
            )
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


def _search_requirement_in_batches(
    base_path,
    requirement,
    limit,
    timeout,
    progress_callback,
    cancellation_event,
    find_all,
):
    latest_command = ["svn", "log", base_path, "-q", "-l", "1"]
    try:
        latest = _run_svn_command(latest_command, timeout, cancellation_event)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return _error(
            base_path,
            "",
            requirement,
            "Nao foi possivel identificar a revisao mais recente do SVN.",
        )
    except _SvnSearchCancelled:
        return _cancelled(base_path, "", requirement)

    if _svn_process_was_interrupted(latest.returncode):
        _notify_progress(
            progress_callback,
            "Reiniciando processo SVN interrompido",
        )
        try:
            latest = _run_svn_command(
                latest_command, timeout, cancellation_event
            )
        except subprocess.TimeoutExpired:
            return _error(
                base_path,
                "",
                requirement,
                "Nao foi possivel identificar a revisao mais recente do SVN.",
            )
        except _SvnSearchCancelled:
            return _cancelled(base_path, "", requirement)

    if latest.returncode != 0:
        if _svn_process_was_interrupted(latest.returncode):
            message = "O processo SVN foi interrompido novamente pelo Windows."
        else:
            message = (
                (latest.stderr or "").strip()
                or f"svn log retornou codigo {latest.returncode}."
            )
        return _error(
            base_path,
            "",
            requirement,
            message,
        )

    latest_match = re.search(r"^r(\d+)\s+\|", latest.stdout or "", re.MULTILINE)
    if not latest_match:
        return _svn_requirement_result(base_path, requirement, [])

    upper_revision = int(latest_match.group(1))
    batch_size = 5000
    minimum_batch_size = 250
    matching_entries = []
    while upper_revision >= 1:
        if cancellation_event and cancellation_event.is_set():
            return _cancelled(base_path, "", requirement)
        lower_revision = max(1, upper_revision - batch_size + 1)
        _notify_progress(
            progress_callback,
            f"Pesquisando revisoes {upper_revision} a {lower_revision}",
        )
        command = [
            "svn",
            "log",
            base_path,
            "-v",
            "--search",
            requirement,
            "-r",
            f"{upper_revision}:{lower_revision}",
        ]
        timed_out = False
        try:
            completed = _run_svn_command(command, timeout, cancellation_event)
        except subprocess.TimeoutExpired:
            completed = None
            timed_out = True
        except _SvnSearchCancelled:
            return _cancelled(base_path, "", requirement)

        process_interrupted = (
            completed is not None
            and _svn_process_was_interrupted(completed.returncode)
        )
        if timed_out or process_interrupted:
            if batch_size > minimum_batch_size:
                batch_size = max(minimum_batch_size, batch_size // 2)
                _notify_progress(
                    progress_callback,
                    f"Repetindo com bloco de {batch_size} revisoes",
                )
                continue
            return _error(
                base_path,
                "",
                requirement,
                (
                    "O processo SVN continuou sendo interrompido pelo Windows."
                    if process_interrupted
                    else "O servidor SVN nao respondeu nem para um bloco "
                    "reduzido de revisoes."
                ),
            )

        if completed.returncode != 0:
            return _error(
                base_path,
                "",
                requirement,
                (completed.stderr or "").strip()
                or f"svn log retornou codigo {completed.returncode}.",
            )

        entries = parse_svn_log((completed.stdout or "").splitlines())
        if entries:
            matching_entries.extend(entries)
            if not find_all or len(matching_entries) >= limit:
                return _svn_requirement_result(
                    base_path, requirement, matching_entries[:limit]
                )
        upper_revision = lower_revision - 1

    return _svn_requirement_result(
        base_path, requirement, matching_entries[:limit]
    )


def _run_svn_command(command, timeout, cancellation_event=None):
    if cancellation_event is None:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=SVN_CREATION_FLAGS,
        )

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=SVN_CREATION_FLAGS,
    )
    deadline = time.monotonic() + timeout
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.2)
            return subprocess.CompletedProcess(
                command,
                process.returncode,
                stdout,
                stderr,
            )
        except subprocess.TimeoutExpired:
            if cancellation_event.is_set():
                _stop_process(process)
                raise _SvnSearchCancelled
            if time.monotonic() >= deadline:
                _stop_process(process, force=True)
                raise subprocess.TimeoutExpired(command, timeout)


def _stop_process(process, force=False):
    try:
        process.kill() if force else process.terminate()
        process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
    except OSError:
        pass


def _svn_process_was_interrupted(return_code):
    try:
        normalized = int(return_code) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return False
    return normalized == SVN_INTERRUPTED_EXIT_CODE


def _notify_progress(progress_callback, message):
    if progress_callback:
        progress_callback(message)


def _svn_requirement_result(base_path, requirement, entries):
    lines = [
        line
        for entry in entries
        for line in entry.raw_lines
        if line.strip()
    ]
    if not lines:
        lines = ["Nenhum resultado encontrado."]
    return SvnSearchResult(
        ok=True,
        base_path=base_path,
        revision="",
        requirement=requirement,
        source="svn",
        lines=lines,
        entries=entries,
        error="",
    )


def _cancelled(base_path, revision, requirement):
    return SvnSearchResult(
        ok=False,
        base_path=base_path,
        revision=revision,
        requirement=requirement,
        source="svn",
        lines=[],
        entries=[],
        error="Consulta SVN cancelada pelo usuario.",
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


def _split_version_path(path):
    cleaned_path = str(path or "").strip().rstrip("/")
    if not cleaned_path:
        return None
    parts = [part for part in cleaned_path.split("/") if part]
    for index, part in enumerate(parts):
        if _looks_like_project_version(part):
            parent = "/" + "/".join(parts[:index]) if index else ""
            return part, parent
    return None


def _looks_like_project_version(value):
    normalized = str(value or "").strip()
    if "_" in normalized:
        normalized = normalized.split("_", 1)[0]
    return bool(re.fullmatch(r"\d+(?:\.\d+){4,}(?:\.\d+)*", normalized))


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
