import base64
import json
from datetime import datetime
from urllib.parse import urlencode
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


def _request_json(url, username, password, timeout):
    request = Request(url)
    authorization = _authorization_header(username, password)
    if authorization:
        request.add_header("Authorization", authorization)

    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = getattr(response, "status", response.getcode())
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as error:
        try:
            error.close()
        except OSError:
            pass
        if error.code in (401, 403):
            return False, "Falha de autenticacao no Jenkins.", {}
        return False, f"Jenkins respondeu HTTP {error.code}.", {}
    except URLError as error:
        reason = getattr(error, "reason", error)
        return False, f"Nao foi possivel acessar o Jenkins: {reason}", {}
    except OSError as error:
        return False, f"Nao foi possivel acessar o Jenkins: {error}", {}

    if status_code != 200:
        return False, f"Jenkins respondeu HTTP {status_code}.", {}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {}
    return True, "", payload


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


def get_jenkins_jobs_status(url, username, password, timeout=None):
    normalized_url = normalize_jenkins_url(url)
    timeout = timeout or DEFAULT_JENKINS_TIMEOUT_SECONDS
    query = urlencode(
        {
            "tree": (
                "jobs[name,url,color,lastBuild[number,result,building,"
                "timestamp,duration,url]]"
            )
        }
    )
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

    statuses = [_format_job_status(job) for job in jobs if isinstance(job, dict)]
    return True, f"{len(statuses)} job(s) encontrado(s).", statuses


def _format_job_status(job):
    last_build = job.get("lastBuild") if isinstance(job.get("lastBuild"), dict) else None
    if not last_build:
        return {
            "name": str(job.get("name") or ""),
            "number": "",
            "status": "SEM BUILD",
            "building": False,
            "timestamp": "",
            "duration": "",
            "url": str(job.get("url") or ""),
        }

    building = bool(last_build.get("building"))
    result = str(last_build.get("result") or "").strip()
    if building:
        status = "EM ANDAMENTO"
    elif result:
        status = result
    else:
        status = "DESCONHECIDO"

    return {
        "name": str(job.get("name") or ""),
        "number": str(last_build.get("number") or ""),
        "status": status,
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
