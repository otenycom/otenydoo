#!/usr/bin/env python3
"""odoo_call — reach an Odoo the owner set up on this bot, over /json/2/.

The bot-held lane of oteny-odoo-access-talent. Use it when the owner gave you the
address, database and login of their Odoo, and the API key arrived through the
secure connect page. When the project bound the connection for you, use
`odoo_client` instead. Setup: references/bot-held-connection.md.

    python3 odoo_call.py --connection NAME --model MODEL --method METHOD [--kwargs JSON]
    python3 odoo_call.py --connection NAME --check
    python3 odoo_call.py --connection NAME --doc [MODEL] [--out FILE]

The connection file is ~/.hermes/data/oteny-odoo-access-talent/connections/NAME.yaml,
flat `key: value` lines:

    url: https://erp.example.com
    db: example-main
    login: bot
    key_env: EXAMPLE_ODOO_KEY

It carries no secret. The key is read from the variable `key_env` names.

The call behaves as `odoo_client` does, so one set of verb instructions serves both
lanes: the same wire, the same search_read limits, the same chatter guard, the same
50,000-character cap and the same error hints. Transient errors are retried on a read
only. The key never reaches stdout, stderr or an error text, and a redirect is
refused, because urllib would forward the key to the new host.

Pure standard library. Exit 0 = answered, 1 = the call or a guard refused it,
2 = the connection or the arguments are wrong. --check always exits 0.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TALENT = "oteny-odoo-access-talent"
SETUP_REF = f"~/.hermes/skills/talents/{TALENT}/references/bot-held-connection.md"

# ----------------------------------------------------------------------------------------
# Copied from hermeshost catalog/plugins/hh-odoo-client/odoo_client_wire.py. Both lanes
# must behave the same to the model, so change these in both places at once.
# ----------------------------------------------------------------------------------------
AGENT = "Mozilla/5.0 (compatible; OtenyBot/1.0; +https://oteny.com)"
TRANSIENT_HTTP = frozenset({502, 503, 504})
RETRYABLE_READ_METHODS = frozenset({
    "search_read", "search_count", "read", "read_group", "fields_get",
    "name_search", "default_get",
})
RETRIES = 2
RETRY_BACKOFF_S = 0.5
TIMEOUT_S = 30.0
MAX_CHARS = 50_000
SEARCH_READ_DEFAULT_LIMIT = 50
SEARCH_READ_MAX_LIMIT = 100
HINTS = (
    ("404", "unknown model or method — only standard ORM methods (search_read, "
            "search_count, read, create, write) and the methods your Talent's connection "
            "documents exist; there are NO custom "
            "getters. Read a record's field with search_read + fields=[<field>]."),
    ("500", "server error — usually a wrong FIELD name in fields/domain (e.g. the state "
            "field is 'state_id', not 'state'). Fix the field names; do not invent new "
            "method names."),
    ("422", "bad arguments — kwargs must match the method signature (read takes "
            "{'ids': [...], 'fields': [...]}; search_read takes {'domain': [...], "
            "'fields': [...], 'limit': ...})."),
    ("403", "access denied — this record/field is outside your least-privilege grants; "
            "do not retry, report it."),
)

# ----------------------------------------------------------------------------------------
# The rules a connection file must meet.
# ----------------------------------------------------------------------------------------
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
KEY_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
MODEL_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
# The platform owns these names. OTENY_CONN_ODOO_KEY is the key to Oteny's own Odoo:
# pointed at a business's address, it would send Oteny's key to that server.
PLATFORM_PREFIXES = ("OTENY_", "TELEGRAM_")
SECRET_FIELDS = frozenset({"key", "api_key", "apikey", "password", "secret", "token"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

DOC_GROUP_HINT = (
    "the login is not in 'Technical Documentation' (api_doc.group_allow_doc). Ask the "
    "Odoo admin to add that one group — never 'Access Rights', which lets the login edit "
    "users and groups.")
DOC_MISSING_HINT = (
    "no /doc-bearer/ route: api_doc is not installed on that Odoo, or this model does "
    "not exist there.")


class ConfigError(Exception):
    """The connection or the arguments are wrong. Exit 2."""


class UplinkError(Exception):
    """The call failed. The message is what the model reads."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class UplinkTransient(UplinkError):
    """A gateway error or a lost connection: the request may never have run."""


# ---------------------------------------------------------------------------- the key


class Redactor:
    """Every line this script prints passes through here, so the key cannot leak."""

    def __init__(self):
        self.secret = ""

    def __call__(self, text: str) -> str:
        return text.replace(self.secret, "***") if self.secret else text


REDACT = Redactor()


def emit(obj) -> None:
    text = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    sys.stdout.write(REDACT(text) + "\n")


def warn(text: str) -> None:
    sys.stderr.write(REDACT(text) + "\n")


# ---------------------------------------------------------------------- the connection


def connections_dir() -> Path:
    return Path.home() / ".hermes" / "data" / TALENT / "connections"


def _parse_flat(text: str) -> dict[str, str]:
    """`key: value` lines, `#` comments. Pure standard library, so no YAML package."""
    out: dict[str, str] = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ConfigError(f"connection file line {n} is not 'key: value'")
        field, value = (s.strip() for s in line.split(":", 1))
        if value[:1] in ("'", '"'):
            end = value.find(value[0], 1)
            if end == -1:
                raise ConfigError(f"connection file line {n} has an unclosed quote")
            value = value[1:end]
        else:
            value = value.split(" #", 1)[0].strip()
        out[field.lower()] = value
    return out


def _check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return
    if parts.scheme == "http" and (parts.hostname or "") in LOOPBACK_HOSTS:
        return
    raise ConfigError(
        f"url must be https://, or http:// on loopback only (got {url!r})")


def load_connection(name: str) -> dict[str, str]:
    if not NAME_RE.match(name or ""):
        raise ConfigError(
            "connection must be a lower-case slug such as 'crm' or 'client-erp' "
            f"(got {name!r})")
    path = connections_dir() / f"{name}.yaml"
    if not path.is_file():
        raise ConfigError(
            f"no connection {name!r}: {path} does not exist. To set one up, follow "
            f"{SETUP_REF}. If the project bound this connection, call odoo_client.")
    conn = _parse_flat(path.read_text(encoding="utf-8"))
    held = sorted(f for f in conn if f in SECRET_FIELDS)
    if held:
        raise ConfigError(
            f"the connection file must hold no secret: remove {', '.join(held)}. "
            "The key belongs in the variable key_env names, set by the secure connect "
            "page.")
    url = conn.get("url", "")
    if not url:
        raise ConfigError("the connection file has no url")
    _check_url(url)
    key_env = conn.get("key_env", "")
    if not KEY_ENV_RE.match(key_env):
        raise ConfigError(
            f"key_env must be an UPPER_SNAKE_CASE variable name (got {key_env!r})")
    if key_env.startswith(PLATFORM_PREFIXES):
        raise ConfigError(
            f"key_env {key_env!r} is a platform name. Use a variable the secure connect "
            "page bound for this Odoo, of the form <NAME>_ODOO_KEY.")
    conn["name"] = name
    conn["url"] = url.rstrip("/")
    return conn


def load_key(conn: dict[str, str]) -> str:
    key = (os.environ.get(conn["key_env"]) or "").strip()
    if not key:
        raise ConfigError(
            f"the key variable {conn['key_env']} is not set on this bot. Raise it "
            f"through the secure connect page ({SETUP_REF}); never ask for it in chat.")
    REDACT.secret = key
    return key


# ---------------------------------------------------------------------------- the wire


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # urllib then raises HTTPError with the 3xx status


_OPENER = urllib.request.build_opener(_NoRedirect)


def _transport(url: str, data: bytes, headers: dict) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with _OPENER.open(req, timeout=TIMEOUT_S) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            where = exc.headers.get("Location", "?") if exc.headers else "?"
            raise UplinkError(
                f"HTTPError {exc.code}: the address redirected to {where}. Put the final "
                "https address in the connection file; a redirect is never followed.",
                exc.code) from None
        body = ""
        try:
            body = (exc.read() or b"").decode("utf-8", "replace")[:600]
        except Exception:  # noqa: BLE001
            body = ""
        err = UplinkTransient if exc.code in TRANSIENT_HTTP else UplinkError
        raise err(f"HTTPError {exc.code}: {body or exc.reason}", exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise UplinkTransient(f"{type(exc).__name__}: {reason}") from None


def _headers(conn: dict[str, str], key: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "User-Agent": AGENT,
        "Accept": "application/json",
    }
    if conn.get("db"):
        headers["X-Odoo-Database"] = conn["db"]
    return headers


def _post(conn, key, path: str, payload: dict, label: str, *, read: bool,
          extra_headers: dict | None = None):
    headers = _headers(conn, key)
    headers.update(extra_headers or {})
    body = json.dumps(payload).encode()
    attempt = 0
    while True:
        try:
            raw = _transport(conn["url"] + path, body, headers)
            break
        except UplinkTransient as exc:
            if read and attempt < RETRIES:
                attempt += 1
                warn(f"[odoo_call] transient error on {label} (retry {attempt}/{RETRIES}): "
                     f"{exc}")
                time.sleep(RETRY_BACKOFF_S * attempt)
                continue
            raise UplinkError(f"{label}: {exc}", exc.status) from None
        except UplinkError as exc:
            raise UplinkError(f"{label}: {exc}", exc.status) from None
    if not raw:
        return None
    parsed = json.loads(raw)
    if isinstance(parsed, dict) and parsed.get("error"):
        raise UplinkError(f"{label}: {parsed['error']}")
    return parsed


def call(conn, key, model: str, method: str, kwargs: dict):
    return _post(conn, key, f"/json/2/{model}/{method}", kwargs, f"{model}.{method}",
                 read=method in RETRYABLE_READ_METHODS)


# -------------------------------------------------------------------- the model's view


def _hint(message: str) -> str | None:
    for marker, hint in HINTS:
        if marker in message:
            return hint
    return None


def _bound_search_read(kwargs: dict) -> dict:
    out = dict(kwargs)
    try:
        lim = int(out.get("limit")) if out.get("limit") is not None else 0
    except (TypeError, ValueError):
        lim = 0
    if lim < 1:
        out["limit"] = SEARCH_READ_DEFAULT_LIMIT
    else:
        out["limit"] = min(lim, SEARCH_READ_MAX_LIMIT)
    return out


def _has_res_id(domain) -> bool:
    if not isinstance(domain, list):
        return False
    for leaf in domain:
        if isinstance(leaf, (list, tuple)) and len(leaf) == 3 and leaf[0] == "res_id" \
                and leaf[1] == "=":
            try:
                if int(leaf[2]) > 0:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _capped(result) -> dict | object:
    out = json.dumps(result, default=str)
    if len(out) <= MAX_CHARS:
        return result
    envelope = {
        "ok": True, "truncated": True,
        "note": f"result exceeded {MAX_CHARS} chars — narrow the domain/fields/limit and "
                "query again",
        "head": out[:MAX_CHARS],
    }
    if isinstance(result, dict):  # a cut form verb still carries what set/save need
        for field in ("handle", "model", "res_id"):
            if field in result:
                envelope[field] = result[field]
    return envelope


def run_call(conn, key, model: str, method: str, kwargs: dict) -> int:
    if method == "search_read":
        kwargs = _bound_search_read(kwargs)
        if model == "mail.message" and not _has_res_id(kwargs.get("domain")):
            emit({
                "ok": False,
                "error": "mail.message search_read requires a res_id '=' clause "
                         "(never scan all chatter / all records of a model)",
                "hint": "Narrow to one record's chatter, e.g. domain="
                        "[['model','=','res.partner'],['res_id','=',48]] "
                        f"with limit≤{SEARCH_READ_MAX_LIMIT}.",
            })
            return 1
    try:
        result = call(conn, key, model, method, kwargs)
    except UplinkError as exc:
        message = f"{type(exc).__name__}: {exc}".replace("UplinkTransient", "UplinkError")
        out = {"ok": False, "error": message}
        hint = _hint(message)
        if hint:
            out["hint"] = hint
        emit(out)
        return 1
    emit(_capped(result))
    return 0


# -------------------------------------------------------------------------------- --doc

# What a relation map needs from one field. Odoo's full document adds help texts, flags
# and every method's docstring, which passes 50,000 characters on a model such as
# res.partner; --out keeps all of it.
DOC_FIELD_KEYS = ("type", "string", "relation", "relation_field")
DOC_FIELD_FLAGS = ("required", "readonly")
DOC_NOTE = "a compact view; add --out <file> for Odoo's whole document, uncut"


def _compact_doc(result, model: str):
    if not isinstance(result, dict):
        return result
    if model:
        fields = {}
        for name, spec in (result.get("fields") or {}).items():
            spec = spec if isinstance(spec, dict) else {}
            slim = {k: spec[k] for k in DOC_FIELD_KEYS if spec.get(k)}
            slim.update({k: True for k in DOC_FIELD_FLAGS if spec.get(k) is True})
            fields[name] = slim
        return {"model": result.get("model", model), "name": result.get("name"),
                "fields": fields, "methods": sorted(result.get("methods") or {}),
                "note": DOC_NOTE}
    models = [{"model": m.get("model"), "name": m.get("name"),
               "fields": len(m.get("fields") or {})}
              for m in result.get("models") or [] if isinstance(m, dict)]
    return {"count": len(models), "models": models, "note": DOC_NOTE}


def run_doc(conn, key, model: str, out_path: str | None) -> int:
    if model and not MODEL_RE.match(model):
        raise ConfigError(f"--doc takes a model name such as res.partner (got {model!r})")
    path = f"/doc-bearer/{model}.json" if model else "/doc-bearer/index.json"
    try:
        # no-cache: Odoo builds the document and stores no cache file for it
        result = _post(conn, key, path, {}, path, read=True,
                       extra_headers={"Cache-Control": "no-cache"})
    except UplinkError as exc:
        out = {"ok": False, "error": f"UplinkError: {exc}"}
        if exc.status == 403:
            out["hint"] = DOC_GROUP_HINT
        elif exc.status == 404:
            out["hint"] = DOC_MISSING_HINT
        else:
            hint = _hint(out["error"])
            if hint:
                out["hint"] = hint
        emit(out)
        return 1
    if not out_path:
        emit(_capped(_compact_doc(result, model)))
        return 0
    target = Path(out_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, target)
    summary = {"ok": True, "written": str(target)}
    if isinstance(result, dict) and model:
        summary["fields"] = len(result.get("fields") or {})
    elif isinstance(result, dict):
        summary["models"] = len(result.get("models") or [])
    emit(summary)
    return 0


# ------------------------------------------------------------------------------ --check


def run_check(name: str) -> int:
    """Readiness: print READY: yes|no and the reasons. Never fails hard."""
    lines: list[str] = []
    problems: list[str] = []
    notes: list[str] = []

    def report() -> int:
        emit("READY: " + ("no" if problems else "yes"))
        for line in problems + lines + notes:
            emit(line)
        return 0

    try:
        conn = load_connection(name)
    except ConfigError as exc:
        problems.append(f"connection: {exc}")
        return report()
    lines += [f"connection: {name}", f"url: {conn['url']}",
              f"db: {conn.get('db') or '(none — the host must serve one database)'}"]
    try:
        key = load_key(conn)
    except ConfigError as exc:
        problems.append(f"key: {exc}")
        return report()
    lines.append(f"key: set ({conn['key_env']})")

    try:
        call(conn, key, "res.users", "context_get", {})
        lines.append("auth: ok")
    except UplinkError as exc:
        if exc.status == 401:
            problems.append("auth: the key was refused by that Odoo (401). Raise a new key "
                            "through the secure connect page.")
        elif exc.status == 404:
            problems.append(f"database: that address serves no database "
                            f"{conn.get('db') or ''!r} over /json/2/. Check the address "
                            "and the database name.")
        else:
            problems.append(f"auth: {exc}")
        return report()

    login = conn.get("login")
    if login:
        try:
            rows = call(conn, key, "res.users", "search_read",
                        {"domain": [["login", "=", login]],
                         "fields": ["id", "login", "active"], "limit": 1})
            if rows:
                lines.append(f"login: {login} found (id {rows[0].get('id')})")
            else:
                notes.append(f"note: login {login!r} not found. The key works; check the "
                             "login name with the owner.")
        except UplinkError as exc:
            notes.append(f"note: could not look up login {login!r} ({exc.status or exc}).")
        notes.append("note: the key authenticates, but Odoo 19 does not say which login "
                     "it belongs to.")
    try:
        call(conn, key, "oteny.form.session", "views", {"res_model": "res.partner"})
        lines.append("form host: ok")
    except UplinkError as exc:
        if exc.status == 404:
            notes.append("note: oteny_bot is not installed on that Odoo, so the form verbs "
                         "will not work. Raw reads still do.")
        else:
            notes.append(f"note: the form host did not answer ({exc.status or exc}).")
    return report()


# -------------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--connection", required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--method")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--doc", nargs="?", const="", default=None, metavar="MODEL")
    ap.add_argument("--model")
    ap.add_argument("--kwargs", default="{}")
    ap.add_argument("--out", help="with --doc: write the full document to this file")
    args = ap.parse_args(argv)

    if args.check:
        return run_check(args.connection)
    try:
        conn = load_connection(args.connection)
        key = load_key(conn)
        if args.doc is not None:
            return run_doc(conn, key, args.doc, args.out)
        if not args.model or not MODEL_RE.match(args.model):
            raise ConfigError("--method needs --model, such as res.partner")
        try:
            kwargs = json.loads(args.kwargs)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"--kwargs is not JSON: {exc}") from None
        if not isinstance(kwargs, dict):
            raise ConfigError("--kwargs must be a JSON object")
        return run_call(conn, key, args.model, args.method, kwargs)
    except ConfigError as exc:
        emit({"ok": False, "error": str(exc)})
        return 2
    except Exception as exc:  # noqa: BLE001 — a crash is a result, never a traceback
        emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
