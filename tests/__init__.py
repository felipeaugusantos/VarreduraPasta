import atexit
import os
import shutil
import tempfile

# Isola os testes dos dados reais do operador (settings, Auditoria, memoria de
# ZIPs etc. em %LOCALAPPDATA%\VarreduraSistema). Precisa rodar antes de qualquer
# import de `app`, pois os caminhos sao resolvidos no import.
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="varredura-testes-")
os.environ["VARREDURA_SISTEMA_DATA_DIR"] = _TEST_DATA_DIR
atexit.register(shutil.rmtree, _TEST_DATA_DIR, ignore_errors=True)
