from pathlib import Path


BASE_DIRECTORY = Path(r"C:\VERSOES_FECHADAS")
SCAN_INTERVAL_MS = 5 * 60 * 1000

REQUIRED_FILE_GROUPS = (
    {
        "name": "Autcom",
        "accepted_files": ("Autcom.exe",),
        "validate_version": True,
    },
    {
        "name": "AutcomTinta",
        "accepted_files": ("AutcomTinta.exe", "AutcomTinta.dll"),
        "validate_version": True,
    },
    {
        "name": "Autban",
        "accepted_files": ("libAutban.dll", "libAutban.exe", "AutBan.exe"),
        "validate_version": True,
    },
    {
        "name": "Auttin",
        "accepted_files": ("libAuttin.dll", "libAuttin.exe", "Auttin.exe"),
        "validate_version": True,
    },
)

KNOWN_FILE_ALTERNATIVES = {
    "autban": ("libAutban.dll", "libAutban.exe", "AutBan.exe"),
    "auttin": ("libAuttin.dll", "libAuttin.exe", "Auttin.exe"),
}

OPTIONAL_FILE_NAMES = {
    "libfuncoes.dll",
}

LOCAL_MAX_AUTCOM_MB = 100
CLOUD_MIN_AUTCOM_MB = 200
COPY_TARGET_DIRECTORY = Path(r"\\citel-fileserve\Modem\_VERSOES_INDIVIDUAIS")
COPY_TARGET_DIRECTORIES = (
    Path(r"\\citel-fileserve\Modem\VERSOES\379_Versoes\379.48.02\379.48.2.30"),
    COPY_TARGET_DIRECTORY,
)

IGNORED_PROJECT_FOLDERS = {
    "pastateste",
    "pastatestecopia_72",
    "pastatestecopia_74",
    "pastatestecopia_74_111",
    "pastatestecopia_74_150",
    "pastatestecopia_especificas",
    "pastatestecopia3",
}
