import base64
import json
import re
import time
from datetime import datetime
from urllib.parse import urlencode, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_JENKINS_TIMEOUT_SECONDS = 5


def normalize_jenkins_url(url):
    value = str(url or "").strip() or "http://localhost:8080"
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    return value.rstrip("/")


def _authorization_header(username, password):
    if not username and not password:
        return None
    token = f"{username}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(token).decode("ascii")


def _perform(url, username, password, timeout, method="GET", headers=None, ok_statuses=(200,)):
    """Chamada HTTP ao Jenkins. Retorna (ok, mensagem, corpo_texto, cabecalhos)."""
    parsed = urlsplit(url)
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if username or password:
        if parsed.scheme != "https" and parsed.hostname not in local_hosts:
            return (
                False,
                "Autenticacao Jenkins exige HTTPS para enderecos nao locais.",
                "",
                {},
            )
    request = Request(url, method=method, data=b"" if method == "POST" else None)
    authorization = _authorization_header(username, password)
    if authorization:
        request.add_header("Authorization", authorization)
    for name, value in (headers or {}).items():
        request.add_header(name, value)

    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = getattr(response, "status", response.getcode())
            body = response.read().decode("utf-8", errors="replace")
            response_headers = dict(getattr(response, "headers", None) or {})
    except HTTPError as error:
        try:
            error.close()
        except OSError:
            pass
        if error.code in (401, 403):
            return False, "Falha de autenticacao no Jenkins.", "", {}
        return False, f"Jenkins respondeu HTTP {error.code}.", "", {}
    except URLError as error:
        reason = getattr(error, "reason", error)
        return False, f"Nao foi possivel acessar o Jenkins: {reason}", "", {}
    except OSError as error:
        return False, f"Nao foi possivel acessar o Jenkins: {error}", "", {}

    if status_code not in ok_statuses:
        return False, f"Jenkins respondeu HTTP {status_code}.", "", {}
    return True, "", body, response_headers


def _request_json(url, username, password, timeout):
    ok, message, body, _headers = _perform(url, username, password, timeout)
    if not ok:
        return False, message, {}
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {}
    return True, "", payload


def rebase_url(url, base_url):
    """Mantem so o caminho de `url` e usa o endereco configurado do Jenkins.

    Os links vindos da API podem apontar para outro nome de host; as credenciais
    so podem ir para o Jenkins configurado.
    """
    base = urlsplit(normalize_jenkins_url(base_url))
    parsed = urlsplit(str(url or ""))
    path = parsed.path or "/"
    if base.path and not path.startswith(base.path):
        path = base.path.rstrip("/") + "/" + path.lstrip("/")
    return f"{base.scheme}://{base.netloc}{path}"


def test_jenkins_connection(url, username, password, timeout=None):
    normalized_url = normalize_jenkins_url(url)
    timeout = timeout or DEFAULT_JENKINS_TIMEOUT_SECONDS
    ok, message, payload = _request_json(
        f"{normalized_url}/api/json",
        username,
        password,
        timeout,
    )
    if not ok:
        return False, message

    node_name = payload.get("nodeName") or payload.get("mode") or "online"
    return True, f"Jenkins online ({node_name})."


FOLDER_DEPTH = 3
_JOB_FIELDS = (
    "name,url,color,lastBuild[number,result,building,"
    "timestamp,duration,estimatedDuration,url]"
)


def _jobs_tree(depth):
    """Parametro `tree` da API: jobs e, ate `depth` niveis, os jobs dentro de pastas."""
    inner = _JOB_FIELDS
    for _ in range(depth):
        inner = f"{_JOB_FIELDS},jobs[{inner}]"
    return f"jobs[{inner}]"


def flatten_jobs(jobs, prefix=""):
    """[(nome_completo, job)] percorrendo pastas ('Pasta/Sub/job'); pastas vazias somem."""
    found = []
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        name = f"{prefix}{job.get('name') or ''}"
        children = job.get("jobs")
        if isinstance(children, list):
            found.extend(flatten_jobs(children, f"{name}/"))
        else:
            found.append((name, job))
    return found


def get_jenkins_jobs_status(url, username, password, timeout=None):
    normalized_url = normalize_jenkins_url(url)
    timeout = timeout or DEFAULT_JENKINS_TIMEOUT_SECONDS
    query = urlencode({"tree": _jobs_tree(FOLDER_DEPTH)})
    ok, message, payload = _request_json(
        f"{normalized_url}/api/json?{query}",
        username,
        password,
        timeout,
    )
    if not ok:
        return False, message, []

    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return True, "Nenhum job encontrado no Jenkins.", []

    now_ms = int(time.time() * 1000)
    statuses = [_format_job_status(job, now_ms, name) for name, job in flatten_jobs(jobs)]
    return True, f"{len(statuses)} job(s) encontrado(s).", statuses


# Situacao -> (simbolo, rotulo em portugues, prioridade na lista).
JOB_STATES = {
    "failure": ("✖", "Falhou", 0),
    "building": ("⟳", "Em andamento", 1),
    "unstable": ("▲", "Instável", 2),
    "aborted": ("■", "Cancelado", 3),
    "success": ("●", "Sucesso", 4),
    "never": ("○", "Sem build", 5),
    "unknown": ("?", "Desconhecido", 5),
}
_RESULT_STATES = {
    "SUCCESS": "success",
    "FAILURE": "failure",
    "UNSTABLE": "unstable",
    "ABORTED": "aborted",
    "NOT_BUILT": "never",
}


def build_progress_percent(timestamp_ms, estimated_ms, now_ms):
    """Andamento estimado de um build pelo tempo medio que o Jenkins informa."""
    try:
        started, estimated, now = int(timestamp_ms), int(estimated_ms), int(now_ms)
    except (TypeError, ValueError):
        return None
    if started <= 0 or estimated <= 0:
        return None
    return max(0, min(99, int((now - started) * 100 / estimated)))


def job_label(job):
    """Texto da coluna Situacao, ex.: '✖ Falhou' ou '⟳ Em andamento 45%'."""
    symbol, label, _priority = JOB_STATES.get(job.get("state"), JOB_STATES["unknown"])
    text = f"{symbol} {label}"
    if job.get("state") == "building" and job.get("percent") is not None:
        text += f" {job['percent']}%"
    return text


def sort_jobs_for_display(jobs):
    """Falhas e builds em andamento primeiro; depois por nome."""
    return sorted(
        jobs,
        key=lambda job: (
            JOB_STATES.get(job.get("state"), JOB_STATES["unknown"])[2],
            str(job.get("name") or "").lower(),
        ),
    )


def _format_job_status(job, now_ms=None, name=None):
    name = str(name if name is not None else job.get("name") or "")
    last_build = job.get("lastBuild") if isinstance(job.get("lastBuild"), dict) else None
    if not last_build:
        return {
            "name": name,
            "job_url": str(job.get("url") or ""),
            "number": "",
            "status": "SEM BUILD",
            "state": "never",
            "percent": None,
            "building": False,
            "timestamp": "",
            "duration": "",
            "url": str(job.get("url") or ""),
        }

    building = bool(last_build.get("building"))
    result = str(last_build.get("result") or "").strip()
    percent = None
    if building:
        status = "EM ANDAMENTO"
        state = "building"
        percent = build_progress_percent(
            last_build.get("timestamp"),
            last_build.get("estimatedDuration"),
            now_ms if now_ms is not None else int(time.time() * 1000),
        )
    elif result:
        status = result
        state = _RESULT_STATES.get(result.upper(), "unknown")
    else:
        status = "DESCONHECIDO"
        state = "unknown"

    return {
        "name": name,
        "job_url": str(job.get("url") or ""),
        "number": str(last_build.get("number") or ""),
        "status": status,
        "state": state,
        "percent": percent,
        "building": building,
        "timestamp": _format_jenkins_timestamp(last_build.get("timestamp")),
        "duration": _format_duration(last_build.get("duration")),
        "url": str(last_build.get("url") or job.get("url") or ""),
    }


def _format_jenkins_timestamp(timestamp):
    try:
        value = int(timestamp)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return datetime.fromtimestamp(value / 1000).strftime("%d/%m/%Y %H:%M:%S")


def _format_duration(duration):
    try:
        value = int(duration)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    seconds = value // 1000
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def count_failed_jobs(jobs):
    return sum(1 for job in jobs if job.get("state") == "failure")


ESC = chr(27)
_HIDDEN_NOTE = re.compile(ESC + r"\[8m.*?" + ESC + r"\[0m", re.S)
_ANSI = re.compile(ESC + r"\[[0-9;?]*[A-Za-z]")
LOG_TAIL_CHARS = 200_000


def clean_console_text(text):
    """Tira as anotacoes escondidas e as cores ANSI do log do Jenkins."""
    return _ANSI.sub("", _HIDDEN_NOTE.sub("", str(text or "")))


def get_build_log(url, username, password, build_url, timeout=None):
    """Final do log do build (so o trecho do fim, onde a falha aparece).

    Retorna (ok, mensagem, texto). Usa o endereco configurado do Jenkins.
    """
    timeout = timeout or DEFAULT_JENKINS_TIMEOUT_SECONDS
    target = rebase_url(build_url, url).rstrip("/") + "/logText/progressiveText"
    ok, message, _body, headers = _perform(f"{target}?start=999999999999", username, password, timeout)
    if not ok:
        return False, message, ""
    try:
        size = int(headers.get("X-Text-Size") or headers.get("x-text-size") or 0)
    except (TypeError, ValueError):
        size = 0
    start = max(0, size - LOG_TAIL_CHARS)
    ok, message, body, _headers = _perform(f"{target}?start={start}", username, password, timeout)
    if not ok:
        return False, message, ""
    text = clean_console_text(body)
    if start > 0:
        text = "[... inicio do log omitido ...]\n" + text
    return True, "", text


def trigger_build(url, username, password, job_url, timeout=None):
    """Dispara um build do job (sem parametros). Retorna (ok, mensagem)."""
    timeout = timeout or DEFAULT_JENKINS_TIMEOUT_SECONDS
    base = normalize_jenkins_url(url)
    headers = {}
    ok, _message, body, _h = _perform(f"{base}/crumbIssuer/api/json", username, password, timeout)
    if ok:
        try:
            crumb = json.loads(body)
            headers[crumb["crumbRequestField"]] = crumb["crumb"]
        except (ValueError, KeyError, TypeError):
            headers = {}
    target = rebase_url(job_url, base).rstrip("/") + "/build"
    ok, message, _body, _h = _perform(
        target, username, password, timeout, method="POST", headers=headers, ok_statuses=(200, 201, 202)
    )
    if ok:
        return True, "Build disparado."
    if "HTTP 400" in message or "HTTP 500" in message:
        message += " O job pode exigir parametros (nao suportado aqui)."
    elif "autenticacao" in message:
        message += " O usuario precisa de permissao para executar builds."
    return False, message
