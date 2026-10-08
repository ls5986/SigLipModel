"""Hosted account controls must work independently of the training database."""
import http.client
import json
import threading
from urllib.parse import urlencode

import pytest
from psycopg import OperationalError
from psycopg.errors import InsufficientPrivilege, UndefinedColumn, UndefinedTable

import hosted_server
from hosted_session import account_bar


class FailingApp:
    token = "current-review-token"

    def __init__(self):
        self.calls = 0
        self.error = OperationalError("SECRET connection diagnostics must not be exposed")

    def get_studio(self):
        self.calls += 1
        raise self.error


@pytest.fixture
def hosted(monkeypatch, tmp_path):
    monkeypatch.setenv("STUDIO_PUBLIC_ORIGIN", "https://studio.example.test")
    monkeypatch.setenv("STUDIO_LOGIN_USERNAME", "owner@example.test")
    monkeypatch.setenv("STUDIO_LOGIN_PASSWORD", "correct horse battery")
    monkeypatch.setenv("STUDIO_SESSION_SECRET", "s" * 32)
    app = FailingApp()
    auth = hosted_server.HostedAuth()
    for name in ("studio_home.html", "training_studio.html", "mls_validation_ui.html",
                 "review_ui.html", "source_rows.html", "hosted_status.html"):
        (tmp_path / name).write_text('<html><head><script>window.pageScript=true</script></head><body class="page">Page</body></html>')
    monkeypatch.setattr(hosted_server, "CODE_ROOT", tmp_path)
    server = hosted_server.create_server(0, app, auth)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, *, cookie=None, origin="https://studio.example.test", token=None, body=None, host="studio.example.test"):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
        headers = {"Host": host}
        if cookie is not None:
            headers["Cookie"] = cookie
        if origin is not None:
            headers["Origin"] = origin
        if token is not None:
            headers["X-Review-Token"] = token
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    cookie = hosted_server.COOKIE + "=" + auth.issue()
    try:
        yield app, request, cookie
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_login_and_logout_do_not_require_database(hosted):
    app, request, cookie = hosted
    status, _, body = request("GET", "/login")
    assert status == 200 and b"Sign in" in body
    form = urlencode({"username": "owner@example.test", "password": "correct horse battery"})
    status, headers, _ = request("POST", "/login", body=form)
    assert status == 303
    assert "Secure" in headers["Set-Cookie"] and "HttpOnly" in headers["Set-Cookie"]
    status, headers, _ = request("POST", "/logout", cookie=cookie)
    assert status == 303 and headers["Location"] == "/login"
    assert "Max-Age=0" in headers["Set-Cookie"]
    assert "SameSite=Strict" in headers["Set-Cookie"]
    cleared = headers["Set-Cookie"].split(";", 1)[0]
    assert request("GET", "/api/session", cookie=cleared)[0] == 401
    assert request("GET", "/studio", cookie=cleared)[0] == 303
    assert app.calls == 0


def test_session_endpoint_is_authenticated_no_store_and_database_independent(hosted):
    app, request, cookie = hosted
    status, headers, body = request("GET", "/api/session", cookie=cookie)
    data = json.loads(body)
    assert status == 200 and data["authenticated"]
    assert data["username"] == "owner@example.test"
    assert data["review_token"] == app.token
    assert headers["Cache-Control"] == "no-store"
    status, _, body = request("GET", "/api/session")
    assert status == 401 and "review_token" not in json.loads(body)
    assert request("GET", "/api/session", cookie=cookie, host="evil.test")[0] == 403
    assert app.calls == 0


@pytest.mark.parametrize("path", ["/", "/studio", "/mls-validation", "/source-evidence", "/source-rows", "/status"])
def test_account_controls_exist_on_every_hosted_page(hosted, path):
    app, request, cookie = hosted
    status, headers, body = request("GET", path, cookie=cookie)
    assert status == 200
    assert b'Signed in as' in body and b'owner@example.test' in body
    assert b'action="/logout"' in body and b'>Log out</button>' in body
    assert body.index(b"const originalFetch") < body.index(b"window.pageScript")
    assert int(headers["Content-Length"]) == len(body)
    assert app.calls == 0


@pytest.mark.parametrize("origin", ["https://evil.test", None, "null"])
def test_logout_rejects_cross_site_or_unverifiable_requests(hosted, origin):
    _, request, cookie = hosted
    status, headers, _ = request("POST", "/logout", cookie=cookie, origin=origin)
    assert status == 403 and "Set-Cookie" not in headers


def test_get_logout_never_changes_session(hosted):
    _, request, cookie = hosted
    status, headers, _ = request("GET", "/logout", cookie=cookie)
    assert status == 405 and headers["Allow"] == "POST"
    assert "Set-Cookie" not in headers


def test_expired_logout_is_idempotent(hosted):
    app, request, _ = hosted
    status, headers, _ = request("POST", "/logout", cookie="acq_studio_session=expired")
    assert status == 303 and "Max-Age=0" in headers["Set-Cookie"]
    assert app.calls == 0


def test_expired_save_is_401_and_old_csrf_is_distinct_403(hosted):
    app, request, cookie = hosted
    status, _, body = request("POST", "/api/studio/review", body="{}")
    assert status == 401 and json.loads(body)["code"] == "session_expired"
    status, _, body = request("POST", "/api/studio/review", cookie=cookie, token="old", body="{}")
    assert status == 403 and json.loads(body)["code"] == "review_token_expired"
    assert app.calls == 0


@pytest.mark.parametrize("error,category", [
    (OperationalError("SECRET"), "database_connection"),
    (UndefinedTable("SECRET"), "database_schema"),
    (UndefinedColumn("SECRET"), "database_schema"),
    (InsufficientPrivilege("SECRET"), "database_permissions"),
    (OSError("SECRET"), "storage"),
])
def test_storage_error_remains_503_and_does_not_expose_credentials(hosted, error, category, caplog):
    app, request, cookie = hosted
    app.error = error
    status, _, body = request("GET", "/api/studio/review-queue", cookie=cookie)
    data = json.loads(body)
    assert status == 503 and data["code"] == "studio_unavailable"
    assert data["category"] == category
    assert "Signing in again will not repair" in data["error"]
    assert data["request_id"] in caplog.text
    assert "SECRET" not in body.decode() and "SECRET" not in caplog.text
    assert request("GET", "/api/session", cookie=cookie)[0] == 200


def test_username_is_html_escaped():
    assert b"<script>" not in account_bar("<script>alert(1)</script>")
    assert b"&lt;script&gt;" in account_bar("<script>alert(1)</script>")
