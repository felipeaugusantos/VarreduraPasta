import ctypes
from ctypes import wintypes
from pathlib import Path


class VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [
        ("dwSignature", wintypes.DWORD),
        ("dwStrucVersion", wintypes.DWORD),
        ("dwFileVersionMS", wintypes.DWORD),
        ("dwFileVersionLS", wintypes.DWORD),
        ("dwProductVersionMS", wintypes.DWORD),
        ("dwProductVersionLS", wintypes.DWORD),
        ("dwFileFlagsMask", wintypes.DWORD),
        ("dwFileFlags", wintypes.DWORD),
        ("dwFileOS", wintypes.DWORD),
        ("dwFileType", wintypes.DWORD),
        ("dwFileSubtype", wintypes.DWORD),
        ("dwFileDateMS", wintypes.DWORD),
        ("dwFileDateLS", wintypes.DWORD),
    ]


def _format_version(ms_value, ls_value):
    return ".".join(
        str(part).zfill(2) if part < 10 else str(part)
        for part in (
            ms_value >> 16,
            ms_value & 0xFFFF,
            ls_value >> 16,
            ls_value & 0xFFFF,
        )
    )


def read_exe_versions(path):
    """Return FileVersion and ProductVersion from a Windows executable."""
    exe_path = str(Path(path))
    size = ctypes.windll.version.GetFileVersionInfoSizeW(exe_path, None)
    if not size:
        return None, None

    buffer = ctypes.create_string_buffer(size)
    ok = ctypes.windll.version.GetFileVersionInfoW(exe_path, 0, size, buffer)
    if not ok:
        return None, None

    value = ctypes.c_void_p()
    value_size = wintypes.UINT()
    ok = ctypes.windll.version.VerQueryValueW(
        buffer,
        "\\",
        ctypes.byref(value),
        ctypes.byref(value_size),
    )
    if not ok:
        return None, None

    fixed_info = ctypes.cast(value, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
    return (
        _format_version(fixed_info.dwFileVersionMS, fixed_info.dwFileVersionLS),
        _format_version(
            fixed_info.dwProductVersionMS,
            fixed_info.dwProductVersionLS,
        ),
    )
