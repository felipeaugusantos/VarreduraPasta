import ctypes
import hashlib
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


BAT_TIMEOUT_SECONDS = 10 * 60
STARTUP_TIMEOUT_SECONDS = 60
LOGIN_TIMEOUT_SECONDS = 60
WINDOW_POLL_SECONDS = 0.5
WINDOW_STABILITY_SECONDS = 5
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOGIN_USERNAME = "998"
LOGIN_PASSWORD = "998"
LOGIN_COMPANY = "001"


@dataclass(frozen=True)
class _WindowInfo:
    handle: int
    title: str
    class_name: str
    is_hung: bool


@dataclass(frozen=True)
class AutcomTestLayout:
    root: Path
    exe_directory: Path
    bat_path: Path
    autcom_path: Path


@dataclass(frozen=True)
class AutcomTestResult:
    status: str
    message: str
    bat_return_code: int | None = None
    process_id: int | None = None
    window_title: str = ""

    @property
    def ok(self):
        return self.status == "OK"


def test_folder_name(version_folder_name):
    parts = str(version_folder_name or "").strip().split(".")
    if len(parts) < 6:
        raise ValueError("Nome da versao nao possui partes suficientes.")
    branch = parts[4].strip()
    sub_branch = parts[5].strip()
    if not branch.isdigit() or not sub_branch.isdigit():
        raise ValueError("Nome da versao nao permite identificar a pasta de teste.")
    return f"PastaTesteCopia_{branch}_{sub_branch}"


def resolve_test_layout(project_path):
    project_path = Path(project_path)
    expected_name = test_folder_name(project_path.name)
    base_directory = project_path.parent
    root = base_directory / expected_name
    if base_directory.exists():
        try:
            root = next(
                (
                    item
                    for item in base_directory.iterdir()
                    if item.is_dir() and item.name.lower() == expected_name.lower()
                ),
                root,
            )
        except OSError:
            pass
    exe_directory = root / "exe"
    return AutcomTestLayout(
        root=root,
        exe_directory=exe_directory,
        bat_path=exe_directory / "CopiaArquivos.bat",
        autcom_path=root / "Autcom.exe",
    )


def validate_test_layout(project_path):
    layout = resolve_test_layout(project_path)
    missing = []
    if not layout.root.is_dir():
        missing.append(f"pasta de teste: {layout.root}")
    if not layout.exe_directory.is_dir():
        missing.append(f"pasta exe: {layout.exe_directory}")
    if not layout.bat_path.is_file():
        missing.append(f"BAT: {layout.bat_path}")
    if not layout.autcom_path.is_file():
        missing.append(f"Autcom: {layout.autcom_path}")
    return layout, missing


def run_autcom_test(
    project_path,
    progress_callback=None,
    bat_timeout=BAT_TIMEOUT_SECONDS,
    startup_timeout=STARTUP_TIMEOUT_SECONDS,
):
    project_path = Path(project_path)
    layout, missing = validate_test_layout(project_path)
    if missing:
        return AutcomTestResult(
            status="FALHA",
            message="Estrutura de teste incompleta:\n" + "\n".join(missing),
        )

    _notify(progress_callback, "Sincronizando arquivos ZIP")
    copied_files = synchronize_zip_files(project_path, layout.exe_directory)

    _notify(progress_callback, "Executando CopiaArquivos.bat")
    try:
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", str(layout.bat_path)],
            cwd=layout.exe_directory,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=bat_timeout,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return AutcomTestResult(
            status="FALHA",
            message=f"CopiaArquivos.bat excedeu {bat_timeout} segundos.",
        )

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        if len(detail) > 1000:
            detail = detail[-1000:]
        return AutcomTestResult(
            status="FALHA",
            message=(
                f"CopiaArquivos.bat retornou codigo {completed.returncode}."
                + (f"\n\n{detail}" if detail else "")
            ),
            bat_return_code=completed.returncode,
        )

    _notify(progress_callback, "Iniciando Autcom.exe")
    try:
        process = subprocess.Popen(
            [str(layout.autcom_path)],
            cwd=layout.root,
        )
    except OSError as error:
        return AutcomTestResult(
            status="FALHA",
            message=f"Nao foi possivel iniciar o Autcom.exe: {error}",
            bat_return_code=completed.returncode,
        )

    _notify(progress_callback, "Aguardando janela do Autcom")
    startup = validate_process_startup(process, startup_timeout)
    if not startup.ok:
        copied_text = f"{len(copied_files)} ZIP(s) preparado(s). "
        return AutcomTestResult(
            status=startup.status,
            message=copied_text + startup.message,
            bat_return_code=completed.returncode,
            process_id=process.pid,
            window_title=startup.window_title,
        )

    _notify(progress_callback, "Preenchendo login de teste")
    try:
        login_handle = automate_autcom_login(process)
    except OSError as error:
        return AutcomTestResult(
            status="FALHA",
            message=f"Nao foi possivel preencher o login do Autcom: {error}",
            bat_return_code=completed.returncode,
            process_id=process.pid,
            window_title=startup.window_title,
        )

    _notify(progress_callback, "Confirmando abertura da tela principal")
    login_result = validate_login_completion(
        process,
        login_handle,
        LOGIN_TIMEOUT_SECONDS,
    )
    copied_text = f"{len(copied_files)} ZIP(s) preparado(s). "
    return AutcomTestResult(
        status=login_result.status,
        message=copied_text + login_result.message,
        bat_return_code=completed.returncode,
        process_id=process.pid,
        window_title=login_result.window_title,
    )


def synchronize_zip_files(source_directory, destination_directory):
    source_directory = Path(source_directory)
    destination_directory = Path(destination_directory)
    source_files = sorted(source_directory.glob("*.zip"))
    if not source_files:
        raise OSError(f"Nenhum arquivo ZIP encontrado em {source_directory}.")

    transaction = destination_directory / f".varredura-autcom-{uuid4().hex}"
    staging = transaction / "novos"
    backup = transaction / "anteriores"
    staging.mkdir(parents=True)
    promoted = []
    backed_up = []
    remove_transaction = False
    try:
        for source_file in source_files:
            staged_file = staging / source_file.name
            shutil.copy2(source_file, staged_file)
            if _sha256(source_file) != _sha256(staged_file):
                raise OSError(f"Hash divergente ao preparar {source_file.name}.")

        backup.mkdir()
        for existing_file in sorted(destination_directory.glob("*.zip")):
            backup_file = backup / existing_file.name
            existing_file.replace(backup_file)
            backed_up.append(existing_file.name)

        for source_file in source_files:
            staged_file = staging / source_file.name
            final_file = destination_directory / source_file.name
            staged_file.replace(final_file)
            promoted.append(source_file.name)
            if _sha256(source_file) != _sha256(final_file):
                raise OSError(f"Hash divergente no destino para {source_file.name}.")
        remove_transaction = True
    except OSError as original_error:
        rollback_errors = []
        for file_name in reversed(promoted):
            try:
                (destination_directory / file_name).unlink(missing_ok=True)
            except OSError as rollback_error:
                rollback_errors.append(
                    f"remover {destination_directory / file_name}: {rollback_error}"
                )
        for file_name in reversed(backed_up):
            try:
                (backup / file_name).replace(destination_directory / file_name)
            except OSError as rollback_error:
                rollback_errors.append(
                    f"restaurar {backup / file_name}: {rollback_error}"
                )
        if rollback_errors:
            try:
                (transaction / "ROLLBACK_INCOMPLETO.txt").write_text(
                    "\n".join(rollback_errors),
                    encoding="utf-8",
                )
            except OSError:
                pass
            raise OSError(
                f"{original_error}. Rollback incompleto; arquivos preservados em "
                f"{transaction}."
            ) from original_error
        remove_transaction = True
        raise
    finally:
        if remove_transaction:
            shutil.rmtree(transaction, ignore_errors=True)
    return [destination_directory / item.name for item in source_files]


def validate_process_startup(
    process,
    timeout=STARTUP_TIMEOUT_SECONDS,
    stability_seconds=WINDOW_STABILITY_SECONDS,
):
    deadline = time.monotonic() + timeout
    responsive_since = None
    hung_since = None
    while time.monotonic() <= deadline:
        return_code = process.poll()
        if return_code is not None:
            return AutcomTestResult(
                status="FALHA",
                message=f"Autcom.exe encerrou durante a abertura (codigo {return_code}).",
                process_id=process.pid,
            )

        windows = _visible_windows_for_process(process.pid)
        if windows:
            title, is_hung = windows[0]
            if is_hung:
                responsive_since = None
                hung_since = hung_since or time.monotonic()
                if time.monotonic() - hung_since >= stability_seconds:
                    return AutcomTestResult(
                        status="FALHA",
                        message=(
                            "A janela do Autcom foi criada, mas permaneceu "
                            "sem responder."
                        ),
                        process_id=process.pid,
                        window_title=title,
                    )
            else:
                hung_since = None
                responsive_since = responsive_since or time.monotonic()
                if time.monotonic() - responsive_since >= stability_seconds:
                    return AutcomTestResult(
                        status="OK",
                        message=(
                            "Autcom abriu uma janela visivel, responsiva e "
                            f"estavel por {stability_seconds} segundos. "
                            "Feche-o normalmente depois da conferencia."
                        ),
                        process_id=process.pid,
                        window_title=title,
                    )
        else:
            responsive_since = None
            hung_since = None
        time.sleep(WINDOW_POLL_SECONDS)

    return AutcomTestResult(
        status="INCONCLUSIVO",
        message=(
            f"Autcom continua em execucao, mas nenhuma janela estavel e "
            f"responsiva foi confirmada em {timeout} segundos. "
            "Confira o processo manualmente."
        ),
        process_id=process.pid,
    )


def automate_autcom_login(
    process,
    username=LOGIN_USERNAME,
    password=LOGIN_PASSWORD,
    company=LOGIN_COMPANY,
    timeout=STARTUP_TIMEOUT_SECONDS,
):
    deadline = time.monotonic() + timeout
    login_window = None
    while time.monotonic() <= deadline:
        if process.poll() is not None:
            raise OSError("Autcom encerrou antes da tela de login.")
        windows = _window_details_for_process(process.pid)
        login_window = next(
            (
                window
                for window in windows
                if window.class_name.lower() == "tparentform"
                and not window.is_hung
            ),
            None,
        )
        if login_window:
            break
        time.sleep(WINDOW_POLL_SECONDS)
    if not login_window:
        raise OSError("Tela de login TParentForm nao foi identificada.")

    user32 = ctypes.windll.user32
    width, height, origin_x, origin_y = _client_geometry(
        user32,
        login_window.handle,
    )
    points = login_points(width, height)
    user32.ShowWindow(login_window.handle, 9)
    user32.SetForegroundWindow(login_window.handle)
    time.sleep(0.3)

    for field_name, value in (
        ("usuario", username),
        ("senha", password),
        ("empresa", company),
    ):
        x, y = points[field_name]
        _click_screen_point(user32, origin_x + x, origin_y + y)
        if user32.GetForegroundWindow() != login_window.handle:
            raise OSError("Nao foi possivel manter o foco na tela de login.")
        _replace_focused_text(user32, value)

    login_x, login_y = points["login"]
    _click_screen_point(user32, origin_x + login_x, origin_y + login_y)
    return login_window.handle


def validate_login_completion(
    process,
    login_handle,
    timeout=LOGIN_TIMEOUT_SECONDS,
    stability_seconds=WINDOW_STABILITY_SECONDS,
):
    deadline = time.monotonic() + timeout
    principal_since = None
    principal_title = ""
    while time.monotonic() <= deadline:
        return_code = process.poll()
        if return_code is not None:
            return AutcomTestResult(
                status="FALHA",
                message=(
                    "Autcom encerrou durante a autenticacao "
                    f"(codigo {return_code})."
                ),
                process_id=process.pid,
            )
        windows = _window_details_for_process(process.pid)
        login_is_visible = any(
            window.handle == login_handle
            for window in windows
        )
        principal = next(
            (
                window
                for window in windows
                if window.handle != login_handle
                and window.class_name.lower() == "tfrmprincipal"
                and not window.is_hung
            ),
            None,
        )
        if principal and not login_is_visible:
            principal_title = principal.title
            principal_since = principal_since or time.monotonic()
            if time.monotonic() - principal_since >= stability_seconds:
                return AutcomTestResult(
                    status="OK",
                    message=(
                        "Login de teste realizado e tela principal do Autcom "
                        f"confirmada por {stability_seconds} segundos. "
                        "Feche-o normalmente depois da conferencia."
                    ),
                    process_id=process.pid,
                    window_title=principal_title,
                )
        else:
            principal_since = None
        time.sleep(WINDOW_POLL_SECONDS)

    login_is_visible = any(
        window.handle == login_handle
        for window in _window_details_for_process(process.pid)
    )
    if login_is_visible:
        message = (
            "A tela de login permaneceu aberta. Confira usuario, senha, empresa "
            "ou alguma mensagem apresentada pelo Autcom."
        )
    else:
        message = (
            "A tela de login fechou, mas a tela principal responsiva nao foi "
            "confirmada."
        )
    return AutcomTestResult(
        status="FALHA",
        message=message,
        process_id=process.pid,
        window_title=principal_title,
    )


def login_points(client_width, client_height):
    return {
        "usuario": (round(client_width * 0.16), round(client_height * 0.41)),
        "senha": (round(client_width * 0.16), round(client_height * 0.55)),
        "empresa": (round(client_width * 0.16), round(client_height * 0.69)),
        "login": (round(client_width * 0.16), round(client_height * 0.875)),
    }


def _visible_windows_for_process(process_id):
    return [
        (window.title, window.is_hung)
        for window in _window_details_for_process(process_id)
    ]


def _window_details_for_process(process_id):
    user32 = ctypes.windll.user32
    windows = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def inspect_window(window_handle, _parameter):
        window_process_id = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(
            window_handle,
            ctypes.byref(window_process_id),
        )
        if window_process_id.value != process_id or not user32.IsWindowVisible(
            window_handle
        ):
            return True
        length = user32.GetWindowTextLengthW(window_handle)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(window_handle, buffer, length + 1)
        class_buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(window_handle, class_buffer, len(class_buffer))
        windows.append(
            _WindowInfo(
                handle=int(window_handle),
                title=buffer.value or "Autcom",
                class_name=class_buffer.value,
                is_hung=bool(user32.IsHungAppWindow(window_handle)),
            )
        )
        return True

    user32.EnumWindows(callback_type(inspect_window), 0)
    return windows


def _client_geometry(user32, window_handle):
    class Rect(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class Point(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    rectangle = Rect()
    if not user32.GetClientRect(window_handle, ctypes.byref(rectangle)):
        raise OSError("Nao foi possivel obter o tamanho da tela de login.")
    origin = Point(0, 0)
    if not user32.ClientToScreen(window_handle, ctypes.byref(origin)):
        raise OSError("Nao foi possivel localizar a tela de login.")
    return (
        rectangle.right - rectangle.left,
        rectangle.bottom - rectangle.top,
        origin.x,
        origin.y,
    )


def _click_screen_point(user32, x, y):
    if not user32.SetCursorPos(x, y):
        raise OSError("Nao foi possivel posicionar o cursor no campo de login.")
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.2)


def _replace_focused_text(user32, value):
    user32.keybd_event(0x11, 0, 0, 0)
    user32.keybd_event(ord("A"), 0, 0, 0)
    user32.keybd_event(ord("A"), 0, 0x0002, 0)
    user32.keybd_event(0x11, 0, 0x0002, 0)
    user32.keybd_event(0x08, 0, 0, 0)
    user32.keybd_event(0x08, 0, 0x0002, 0)
    for character in str(value):
        virtual_key = ord(character)
        user32.keybd_event(virtual_key, 0, 0, 0)
        user32.keybd_event(virtual_key, 0, 0x0002, 0)
    time.sleep(0.15)


def _sha256(file_path):
    digest = hashlib.sha256()
    with Path(file_path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _notify(callback, message):
    if callback:
        callback(message)
