"""Limpeza dos logs de BAT antigos (um arquivo por fechamento se acumula)."""
import time

from app.runtime import logs_directory

BAT_LOGS_FOLDER = "fechamentos_bat"
BAT_LOG_RETENTION_DAYS = 90


def remove_old_bat_logs(directory=None, retention_days=BAT_LOG_RETENTION_DAYS, now=None):
    """Apaga logs de BAT mais antigos que `retention_days`; devolve quantos removeu.

    A Auditoria mensal (`fechamentos-AAAA-MM.log`) e o `erros.log` nao sao tocados.
    """
    folder = (directory or logs_directory()) / BAT_LOGS_FOLDER
    limit = (time.time() if now is None else now) - retention_days * 86400
    removed = 0
    try:
        files = list(folder.glob("*.log"))
    except OSError:
        return 0
    for path in files:
        try:
            if path.is_file() and path.stat().st_mtime < limit:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
