from pathlib import Path


BASE_DIRECTORY = Path(r"C:\VERSOES_FECHADAS")
SCAN_INTERVAL_MS = 5 * 60 * 1000
CLOSING_MAX_CONCURRENT = 2
CLOSING_TIMEOUT_SECONDS = 60 * 60

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
CLOUD_MIN_AUTCOM_MB = 150
COPY_TARGET_DIRECTORY = Path(r"\\citel-fileserve\Modem\_VERSOES_INDIVIDUAIS")
COPY_TARGET_DIRECTORIES = (
    Path(r"\\citel-fileserve\Modem\VERSOES\379_Versoes\379.48.02\379.48.2.30"),
    COPY_TARGET_DIRECTORY,
)
SCRIPT_MONITOR_DIRECTORY = Path(
    r"\\citel-fileserve\Modem\WEBSERVICE_CITEL\Scripts"
)
# Pasta compartilhada (todos os operadores do TS gravam) com o registro de
# scripts ja feitos/executados. Configuravel em Configuracoes.
SCRIPT_CONTROL_DIRECTORY = Path(r"\\citel-fileserve\Modem\_CONTROLE_VARREDURA")
SCRIPT_LEDGER_FILE_NAME = "scripts_banco_modelo.log"
SCRIPT_CHECK_INTERVAL_MS = 30 * 60 * 1000
SCRIPT_RETRY_INTERVAL_MS = 15 * 60 * 1000
# Pasta oficial do exe no fileserver; o pacote traz versao.txt com o numero.
UPDATE_DIRECTORY = Path(r"\\citel-fileserve\Modem\SISTEMA_COPIA")
UPDATE_VERSION_FILE_NAME = "versao.txt"
UPDATE_LAUNCHER_FILE_NAME = "Iniciar_VarreduraSistema.bat"
UPDATE_CHECK_INTERVAL_MS = 60 * 60 * 1000
# Login de teste do Autcom (Testar Autcom); cada operador pode trocar em Configuracoes.
AUTCOM_TEST_USERNAME = "998"
AUTCOM_TEST_PASSWORD = "998"
AUTCOM_TEST_COMPANY = "001"
SVN_BASE_PATH = ""

IGNORED_PROJECT_FOLDERS = {
    "pastateste",
    "pastatestecopia_72",
    "pastatestecopia_74",
    "pastatestecopia_74_111",
    "pastatestecopia_74_150",
    "pastatestecopia_especificas",
    "pastatestecopia3",
}
