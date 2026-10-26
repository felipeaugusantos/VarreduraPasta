import sys

from app.error_handler import report_startup_error


def main():
    try:
        # Tkinter e ctypes fazem parte da biblioteca padrao do Python no Windows.
        from app.interface import VersionScannerApp

        app = VersionScannerApp()
    except Exception:
        # No exe nao ha console: sem isto o sistema simplesmente nao abre.
        report_startup_error(*sys.exc_info())
        raise SystemExit(1)
    app.mainloop()


if __name__ == "__main__":
    main()
