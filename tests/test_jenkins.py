import unittest
from io import BytesIO
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from app.jenkins import normalize_jenkins_url, test_jenkins_connection


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


if __name__ == "__main__":
    unittest.main()
