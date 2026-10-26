import json
import unittest
from io import BytesIO
from urllib.error import HTTPError
from unittest.mock import patch

from app.jenkins import (
    LOG_TAIL_CHARS,
    clean_console_text,
    count_failed_jobs,
    flatten_jobs,
    get_build_log,
    get_jenkins_jobs_status,
    rebase_url,
    trigger_build,
)

ESC = chr(27)


class FakeResponse:
    def __init__(self, body=b"{}", status=200, headers=None):
        self.status = status
        self.headers = headers or {}
        self._body = BytesIO(body)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body.read()

    def getcode(self):
        return self.status


def job(name, result="SUCCESS", url=None):
    return {
        "name": name,
        "url": url or f"http://localhost:8080/job/{name}/",
        "lastBuild": {
            "number": 7,
            "result": result,
            "building": False,
            "timestamp": 1_700_000_000_000,
            "duration": 5000,
            "url": f"http://localhost:8080/job/{name}/7/",
        },
    }


class FolderTests(unittest.TestCase):
    def test_jobs_inside_folders_get_the_full_name(self):
        tree = [
            job("raiz"),
            {"name": "Pasta", "jobs": [job("a"), {"name": "Sub", "jobs": [job("b")]}]},
            {"name": "Vazia", "jobs": []},
        ]

        names = [name for name, _job in flatten_jobs(tree)]

        self.assertEqual(names, ["raiz", "Pasta/a", "Pasta/Sub/b"])

    def test_status_lists_folder_jobs_with_their_own_urls(self):
        payload = {"jobs": [{"name": "Pasta", "jobs": [job("build", "FAILURE")]}]}
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            return FakeResponse(json.dumps(payload).encode())

        with patch("app.jenkins.urlopen", fake_urlopen):
            ok, _message, jobs = get_jenkins_jobs_status("localhost:8080", "", "")

        self.assertTrue(ok)
        self.assertEqual(jobs[0]["name"], "Pasta/build")
        self.assertEqual(jobs[0]["job_url"], "http://localhost:8080/job/build/")
        self.assertEqual(jobs[0]["state"], "failure")
        self.assertIn("jobs%5Bname", captured["url"])  # pede os jobs de dentro das pastas
        self.assertEqual(count_failed_jobs(jobs), 1)


class LogTextTests(unittest.TestCase):
    def test_hidden_notes_and_colors_are_removed(self):
        raw = f"a{ESC}[8mha:AAAA==ENDnote{ESC}[0mb{ESC}[31mc{ESC}[0m"
        self.assertEqual(clean_console_text(raw), "abc")

    def test_rebase_url_keeps_only_the_path_on_the_configured_host(self):
        self.assertEqual(
            rebase_url("http://outro-host:9999/job/x/3/", "localhost:8080"),
            "http://localhost:8080/job/x/3/",
        )
        self.assertEqual(
            rebase_url("http://x/jenkins/job/a/1/", "http://h:8080/jenkins"),
            "http://h:8080/jenkins/job/a/1/",
        )
        self.assertEqual(
            rebase_url("http://x/job/a/1/", "http://h:8080/jenkins"),
            "http://h:8080/jenkins/job/a/1/",
        )


class BuildLogTests(unittest.TestCase):
    def test_reads_only_the_tail_of_a_big_log(self):
        calls = []
        total = LOG_TAIL_CHARS + 5000

        def fake_urlopen(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                return FakeResponse(b"", headers={"X-Text-Size": str(total)})
            return FakeResponse(b"ERRO FINAL")

        with patch("app.jenkins.urlopen", fake_urlopen):
            ok, _message, text = get_build_log(
                "http://localhost:8080", "u", "p", "http://outro:1/job/x/3/"
            )

        self.assertTrue(ok)
        self.assertTrue(calls[0].startswith("http://localhost:8080/job/x/3/logText/progressiveText"))
        self.assertTrue(calls[1].endswith("start=5000"))
        self.assertIn("ERRO FINAL", text)
        self.assertIn("omitido", text)

    def test_small_log_is_returned_whole(self):
        responses = iter([FakeResponse(b"", headers={"X-Text-Size": "10"}), FakeResponse(b"log inteiro")])
        with patch("app.jenkins.urlopen", lambda request, timeout: next(responses)):
            ok, _message, text = get_build_log("localhost:8080", "", "", "http://localhost:8080/job/x/1/")

        self.assertEqual((ok, text), (True, "log inteiro"))

    def test_error_is_reported(self):
        def fake_urlopen(request, timeout):
            raise HTTPError(request.full_url, 404, "x", {}, None)

        with patch("app.jenkins.urlopen", fake_urlopen):
            ok, message, text = get_build_log("localhost:8080", "", "", "http://localhost:8080/job/x/1/")

        self.assertFalse(ok)
        self.assertIn("HTTP 404", message)
        self.assertEqual(text, "")


class TriggerBuildTests(unittest.TestCase):
    def test_posts_to_the_job_build_endpoint_with_the_crumb(self):
        seen = []

        def fake_urlopen(request, timeout):
            seen.append((request.get_method(), request.full_url, dict(request.header_items())))
            if request.full_url.endswith("/crumbIssuer/api/json"):
                return FakeResponse(json.dumps({"crumbRequestField": "Jenkins-Crumb", "crumb": "abc"}).encode())
            return FakeResponse(b"", status=201)

        with patch("app.jenkins.urlopen", fake_urlopen):
            ok, message = trigger_build("localhost:8080", "u", "p", "http://outro:1/job/x/")

        self.assertEqual((ok, message), (True, "Build disparado."))
        method, url, headers = seen[-1]
        self.assertEqual((method, url), ("POST", "http://localhost:8080/job/x/build"))
        self.assertEqual(headers.get("Jenkins-crumb"), "abc")

    def test_works_when_the_server_has_no_crumb_issuer(self):
        def fake_urlopen(request, timeout):
            if request.full_url.endswith("/crumbIssuer/api/json"):
                raise HTTPError(request.full_url, 404, "x", {}, None)
            return FakeResponse(b"", status=201)

        with patch("app.jenkins.urlopen", fake_urlopen):
            ok, _message = trigger_build("localhost:8080", "", "", "http://localhost:8080/job/x/")

        self.assertTrue(ok)

    def test_parameterized_job_and_permission_errors_are_explained(self):
        def server_error(request, timeout):
            raise HTTPError(request.full_url, 500, "x", {}, None)

        def forbidden(request, timeout):
            raise HTTPError(request.full_url, 403, "x", {}, None)

        with patch("app.jenkins.urlopen", server_error):
            ok, message = trigger_build("localhost:8080", "", "", "http://localhost:8080/job/x/")
        self.assertFalse(ok)
        self.assertIn("parametros", message)

        with patch("app.jenkins.urlopen", forbidden):
            ok, message = trigger_build("localhost:8080", "", "", "http://localhost:8080/job/x/")
        self.assertFalse(ok)
        self.assertIn("permissao", message)


if __name__ == "__main__":
    unittest.main()
