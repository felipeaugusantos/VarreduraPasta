import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parent.parent / "scripts" / "Iniciar_VarreduraSistema.bat"
FAKE_EXE = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "whoami.exe"


@unittest.skipUnless(sys.platform == "win32" and FAKE_EXE.exists(), "iniciador e do Windows")
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.share = self.temp / "rede"
        self.share.mkdir()
        self.local = self.temp / "local"
        self.local.mkdir()

    def publish(self, version):
        shutil.copy2(FAKE_EXE, self.share / "VarreduraSistema.exe")
        (self.share / "_internal").mkdir(exist_ok=True)
        (self.share / "_internal" / "dado.txt").write_text(version, encoding="ascii")
        (self.share / "versao.txt").write_text(version + "\r\n", encoding="ascii")
        shutil.copy2(LAUNCHER, self.share / LAUNCHER.name)

    def run_launcher(self):
        env = {**os.environ, "LOCALAPPDATA": str(self.local)}
        subprocess.run(
            ["cmd", "/c", str(self.share / LAUNCHER.name)],
            env=env, cwd=str(self.temp), capture_output=True, timeout=60,
        )
        time.sleep(0.5)  # o exe falso termina sozinho

    def installed(self):
        base = self.local / "VarreduraSistema" / "app"
        return sorted(p.name for p in base.iterdir()) if base.exists() else []

    def test_copies_the_package_locally_and_runs_from_there(self):
        self.publish("9.1.0")
        self.run_launcher()

        self.assertEqual(self.installed(), ["9.1.0"])
        copy = self.local / "VarreduraSistema" / "app" / "9.1.0"
        self.assertTrue((copy / "VarreduraSistema.exe").exists())
        self.assertEqual((copy / "_internal" / "dado.txt").read_text(), "9.1.0")
        self.assertFalse((copy / LAUNCHER.name).exists())

    def test_new_version_is_installed_and_old_unused_one_is_removed(self):
        self.publish("9.1.0")
        self.run_launcher()
        self.publish("9.2.0")
        self.run_launcher()

        self.assertEqual(self.installed(), ["9.2.0"])

    def test_old_version_in_use_is_kept(self):
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        self.publish("9.1.0")
        self.run_launcher()
        old_exe = self.local / "VarreduraSistema" / "app" / "9.1.0" / "VarreduraSistema.exe"
        # abre so permitindo leitura de terceiros, como um exe em execucao
        handle = kernel32.CreateFileW(str(old_exe), 0x80000000, 0x1, None, 3, 0x80, None)
        self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
        try:
            self.publish("9.2.0")
            self.run_launcher()
            self.assertEqual(self.installed(), ["9.1.0", "9.2.0"])
        finally:
            kernel32.CloseHandle(handle)

    def test_network_folder_stays_free_to_be_overwritten(self):
        self.publish("9.1.0")
        self.run_launcher()
        self.publish("9.2.0")  # sobrescreve o exe da rede: nada esta travado
        self.assertEqual((self.share / "versao.txt").read_text().strip(), "9.2.0")

    def test_without_versao_txt_it_falls_back_to_the_network_folder(self):
        self.publish("9.1.0")
        (self.share / "versao.txt").unlink()
        self.run_launcher()

        self.assertEqual(self.installed(), [])


if __name__ == "__main__":
    unittest.main()
