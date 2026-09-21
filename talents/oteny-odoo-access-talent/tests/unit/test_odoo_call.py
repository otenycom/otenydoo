"""Unit tests for scripts/odoo_call.py, the bot-held Odoo client.

The script is a command-line tool the bot runs from its terminal, so these tests run it
the same way: as a subprocess with its own HOME and its own environment. A small fake
Odoo, built on the standard library's http.server, answers on loopback and records every
request, so the tests see the exact headers and bodies the script sends. No Odoo needed.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "odoo_call.py"
KEY = "k3y-VALUE-that-must-never-print-0123456789"
KEY_ENV = "ACME_ODOO_KEY"
# odoo_client's agent, verbatim. A business's firewall may already allow it.
AGENT = "Mozilla/5.0 (compatible; OtenyBot/1.0; +https://oteny.com)"


class FakeOdoo:
    """Records requests; answers from a per-path queue, else from a default."""

    def __init__(self):
        self.requests: list[dict] = []
        self.queues: dict[str, list[tuple[int, object]]] = {}
        self.default: tuple[int, object] = (200, {"ok": "default"})
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # keep pytest output clean
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                outer.requests.append({
                    "path": self.path, "headers": dict(self.headers),
                    "body": json.loads(raw) if raw else None})
                queue = outer.queues.get(self.path)
                status, payload = queue.pop(0) if queue else outer.default
                if payload == "REDIRECT":  # the key must never follow a redirect
                    self.send_response(302)
                    self.send_header("Location", "http://elsewhere.example/steal")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if payload == "ECHO_AUTH":  # a hostile server echoes the credential back
                    payload = {"name": "odoo.exceptions.AccessError",
                               "message": "denied for " + self.headers.get("Authorization", "")}
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, path: str, *responses: tuple[int, object]):
        self.queues.setdefault(path, []).extend(responses)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def odoo():
    fake = FakeOdoo()
    yield fake
    fake.close()


@pytest.fixture
def home(tmp_path):
    return tmp_path


def write_conn(home: Path, name: str, text: str) -> Path:
    d = home / ".hermes" / "data" / "oteny-odoo-access-talent" / "connections"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.yaml"
    p.write_text(text)
    return p


def std_conn(home: Path, url: str, *, db: str | None = "acme-main", extra: str = "") -> None:
    lines = [f"url: {url}"]
    if db:
        lines.append(f"db: {db}")
    lines += ["login: bot", f"key_env: {KEY_ENV}   # bound by the connect page"]
    write_conn(home, "acme", "# owner-supplied\n" + "\n".join(lines) + "\n" + extra)


def run(home: Path, *args: str, key: str | None = KEY, timeout: float = 30):
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "")}
    if key is not None:
        env[KEY_ENV] = key
    p = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                       text=True, env=env, timeout=timeout)
    return p


def call(home, method, kwargs=None, model="res.partner", conn="acme"):
    args = ["--connection", conn, "--model", model, "--method", method]
    if kwargs is not None:
        args += ["--kwargs", json.dumps(kwargs)]
    return run(home, *args)


def out_json(p) -> object:
    return json.loads(p.stdout)


def assert_no_key(p):
    assert KEY not in p.stdout
    assert KEY not in p.stderr


# --------------------------------------------------------------------------- the wire


def test_sends_bearer_agent_and_database_headers(odoo, home):
    std_conn(home, odoo.url)
    odoo.answer("/json/2/res.partner/search_count", (200, 7))
    p = call(home, "search_count", {"domain": []})
    assert p.returncode == 0, p.stderr
    assert out_json(p) == 7
    req = odoo.requests[-1]
    assert req["path"] == "/json/2/res.partner/search_count"
    assert req["headers"]["Authorization"] == f"Bearer {KEY}"
    assert req["headers"]["User-Agent"] == AGENT
    assert req["headers"]["X-Odoo-Database"] == "acme-main"
    assert req["headers"]["Content-Type"] == "application/json"
    assert req["body"] == {"domain": []}
    assert_no_key(p)


def test_no_database_header_when_the_file_names_none(odoo, home):
    std_conn(home, odoo.url, db=None)
    odoo.answer("/json/2/res.partner/search_count", (200, 1))
    p = call(home, "search_count", {"domain": []})
    assert p.returncode == 0, p.stderr
    assert "X-Odoo-Database" not in odoo.requests[-1]["headers"]


def test_form_verb_kwargs_pass_through_unchanged(odoo, home):
    std_conn(home, odoo.url)
    odoo.answer("/json/2/oteny.form.session/set",
                (200, {"handle": 1, "model": "res.partner", "res_id": 19, "fields": []}))
    p = call(home, "set", {"handle": 1, "values": {"city": "Amsterdam"}},
             model="oteny.form.session")
    assert p.returncode == 0, p.stderr
    assert odoo.requests[-1]["body"] == {"handle": 1, "values": {"city": "Amsterdam"}}
    assert out_json(p)["res_id"] == 19


# --------------------------------------------------------------------------- the key


def test_key_never_printed_when_the_server_echoes_it(odoo, home):
    std_conn(home, odoo.url)
    odoo.answer("/json/2/res.partner/write", (403, "ECHO_AUTH"))
    p = call(home, "write", {"ids": [1], "vals": {"city": "x"}})
    assert p.returncode == 1
    assert_no_key(p)
    assert "***" in out_json(p)["error"]


def test_key_never_printed_on_a_transport_error(home):
    std_conn(home, "http://127.0.0.1:9")  # the discard port: nothing listens
    p = call(home, "search_count", {"domain": []})
    assert p.returncode == 1
    assert_no_key(p)
    assert out_json(p)["ok"] is False


def test_missing_key_variable_is_a_clear_error(odoo, home):
    std_conn(home, odoo.url)
    p = run(home, "--connection", "acme", "--model", "res.partner",
            "--method", "search_count", key=None)
    assert p.returncode == 2
    assert KEY_ENV in out_json(p)["error"]
    assert odoo.requests == []


# --------------------------------------------------------------------------- the rules


@pytest.mark.parametrize("key_env", ["OTENY_CONN_ODOO_KEY", "TELEGRAM_BOT_TOKEN",
                                     "OTENY_ROUTER_KEY"])
def test_platform_key_names_are_refused(odoo, home, key_env):
    write_conn(home, "acme", f"url: {odoo.url}\ndb: x\nkey_env: {key_env}\n")
    p = call(home, "search_count", {"domain": []})
    assert p.returncode == 2
    assert "key_env" in out_json(p)["error"]
    assert odoo.requests == []


@pytest.mark.parametrize("field", ["key", "api_key", "password", "secret", "token"])
def test_a_file_that_holds_a_secret_is_refused(odoo, home, field):
    write_conn(home, "acme", f"url: {odoo.url}\nkey_env: {KEY_ENV}\n{field}: abc123\n")
    p = call(home, "search_count", {"domain": []})
    assert p.returncode == 2
    assert "secret" in out_json(p)["error"]
    assert "abc123" not in p.stdout + p.stderr
    assert odoo.requests == []


@pytest.mark.parametrize("url", ["http://erp.example.com", "ftp://127.0.0.1",
                                 "http://10.0.0.5:8069", "erp.example.com"])
def test_only_https_or_loopback_http_is_allowed(home, url):
    write_conn(home, "acme", f"url: {url}\nkey_env: {KEY_ENV}\n")
    p = call(home, "search_count", {"domain": []})
    assert p.returncode == 2
    assert "url" in out_json(p)["error"]


@pytest.mark.parametrize("name", ["../x", "a/b", "", "Acme", "x" * 70, ".hidden"])
def test_connection_name_is_a_plain_slug(home, name):
    p = run(home, "--connection", name, "--model", "res.partner", "--method", "search_count")
    assert p.returncode == 2
    assert "connection" in out_json(p)["error"]


def test_an_unknown_connection_names_the_setup_reference(home):
    p = call(home, "search_count", {"domain": []}, conn="nosuch")
    assert p.returncode == 2
    assert "bot-held-connection" in out_json(p)["error"]


# --------------------------------------------------------------------------- retries


def test_transient_error_on_a_read_is_retried(odoo, home):
    std_conn(home, odoo.url)
    odoo.answer("/json/2/res.partner/search_read",
                (503, {"e": 1}), (503, {"e": 2}), (200, [{"id": 1}]))
    p = call(home, "search_read", {"domain": [], "fields": ["id"]})
    assert p.returncode == 0, p.stdout
    assert out_json(p) == [{"id": 1}]
    assert len(odoo.requests) == 3


def test_transient_error_on_a_write_is_never_retried(odoo, home):
    std_conn(home, odoo.url)
    odoo.answer("/json/2/oteny.form.session/save", (503, {"e": 1}), (200, {"res_id": 5}))
    p = call(home, "save", {"handle": 1}, model="oteny.form.session")
    assert p.returncode == 1
    assert len(odoo.requests) == 1


# --------------------------------------------------------------------------- the clamps


def test_search_read_limit_defaults_to_50_and_caps_at_100(odoo, home):
    std_conn(home, odoo.url)
    odoo.answer("/json/2/res.partner/search_read", (200, []), (200, []), (200, []))
    call(home, "search_read", {"domain": []})
    call(home, "search_read", {"domain": [], "limit": 500})
    call(home, "search_read", {"domain": [], "limit": 7})
    assert [r["body"]["limit"] for r in odoo.requests] == [50, 100, 7]


def test_chatter_scan_without_res_id_is_refused_before_the_call(odoo, home):
    std_conn(home, odoo.url)
    p = call(home, "search_read", {"domain": [["body", "ilike", "vessel"]]},
             model="mail.message")
    assert p.returncode == 1
    assert "res_id" in out_json(p)["error"]
    assert odoo.requests == []


def test_chatter_read_of_one_record_is_allowed(odoo, home):
    std_conn(home, odoo.url)
    odoo.answer("/json/2/mail.message/search_read", (200, []))
    p = call(home, "search_read",
             {"domain": [["model", "=", "res.partner"], ["res_id", "=", 48]]},
             model="mail.message")
    assert p.returncode == 0, p.stdout
    assert len(odoo.requests) == 1


# --------------------------------------------------------------------------- the cap


def test_a_cut_form_answer_keeps_handle_model_and_res_id(odoo, home):
    std_conn(home, odoo.url)
    big = {"handle": 4, "model": "res.partner", "res_id": 19,
           "fields": [{"name": f"f{i}", "help": "x" * 400} for i in range(300)]}
    odoo.answer("/json/2/oteny.form.session/open", (200, big))
    p = call(home, "open", {"xmlid": "base.action_partner_form", "res_id": 19},
             model="oteny.form.session")
    assert p.returncode == 0
    assert len(p.stdout) < 60_000
    got = out_json(p)
    assert got["truncated"] is True
    assert (got["handle"], got["model"], got["res_id"]) == (4, "res.partner", 19)
    assert got["note"].startswith("result exceeded 50000 chars")


# --------------------------------------------------------------------------- the hints


@pytest.mark.parametrize("status,starts", [
    (403, "access denied — this record/field is outside your least-privilege grants"),
    (404, "unknown model or method"),
    (422, "bad arguments — kwargs must match the method signature"),
    (500, "server error — usually a wrong FIELD name"),
])
def test_errors_carry_the_odoo_client_hints(odoo, home, status, starts):
    std_conn(home, odoo.url)
    odoo.answer("/json/2/res.partner/write", (status, {"message": "nope"}))
    p = call(home, "write", {"ids": [1], "vals": {}})
    assert p.returncode == 1
    got = out_json(p)
    assert got["ok"] is False
    assert got["error"].startswith(f"UplinkError: res.partner.write: HTTPError {status}")
    assert got["hint"].startswith(starts)


def _module():
    spec = importlib.util.spec_from_file_location("odoo_call_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hint_texts_match_odoo_client_verbatim():
    """The texts are copied from hermeshost's odoo_client_wire.py. Both lanes must read
    the same to the model, so a change here is deliberate and made in both places."""
    hints = dict(_module().HINTS)
    assert hints["403"] == ("access denied — this record/field is outside your "
                            "least-privilege grants; do not retry, report it.")
    assert hints["422"] == ("bad arguments — kwargs must match the method signature (read "
                            "takes {'ids': [...], 'fields': [...]}; search_read takes "
                            "{'domain': [...], 'fields': [...], 'limit': ...}).")
    assert hints["404"].startswith("unknown model or method — only standard ORM methods")
    assert hints["500"].startswith("server error — usually a wrong FIELD name")


def test_a_redirect_is_refused_so_the_key_never_follows_it(odoo, home):
    std_conn(home, odoo.url)
    odoo.answer("/json/2/res.partner/search_count", (302, "REDIRECT"))
    p = call(home, "search_count", {"domain": []})
    assert p.returncode == 1
    assert "redirect" in out_json(p)["error"]
    assert len(odoo.requests) == 1
    assert_no_key(p)


# --------------------------------------------------------------------------- --check


def _ready_server(odoo):
    odoo.answer("/json/2/res.users/context_get", (200, {"lang": "en_US", "tz": "UTC"}))
    odoo.answer("/json/2/res.users/search_read", (200, [{"id": 33, "login": "bot",
                                                        "active": True}]))
    odoo.answer("/json/2/oteny.form.session/views", (200, {"actions": [], "views": []}))


def test_check_ready(odoo, home):
    std_conn(home, odoo.url)
    _ready_server(odoo)
    p = run(home, "--connection", "acme", "--check")
    assert p.returncode == 0
    assert p.stdout.startswith("READY: yes"), p.stdout
    assert "login: bot found (id 33)" in p.stdout
    assert_no_key(p)


def test_check_not_ready_without_key_still_exits_zero(odoo, home):
    std_conn(home, odoo.url)
    p = run(home, "--connection", "acme", "--check", key=None)
    assert p.returncode == 0
    assert p.stdout.startswith("READY: no"), p.stdout
    assert KEY_ENV in p.stdout
    assert odoo.requests == []


def test_check_names_a_wrong_database_separately(odoo, home):
    std_conn(home, odoo.url, db="wrong-db")
    odoo.answer("/json/2/res.users/context_get", (404, {"message": "Not Found"}))
    p = run(home, "--connection", "acme", "--check")
    assert p.returncode == 0
    assert p.stdout.startswith("READY: no")
    assert "database" in p.stdout and "wrong-db" in p.stdout


def test_check_names_a_rejected_key(odoo, home):
    std_conn(home, odoo.url)
    odoo.answer("/json/2/res.users/context_get", (401, {"message": "Invalid apikey"}))
    p = run(home, "--connection", "acme", "--check")
    assert p.returncode == 0
    assert p.stdout.startswith("READY: no")
    assert "key was refused" in p.stdout


def test_check_warns_when_the_form_host_is_missing(odoo, home):
    std_conn(home, odoo.url)
    odoo.answer("/json/2/res.users/context_get", (200, {"lang": "en_US"}))
    odoo.answer("/json/2/res.users/search_read", (200, [{"id": 3, "login": "bot",
                                                        "active": True}]))
    odoo.answer("/json/2/oteny.form.session/views", (404, {"message": "model missing"}))
    p = run(home, "--connection", "acme", "--check")
    assert p.stdout.startswith("READY: yes"), p.stdout
    assert "oteny_bot" in p.stdout


def test_check_refuses_a_bad_file_without_calling(odoo, home):
    write_conn(home, "acme", f"url: {odoo.url}\nkey_env: OTENY_CONN_ODOO_KEY\n")
    p = run(home, "--connection", "acme", "--check")
    assert p.returncode == 0
    assert p.stdout.startswith("READY: no")
    assert odoo.requests == []


# --------------------------------------------------------------------------- --doc


def test_doc_index_and_model_paths(odoo, home):
    std_conn(home, odoo.url)
    odoo.answer("/doc-bearer/index.json", (200, {"modules": [], "models": [
        {"model": "res.partner", "name": "Contact", "fields": {}, "methods": []}]}))
    odoo.answer("/doc-bearer/res.partner.json", (200, {"model": "res.partner",
                                                       "fields": {"parent_id": {
                                                           "relation": "res.partner"}}}))
    p1 = run(home, "--connection", "acme", "--doc")
    p2 = run(home, "--connection", "acme", "--doc", "res.partner")
    assert p1.returncode == 0 and p2.returncode == 0, p1.stdout + p2.stdout
    assert out_json(p1)["models"][0]["model"] == "res.partner"
    assert out_json(p2)["fields"]["parent_id"]["relation"] == "res.partner"
    for r in odoo.requests:
        assert r["headers"]["Cache-Control"] == "no-cache"  # no cache file written in Odoo
        assert r["headers"]["Authorization"] == f"Bearer {KEY}"
    assert_no_key(p1)


def test_doc_out_writes_the_full_document_and_prints_a_summary(odoo, home):
    std_conn(home, odoo.url)
    models = [{"model": f"m.{i}", "name": "x" * 300, "fields": {}, "methods": []}
              for i in range(400)]
    odoo.answer("/doc-bearer/index.json", (200, {"modules": ["base"], "models": models}))
    target = home / "map.json"
    p = run(home, "--connection", "acme", "--doc", "--out", str(target))
    assert p.returncode == 0, p.stdout
    assert json.loads(target.read_text())["models"] == models  # nothing cut
    assert out_json(p) == {"ok": True, "written": str(target), "models": 400}


def test_doc_without_the_group_names_the_group_not_access_rights(odoo, home):
    std_conn(home, odoo.url)
    odoo.answer("/doc-bearer/index.json", (403, {
        "name": "odoo.exceptions.AccessError",
        "message": "This page is only accessible to Technical Documentation users."}))
    p = run(home, "--connection", "acme", "--doc")
    assert p.returncode == 1
    hint = out_json(p)["hint"]
    assert "api_doc.group_allow_doc" in hint and "Technical Documentation" in hint
    assert "never" in hint and "Access Rights" in hint


def test_doc_on_an_odoo_without_api_doc(odoo, home):
    std_conn(home, odoo.url)
    odoo.answer("/doc-bearer/index.json", (404, {"message": "Not Found"}))
    p = run(home, "--connection", "acme", "--doc")
    assert p.returncode == 1
    assert "api_doc" in out_json(p)["hint"]
