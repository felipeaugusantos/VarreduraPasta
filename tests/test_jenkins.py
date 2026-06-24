import unittest
from io import BytesIO
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from app.jenkins import (
    get_jenkins_jobs_status,
    normalize_jenkins_url,
    test_jenkins_connection,
)


class FakeResponse:
    status = 200

    def __init__(self, body=b'{"nodeName": "built-in"}'):
        self.body = BytesIO(body)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body.read()

    def getcode(self):
        return self.status


class JenkinsTests(unittest.TestCase):
    def test_normalize_jenkins_url_adds_scheme_and_removes_trailing_slash(self):
        self.assertEqual(
            normalize_jenkins_url("localhost:8080/"),
            "http://localhost:8080",
        )

    def test_test_connection_uses_basic_auth(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers.get("Authorization")
            captured["timeout"] = timeout
            return FakeResponse()

        with patch("app.jenkins.urlopen", fake_urlopen):
            ok, message = test_jenkins_connection(
                "localhost:8080",
                "admin",
                "fechadas",
                timeout=1,
            )

        self.assertTrue(ok)
        self.assertEqual(message, "Jenkins online (built-in).")
        self.assertEqual(captured["url"], "http://localhost:8080/api/json")
        self.assertEqual(captured["authorization"], "Basic YWRtaW46ZmVjaGFkYXM=")
        self.assertEqual(captured["timeout"], 1)

    def test_authentication_error_returns_friendly_message(self):
        def fake_urlopen(_request, timeout):
            raise HTTPError(
                "http://localhost:8080/api/json",
                403,
                "Forbidden",
                None,
                BytesIO(),
            )

        with patch("app.jenkins.urlopen", fake_urlopen):
            ok, message = test_jenkins_connection("localhost:8080", "admin", "erro")

        self.assertFalse(ok)
        self.assertEqual(message, "Falha de autenticacao no Jenkins.")

    def test_offline_jenkins_returns_friendly_message(self):
        def fake_urlopen(_request, timeout):
            raise URLError("connection refused")

        with patch("app.jenkins.urlopen", fake_urlopen):
            ok, message = test_jenkins_connection("localhost:8080", "admin", "fechadas")

        self.assertFalse(ok)
        self.assertIn("Nao foi possivel acessar o Jenkins", message)

    def test_get_jobs_status_returns_last_build_summary(self):
        captured = {}
        body = (
            b'{"jobs": ['
            b'{"name": "Local", "url": "http://localhost:8080/job/Local/", '
            b'"lastBuild": {"number": 12, "result": "SUCCESS", "building": false, '
            b'"timestamp": 1718118000000, "duration": 61000, '
            b'"url": "http://localhost:8080/job/Local/12/"}},'
            b'{"name": "Cloud", "url": "http://localhost:8080/job/Cloud/", '
            b'"lastBuild": {"number": 13, "result": null, "building": true, '
            b'"timestamp": 1718118060000, "duration": 0, '
            b'"url": "http://localhost:8080/job/Cloud/13/"}},'
            b'{"name": "SemBuild", "url": "http://localhost:8080/job/SemBuild/", '
            b'"lastBuild": null}'
            b']}'
        )

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            return FakeResponse(body)

        with patch("app.jenkins.urlopen", fake_urlopen):
            ok, message, jobs = get_jenkins_jobs_status(
                "localhost:8080",
                "admin",
                "fechadas",
                timeout=1,
            )

        self.assertTrue(ok)
        self.assertEqual(message, "3 job(s) encontrado(s).")
        self.assertIn("tree=jobs", captured["url"])
        self.assertEqual(jobs[0]["name"], "Local")
        self.assertEqual(jobs[0]["number"], "12")
        self.assertEqual(jobs[0]["status"], "SUCCESS")
        self.assertEqual(jobs[0]["duration"], "1m 01s")
        self.assertEqual(jobs[1]["status"], "EM ANDAMENTO")
        self.assertEqual(jobs[2]["status"], "SEM BUILD")

    def test_get_jobs_status_returns_authentication_error(self):
        def fake_urlopen(_request, timeout):
            raise HTTPError(
                "http://localhost:8080/api/json",
                401,
                "Unauthorized",
                None,
                BytesIO(),
            )

        with patch("app.jenkins.urlopen", fake_urlopen):
            ok, message, jobs = get_jenkins_jobs_status(
                "localhost:8080",
                "admin",
                "erro",
            )

        self.assertFalse(ok)
        self.assertEqual(message, "Falha de autenticacao no Jenkins.")
        self.assertEqual(jobs, [])


if __name__ == "__main__":
    unittest.main()
