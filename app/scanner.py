from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

from app.config import (
    BASE_DIRECTORY,
    CLOUD_MIN_AUTCOM_MB,
    LOCAL_MAX_AUTCOM_MB,
    REQUIRED_FILE_GROUPS,
)
from app.pattern import load_required_file_groups
from app.settings import load_ignored_project_folders
from app.version_reader import read_exe_versions


_ZIP_VERSION_CACHE = {}
_ZIP_NAME_CACHE = {}


@dataclass
class FileCheck:
    group_name: str
    accepted_files: tuple[str, ...]
    validate_version: bool
    found_name: str | None
    source: str
    zip_path: Path | None
    file_version: str | None
    product_version: str | None
    size_mb: float | None
    status: str


@dataclass
class ProjectResult:
    folder_name: str
    path: Path
    expected_file_version: str
    expected_product_version: str
    autcom_size_mb: float | None
    file_version: str | None
    product_version: str | None
    display_file_version: str | None
    display_product_version: str | None
    display_autcom_size_mb: float | None
    zip_file_version: str | None
    zip_product_version: str | None
    missing_files: list[str]
    zip_status: str
    status: str
    zip_name_errors: list[str]
    local_copy_allowed: bool
    cloud_copy_allowed: bool
    file_checks: list[FileCheck]
    discovered_files: list[FileCheck]


def expected_versions_from_folder(folder_name):
    base_name = folder_name.removesuffix("_CLOUD")
    parts = base_name.split(".")
    if len(parts) < 6:
        return None, None

    normalized = [part.zfill(2) if part.isdigit() and len(part) == 1 else part for part in parts]
    file_version = ".".join(normalized[1:5])
    product_version = ".".join(normalized[2:6])
    return file_version, product_version


def _build_zip_index(project_path):
    return {
        zip_path.stem.lower(): zip_path
        for zip_path in sorted(project_path.glob("*.zip"))
    }


def _zip_name_cache_stamp(project_path):
    stamp = []
    for zip_path in sorted(Path(project_path).glob("*.zip")):
        try:
            stat = zip_path.stat()
        except OSError:
            continue
        stamp.append((zip_path.name.lower(), stat.st_mtime_ns, stat.st_size))
    return tuple(stamp)


def validate_zip_names(project_path):
    project_path = Path(project_path)
    cache_key = str(project_path).lower()
    cache_stamp = _zip_name_cache_stamp(project_path)
    cached = _ZIP_NAME_CACHE.get(cache_key)
    if cached is not None and cached[0] == cache_stamp:
        return list(cached[1])

    errors = []
    for zip_path in sorted(project_path.glob("*.zip")):
        try:
            with ZipFile(zip_path) as archive:
                supported_members = [
                    Path(info.filename).name
                    for info in archive.infolist()
                    if not info.is_dir()
                    and Path(info.filename).suffix.lower() in {".exe", ".dll", ".bpl"}
                    and not Path(info.filename).name.lower().startswith("err_")
                ]
        except BadZipFile:
            continue
        except OSError as error:
            errors.append(f"{zip_path.name}: erro ao ler ZIP ({error})")
            continue

        if len(supported_members) != 1:
            continue

        inner_name = supported_members[0]
        expected_zip_name = f"{Path(inner_name).stem}.zip"
        if zip_path.name.lower() != expected_zip_name.lower():
            errors.append(
                f"{zip_path.name} contem {inner_name}; esperado {expected_zip_name}"
            )
    _ZIP_NAME_CACHE[cache_key] = (cache_stamp, tuple(errors))
    return errors


def _discover_project_files(project_path, zip_index):
    discovered = []
    seen = set()

    for item in sorted(project_path.iterdir()):
        if not item.is_file() or item.suffix.lower() not in {".exe", ".dll"}:
            continue
        if item.name.lower().startswith("err_"):
            continue

        key = ("pasta", item.name.lower())
        seen.add(key)
        try:
            item_stat = item.stat()
        except OSError:
            continue

        discovered.append(
            FileCheck(
                group_name=Path(item.name).stem,
                accepted_files=(item.name,),
                validate_version=False,
                found_name=item.name,
                source="Pasta",
                zip_path=None,
                file_version=None,
                product_version=None,
                size_mb=item_stat.st_size / (1024 * 1024),
                status="OK",
            )
        )

    for zip_path in zip_index.values():
        try:
            with ZipFile(zip_path) as archive:
                for info in archive.infolist():
                    file_name = Path(info.filename).name
                    if Path(file_name).suffix.lower() not in {".exe", ".dll"}:
                        continue
                    if file_name.lower().startswith("err_"):
                        continue

                    key = ("zip", zip_path.name.lower(), file_name.lower())
                    if key in seen:
                        continue
                    seen.add(key)

                    discovered.append(
                        FileCheck(
                            group_name=Path(file_name).stem,
                            accepted_files=(file_name,),
                            validate_version=False,
                            found_name=file_name,
                            source="Zip",
                            zip_path=zip_path,
                            file_version=None,
                            product_version=None,
                            size_mb=info.file_size / (1024 * 1024),
                            status="OK",
                        )
                    )
        except (BadZipFile, OSError):
            continue

    return discovered


def _project_has_candidate_files(project_path):
    for item in project_path.iterdir():
        if item.is_file() and item.suffix.lower() in {".exe", ".dll", ".zip"}:
            return True
    return False


def _group_stems(group):
    return {
        Path(file_name).stem.lower().removeprefix("lib")
        for file_name in group.get("accepted_files", ())
    }


def _merge_required_core_groups(required_file_groups):
    merged_groups = list(required_file_groups)
    existing_stems = set()
    for group in merged_groups:
        existing_stems.update(_group_stems(group))

    for core_group in REQUIRED_FILE_GROUPS:
        if _group_stems(core_group).isdisjoint(existing_stems):
            merged_groups.append(core_group)
            existing_stems.update(_group_stems(core_group))

    return tuple(merged_groups)


def _find_zip_file(project_path, file_name, zip_index=None):
    stem = Path(file_name).stem.lower()
    if zip_index is not None:
        return zip_index.get(stem)

    preferred_zip = project_path / f"{Path(file_name).stem}.zip"
    if preferred_zip.exists():
        return preferred_zip

    for zip_path in sorted(project_path.glob("*.zip")):
        if zip_path.stem.lower() == stem:
            return zip_path
    return None


def _read_version_from_zip(zip_path, file_name, read_version=True):
    if not zip_path:
        return None, None, None, "Zip ausente"

    try:
        stat = zip_path.stat()
    except OSError as error:
        return None, None, None, f"Erro ao ler zip: {error}"

    cache_key = (str(zip_path).lower(), file_name.lower(), read_version)
    cache_stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _ZIP_VERSION_CACHE.get(cache_key)
    if cached is not None and cached[0] == cache_stamp:
        return cached[1]

    try:
        with ZipFile(zip_path) as archive:
            file_info = next(
                (
                    info
                    for info in archive.infolist()
                    if Path(info.filename).name.lower() == file_name.lower()
                ),
                None,
            )
            if not file_info:
                result = (None, None, None, f"{file_name} ausente no zip")
            else:
                size_mb = file_info.file_size / (1024 * 1024)
                if read_version:
                    with TemporaryDirectory() as temp_directory:
                        extracted_path = Path(temp_directory) / Path(file_info.filename).name
                        extracted_path.write_bytes(archive.read(file_info))
                        file_version, product_version = read_exe_versions(extracted_path)
                        result = (file_version, product_version, size_mb, None)
                else:
                    result = (None, None, size_mb, None)
    except BadZipFile:
        result = (None, None, None, "Zip invalido")
    except OSError as error:
        result = (None, None, None, f"Erro ao ler zip: {error}")

    _ZIP_VERSION_CACHE[cache_key] = (cache_stamp, result)
    return result


def _check_file_group(project_path, group, zip_index):
    accepted_files = tuple(group["accepted_files"])
    validate_version = bool(group.get("validate_version", True))
    for file_name in accepted_files:
        file_path = project_path / file_name
        if file_path.exists():
            try:
                file_stat = file_path.stat()
                file_version, product_version = (
                    read_exe_versions(file_path) if validate_version else (None, None)
                )
            except OSError:
                continue
            return FileCheck(
                group_name=group["name"],
                accepted_files=accepted_files,
                validate_version=validate_version,
                found_name=file_name,
                source="Pasta",
                zip_path=None,
                file_version=file_version,
                product_version=product_version,
                size_mb=file_stat.st_size / (1024 * 1024),
                status="OK",
            )

    last_zip_error = None
    for file_name in accepted_files:
        zip_path = _find_zip_file(project_path, file_name, zip_index)
        if not zip_path:
            continue
        file_version, product_version, size_mb, zip_error = _read_version_from_zip(
            zip_path,
            file_name,
            validate_version,
        )
        if zip_error:
            last_zip_error = FileCheck(
                group_name=group["name"],
                accepted_files=accepted_files,
                validate_version=validate_version,
                found_name=file_name,
                source="Zip",
                zip_path=zip_path,
                file_version=file_version,
                product_version=product_version,
                size_mb=size_mb,
                status=zip_error,
            )
            continue
        return FileCheck(
            group_name=group["name"],
            accepted_files=accepted_files,
            validate_version=validate_version,
            found_name=file_name,
            source="Zip",
            zip_path=zip_path,
            file_version=file_version,
            product_version=product_version,
            size_mb=size_mb,
            status="OK",
        )

    if last_zip_error:
        return last_zip_error

    return FileCheck(
        group_name=group["name"],
        accepted_files=accepted_files,
        validate_version=validate_version,
        found_name=None,
        source="Ausente",
        zip_path=None,
        file_version=None,
        product_version=None,
        size_mb=None,
        status="Ausente",
    )


def _version_errors(file_checks, expected_file, expected_product):
    errors = []
    for check in file_checks:
        if check.status != "OK":
            continue
        if not check.validate_version:
            continue
        if expected_file and check.file_version and check.file_version != expected_file:
            errors.append(f"{check.group_name} FileVersion incorreto")
        if expected_product and check.product_version and check.product_version != expected_product:
            errors.append(f"{check.group_name} ProductVersion incorreto")
    return errors


def _is_autcom_check(check):
    return any(Path(file_name).stem.lower() == "autcom" for file_name in check.accepted_files)


def _autcom_zip_status(autcom_check):
    if autcom_check.source == "Zip":
        if autcom_check.status == "OK":
            return "Autcom zip OK"
        return autcom_check.status
    if autcom_check.source == "Pasta":
        return "Sem autcom.zip"
    return "Autcom ausente"


def _build_status(expected_file, expected_product, file_checks, zip_name_errors=None):
    errors = []
    if expected_file is None or expected_product is None:
        errors.append("Nome da pasta fora do padrao")

    missing_files = [
        " ou ".join(check.accepted_files)
        for check in file_checks
        if check.status == "Ausente"
    ]
    if missing_files:
        errors.append(f"Ausente: {', '.join(missing_files)}")

    zip_errors = [
        f"{check.group_name}: {check.status}"
        for check in file_checks
        if check.source == "Zip" and check.status != "OK"
    ]
    errors.extend(zip_errors)
    if zip_name_errors:
        preview = "; ".join(zip_name_errors[:3])
        if len(zip_name_errors) > 3:
            preview += f"; +{len(zip_name_errors) - 3} ZIP(s)"
        errors.append(f"Nome de ZIP incorreto: {preview}")
    errors.extend(_version_errors(file_checks, expected_file, expected_product))
    return "OK" if not errors else "; ".join(errors)


def scan_projects(base_directory=BASE_DIRECTORY):
    base_path = Path(base_directory)
    if not base_path.exists():
        raise FileNotFoundError(f"Diretorio-base nao encontrado: {base_path}")
    if not base_path.is_dir():
        raise NotADirectoryError(f"Diretorio-base invalido: {base_path}")

    results = []
    ignored_project_folders = load_ignored_project_folders()
    for project_path in sorted(path for path in base_path.iterdir() if path.is_dir()):
        if project_path.name.lower() in ignored_project_folders:
            continue
        if not _project_has_candidate_files(project_path):
            continue

        required_file_groups = _merge_required_core_groups(
            load_required_file_groups(project_path.name) or REQUIRED_FILE_GROUPS
        )
        expected_file, expected_product = expected_versions_from_folder(project_path.name)
        zip_index = _build_zip_index(project_path)
        zip_name_errors = validate_zip_names(project_path)
        discovered_files = _discover_project_files(project_path, zip_index)
        file_checks = [
            _check_file_group(project_path, group, zip_index)
            for group in required_file_groups
        ]
        autcom_check = next((check for check in file_checks if _is_autcom_check(check)), None)
        if autcom_check is None:
            autcom_check = FileCheck(
                group_name="Autcom",
                accepted_files=("Autcom.exe",),
                validate_version=True,
                found_name=None,
                source="Ausente",
                zip_path=None,
                file_version=None,
                product_version=None,
                size_mb=None,
                status="Ausente",
            )
        missing_files = [
            " ou ".join(check.accepted_files)
            for check in file_checks
            if check.status == "Ausente"
        ]

        results.append(
            ProjectResult(
                folder_name=project_path.name,
                path=project_path,
                expected_file_version=expected_file or "",
                expected_product_version=expected_product or "",
                autcom_size_mb=autcom_check.size_mb if autcom_check.source == "Pasta" else None,
                file_version=autcom_check.file_version if autcom_check.source == "Pasta" else None,
                product_version=autcom_check.product_version if autcom_check.source == "Pasta" else None,
                display_file_version=autcom_check.file_version,
                display_product_version=autcom_check.product_version,
                display_autcom_size_mb=autcom_check.size_mb,
                zip_file_version=autcom_check.file_version if autcom_check.source == "Zip" else None,
                zip_product_version=autcom_check.product_version if autcom_check.source == "Zip" else None,
                missing_files=missing_files,
                zip_status=_autcom_zip_status(autcom_check),
                status=_build_status(
                    expected_file,
                    expected_product,
                    file_checks,
                    zip_name_errors,
                ),
                zip_name_errors=zip_name_errors,
                local_copy_allowed=(
                    autcom_check.size_mb is not None
                    and autcom_check.size_mb < LOCAL_MAX_AUTCOM_MB
                ),
                cloud_copy_allowed=(
                    autcom_check.size_mb is not None
                    and autcom_check.size_mb > CLOUD_MIN_AUTCOM_MB
                ),
                file_checks=file_checks,
                discovered_files=discovered_files,
            )
        )
    return results


def _read_check_versions(project, check):
    if not check.found_name:
        return check, None, None, check.size_mb, check.status
    if check.file_version and check.product_version:
        return check, check.file_version, check.product_version, check.size_mb, check.status

    if check.source == "Pasta":
        file_path = project.path / check.found_name
        if not file_path.exists():
            return check, None, None, check.size_mb, check.status
        try:
            file_version, product_version = read_exe_versions(file_path)
        except OSError:
            return check, None, None, check.size_mb, check.status
        return check, file_version, product_version, check.size_mb, check.status

    if check.source == "Zip" and check.zip_path:
        file_version, product_version, size_mb, zip_error = _read_version_from_zip(
            check.zip_path,
            check.found_name,
            read_version=True,
        )
        if zip_error:
            return check, None, None, size_mb, zip_error
        return check, file_version, product_version, size_mb, check.status

    return check, None, None, check.size_mb, check.status


def enrich_project_file_versions(project, include_discovered=False):
    checks = list(project.file_checks)
    if include_discovered:
        checks.extend(project.discovered_files)

    pending_checks = [
        check
        for check in checks
        if check.found_name and not (check.file_version and check.product_version)
    ]
    if not pending_checks:
        return

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(_read_check_versions, project, check)
            for check in pending_checks
        ]
        for future in as_completed(futures):
            check, file_version, product_version, size_mb, status = future.result()
            check.file_version = file_version
            check.product_version = product_version
            check.size_mb = size_mb
            check.status = status
