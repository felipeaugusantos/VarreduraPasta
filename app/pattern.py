import json
from pathlib import Path
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from app.config import KNOWN_FILE_ALTERNATIVES, OPTIONAL_FILE_NAMES
from app.runtime import application_directory


PATTERN_FILE = application_directory() / "project_pattern.json"
SUPPORTED_SUFFIXES = {".exe", ".dll"}
IGNORED_PREFIXES = ("err_",)
VERSION_GROUP_KEYS = {"autcom", "autcomtinta", "autban", "auttin"}


def pattern_key_from_project_name(folder_name):
    base_name = folder_name.removesuffix("_CLOUD")
    parts = base_name.split(".")
    if len(parts) >= 6:
        return ".".join(parts[:6])
    return base_name


def pattern_scope_from_project_name(folder_name):
    scope = "cloud" if folder_name.upper().endswith("_CLOUD") else "local"
    return f"{pattern_key_from_project_name(folder_name)}|{scope}"


def _is_supported_file(file_name):
    path = Path(file_name)
    if path.name.lower().startswith(IGNORED_PREFIXES):
        return False
    if path.name.lower() in OPTIONAL_FILE_NAMES:
        return False
    return path.suffix.lower() in SUPPORTED_SUFFIXES


def generate_pattern_from_project(project_path):
    project_path = Path(project_path)
    files = []
    zips = []

    for item in sorted(project_path.iterdir()):
        if item.is_file() and _is_supported_file(item.name):
            files.append({"file": item.name, "source": "Pasta"})
        elif item.is_file() and item.suffix.lower() == ".zip":
            try:
                with ZipFile(item) as archive:
                    members = [
                        Path(info.filename).name
                        for info in archive.infolist()
                        if _is_supported_file(Path(info.filename).name)
                    ]
            except BadZipFile:
                members = []
            zips.append({"zip": item.name, "files": sorted(set(members))})

    pattern = {
        "source_project": project_path.name,
        "source_path": str(project_path),
        "files": files,
        "zips": zips,
    }
    return pattern


def _group_name_from_stem(stem):
    known_names = {
        "autcom": "Autcom",
        "autcomtinta": "AutcomTinta",
        "autban": "Autban",
        "libautban": "Autban",
        "auttin": "Auttin",
        "libauttin": "Auttin",
    }
    return known_names.get(stem.lower(), stem)


def _add_required_file(groups, file_name):
    if not _is_supported_file(file_name):
        return

    stem = Path(file_name).stem
    key = stem.lower()
    if key.startswith("lib"):
        key = key[3:]

    group = groups.setdefault(
        key,
        {
            "name": _group_name_from_stem(stem),
            "accepted_files": set(),
            "validate_version": key in VERSION_GROUP_KEYS,
        },
    )
    group["accepted_files"].add(Path(file_name).name)
    return group


def _expand_known_alternatives(group_name, accepted_files):
    files = set(accepted_files)
    normalized_group = group_name.lower()
    for file_name in accepted_files:
        stem = Path(file_name).stem.lower()
        if stem.startswith("lib"):
            stem = stem[3:]
        normalized_group = stem
        if stem in KNOWN_FILE_ALTERNATIVES:
            files.update(KNOWN_FILE_ALTERNATIVES[stem])

    if normalized_group in KNOWN_FILE_ALTERNATIVES:
        files.update(KNOWN_FILE_ALTERNATIVES[normalized_group])

    return tuple(
        sorted(
            file_name
            for file_name in files
            if file_name.lower() not in OPTIONAL_FILE_NAMES
        )
    )


def generate_pattern_from_projects(local_project_path=None, cloud_project_path=None):
    source_projects = []
    required_groups = {}

    for project_path in (local_project_path, cloud_project_path):
        if not project_path:
            continue
        project_path = Path(project_path)
        source_projects.append({"name": project_path.name, "path": str(project_path)})
        pattern = generate_pattern_from_project(project_path)

        for file_info in pattern["files"]:
            _add_required_file(required_groups, file_info["file"])

        for zip_info in pattern["zips"]:
            for file_name in zip_info["files"]:
                _add_required_file(required_groups, file_name)

    groups = []
    for group in sorted(required_groups.values(), key=lambda item: item["name"].lower()):
        groups.append(
            {
                "name": group["name"],
                "accepted_files": sorted(group["accepted_files"]),
                "validate_version": group["validate_version"],
            }
        )

    return {
        "source_projects": source_projects,
        "required_groups": groups,
    }


def _normalize_groups(groups):
    if not groups:
        return None

    normalized_groups = []
    for group in groups:
        accepted_files = _expand_known_alternatives(
            group.get("name", ""),
            tuple(group.get("accepted_files", ())),
        )
        if not accepted_files:
            continue
        normalized_groups.append(
            {
                "name": group.get("name") or Path(accepted_files[0]).stem,
                "accepted_files": accepted_files,
                "validate_version": bool(group.get("validate_version", True)),
            }
        )
    return tuple(normalized_groups) or None


def _read_pattern_file():
    if not PATTERN_FILE.exists():
        return None

    try:
        return json.loads(PATTERN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_required_file_groups(project_folder_name=None):
    data = _read_pattern_file()
    if not data:
        return None

    if project_folder_name:
        project_patterns = data.get("project_patterns") or {}
        project_pattern = project_patterns.get(
            pattern_scope_from_project_name(project_folder_name)
        )
        if project_pattern:
            groups = _normalize_groups(project_pattern.get("required_groups"))
            if groups:
                return groups

    return _normalize_groups(data.get("required_groups"))


def save_pattern(pattern):
    PATTERN_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = PATTERN_FILE.with_name(f".{PATTERN_FILE.name}.{uuid4().hex}.tmp")
    try:
        temp_file.write_text(
            json.dumps(pattern, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temp_file.replace(PATTERN_FILE)
    finally:
        try:
            temp_file.unlink(missing_ok=True)
        except OSError:
            pass


def generate_versioned_pattern_from_project(project):
    required_groups = {}
    expected_file = project.expected_file_version
    expected_product = project.expected_product_version

    if not expected_file or not expected_product:
        return None

    for check in project.file_checks:
        if not check.found_name or check.status != "OK":
            continue

        version_matches = (
            check.file_version == expected_file
            and check.product_version == expected_product
        )
        if check.validate_version and not version_matches:
            continue

        group = _add_required_file(required_groups, check.found_name)
        if group and version_matches:
            group["validate_version"] = True

    groups = []
    for group in sorted(required_groups.values(), key=lambda item: item["name"].lower()):
        groups.append(
            {
                "name": group["name"],
                "accepted_files": sorted(group["accepted_files"]),
                "validate_version": group["validate_version"],
            }
        )

    if not groups:
        return None

    return {
        "source_project": project.folder_name,
        "source_path": str(project.path),
        "expected_file_version": expected_file,
        "expected_product_version": expected_product,
        "required_groups": groups,
    }


def save_project_pattern(project):
    project_pattern = generate_versioned_pattern_from_project(project)
    if not project_pattern:
        return False, None

    data = _read_pattern_file() or {}
    data.setdefault("source_projects", [])
    data.setdefault("required_groups", [])
    project_patterns = data.setdefault("project_patterns", {})
    key = pattern_scope_from_project_name(project.folder_name)
    project_patterns[key] = project_pattern
    save_pattern(data)
    return True, key
