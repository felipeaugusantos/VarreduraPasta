import ctypes
import os
from ctypes import wintypes


CREDENTIAL_TYPE_GENERIC = 1
CREDENTIAL_PERSIST_LOCAL_MACHINE = 2
JENKINS_CREDENTIAL_TARGET = "VarreduraSistema/Jenkins"


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", wintypes.LPVOID),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _credential_api():
    if os.name != "nt":
        raise OSError("Gerenciador de Credenciais disponivel apenas no Windows")
    return ctypes.WinDLL("Advapi32.dll", use_last_error=True)


def load_jenkins_password():
    api = _credential_api()
    credential_pointer = ctypes.POINTER(_Credential)()
    if not api.CredReadW(
        JENKINS_CREDENTIAL_TARGET,
        CREDENTIAL_TYPE_GENERIC,
        0,
        ctypes.byref(credential_pointer),
    ):
        error = ctypes.get_last_error()
        if error == 1168:  # ERROR_NOT_FOUND
            return ""
        raise ctypes.WinError(error)
    try:
        credential = credential_pointer.contents
        blob = ctypes.string_at(
            credential.CredentialBlob, credential.CredentialBlobSize
        )
        return blob.decode("utf-16-le")
    finally:
        api.CredFree(credential_pointer)


def save_jenkins_password(username, password):
    if not password:
        delete_jenkins_password()
        return
    api = _credential_api()
    blob = str(password).encode("utf-16-le")
    blob_buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    credential = _Credential()
    credential.Type = CREDENTIAL_TYPE_GENERIC
    credential.TargetName = JENKINS_CREDENTIAL_TARGET
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(
        blob_buffer, ctypes.POINTER(ctypes.c_ubyte)
    )
    credential.Persist = CREDENTIAL_PERSIST_LOCAL_MACHINE
    credential.UserName = str(username or "")
    if not api.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def delete_jenkins_password():
    api = _credential_api()
    if api.CredDeleteW(JENKINS_CREDENTIAL_TARGET, CREDENTIAL_TYPE_GENERIC, 0):
        return
    error = ctypes.get_last_error()
    if error != 1168:
        raise ctypes.WinError(error)
