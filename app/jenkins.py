import base64
import json
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


def test_jenkins_connection(url, username, password, timeout=None):
    normalized_url = normalize_jenkins_url(url)
    request = Request(f"{normalized_url}/api/json")
    authorization = _authorization_header(username, password)
    if authorization:
        request.add_header("Authorization", authorization)

    timeout = timeout or DEFAULT_JENKINS_TIMEOUT_SECONDS
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
            return False, "Falha de autenticacao no Jenkins."
        return False, f"Jenkins respondeu HTTP {error.code}."
    except URLError as error:
        reason = getattr(error, "reason", error)
        return False, f"Nao foi possivel acessar o Jenkins: {reason}"
    except OSError as error:
        return False, f"Nao foi possivel acessar o Jenkins: {error}"

    if status_code != 200:
        return False, f"Jenkins respondeu HTTP {status_code}."

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {}

    node_name = payload.get("nodeName") or payload.get("mode") or "online"
    return True, f"Jenkins online ({node_name})."
