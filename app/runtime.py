import sys
from pathlib import Path


def application_directory():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(relative_path):
    if getattr(sys, "frozen", False):
        base_path = Path(getattr(sys, "_MEIPASS"))
    else:
        base_path = application_directory()
    return base_path / relative_path
