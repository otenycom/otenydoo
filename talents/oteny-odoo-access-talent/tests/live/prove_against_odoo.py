#!/usr/bin/env python3
"""prove_against_odoo — run scripts/odoo_call.py against a real Odoo 19 with oteny_bot.

The unit tests prove the script against a fake server. This proves it against the real
thing: a throwaway database on a local Odoo 19 with oteny_bot installed (api_doc comes
with web). It creates a bot user with a global API key, then drives the real script
through --check, the form verbs, a save Odoo refuses, the raw pipe, --doc with and
without the Technical Documentation group, and a wrong database. The database is
dropped at the end, pass or fail.

    python3 prove_against_odoo.py --python ~/odoo/venv/bin/python \\
        --odoo-bin ~/odoo/odoo19/odoo-bin --addons ~/odoo/odoo19/addons,<otenydoo>

The server runs with --db-filter on that one database, as an odoo.sh host does, so a
request without the database header still finds it and a wrong header does not.

The keys never leave memory. They are read from the setup shell's output inside this
process and handed to the script through its environment, and every check asserts that
no key appears in the script's output.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "odoo_call.py"
DB = "otenydoo_botheld_e2e"
KEY_ENV = "E2E_ODOO_KEY"

SETUP = r'''
import json
mods = env["ir.module.module"].sudo().search([("name", "in", ["oteny_bot", "api_doc", "mail"])])
states = {m.name: m.state for m in mods}
assert states == {"oteny_bot": "installed", "api_doc": "installed", "mail": "installed"}, states
print("E2E_MODULES=" + json.dumps(states))
Users = env["res.users"].sudo().with_context(no_reset_password=True)
g = lambda x: env.ref(x).id
base = [g("base.group_user"), g("base.group_partner_manager")]
bot = Users.create({"name": "E2E Bot", "login": "e2e.bot",
                    "group_ids": [(6, 0, base + [g("api_doc.group_allow_doc")])]})
bot2 = Users.create({"name": "E2E Bot No Doc", "login": "e2e.bot2",
                     "group_ids": [(6, 0, base)]})
Users.create({"name": "Fixture Staff Contact", "login": "e2e.staff",
              "group_ids": [(6, 0, [g("base.group_user")])]})
c = env["res.partner"].sudo().create({"name": "Fixture Contact"})
Key = env["res.users.apikeys"]
k1 = Key.with_user(bot).sudo()._generate(None, "e2e", None)
k2 = Key.with_user(bot2).sudo()._generate(None, "e2e", None)
env.cr.commit()
print("E2E_SETUP=" + json.dumps({"k1": k1, "k2": k2, "contact": c.id, "bot": bot.id}))
'''


class Proof:
    def __init__(self, keys: list[str]):
        self.keys = keys
        self.failed = 0

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        for key in self.keys:
            detail = detail.replace(key, "***")
        print(("PASS " if ok else "FAIL ") + label + (f" — {detail}" if detail and not ok
                                                        else ""), flush=True)
        self.failed += 0 if ok else 1


def free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", required=True)
    ap.add_argument("--odoo-bin", required=True)
    ap.add_argument("--addons", required=True)
    ap.add_argument("--port", type=int, default=8469)
    a = ap.parse_args()
    # "--addons-path=" in one token: odoo-bin finds a subcommand (shell) only after it.
    odoo = [os.path.expanduser(a.python), os.path.expanduser(a.odoo_bin),
            "--addons-path=" + ",".join(os.path.expanduser(p) for p in a.addons.split(","))]
    if not free(a.port):
        print(f"FAIL port {a.port} is busy; pass --port", flush=True)
        return 2

    subprocess.run(["dropdb", "--if-exists", DB], check=False)
    server = None
    try:
        print("step: create the database and install oteny_bot + api_doc", flush=True)
        t0 = time.time()
        r = subprocess.run(odoo + ["-d", DB, "-i", "oteny_bot,api_doc", "--without-demo=True",
                                   "--stop-after-init", "--log-level=warn",
                                   "--http-port", str(a.port)],
                           capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            print("FAIL install:\n" + "\n".join(r.stderr.splitlines()[-15:]), flush=True)
            return 1
        print(f"step: installed in {time.time() - t0:.0f}s", flush=True)

        r = subprocess.run(odoo + ["shell", "-d", DB, "--no-http", "--log-level=warn"],
                           input=SETUP, capture_output=True, text=True, timeout=300)
        line = next((ln for ln in r.stdout.splitlines() if ln.startswith("E2E_SETUP=")), "")
        if not line:
            print("FAIL setup shell:\n" + "\n".join(r.stderr.splitlines()[-15:]), flush=True)
            return 1
        mods = next((ln for ln in r.stdout.splitlines() if ln.startswith("E2E_MODULES=")), "")
        print("step: " + mods, flush=True)
        setup = json.loads(line[len("E2E_SETUP="):])
        k1, k2, contact = setup["k1"], setup["k2"], setup["contact"]
        proof = Proof([k1, k2])
        print("step: bot users and keys created (keys held in memory only)", flush=True)

        log = open(Path(tempfile.gettempdir()) / f"{DB}.log", "w")
        server = subprocess.Popen(odoo + ["-d", DB, "--db-filter", f"^{DB}$",
                                          "--http-port", str(a.port),
                                          "--max-cron-threads", "0", "--log-level=warn"],
                                  stdout=log, stderr=subprocess.STDOUT)
        deadline = time.time() + 120
        while free(a.port):
            if time.time() > deadline or server.poll() is not None:
                print("FAIL the server did not open its port within 120s", flush=True)
                return 1
            time.sleep(1)
        print(f"step: server up on 127.0.0.1:{a.port}", flush=True)

        home = Path(tempfile.mkdtemp())
        cdir = home / ".hermes/data/oteny-odoo-access-talent/connections"
        cdir.mkdir(parents=True)
        url = f"http://127.0.0.1:{a.port}"

        def conn(name, db=DB, login="e2e.bot"):
            lines = [f"url: {url}", f"login: {login}", f"key_env: {KEY_ENV}"]
            if db:
                lines.insert(1, f"db: {db}")
            (cdir / f"{name}.yaml").write_text("\n".join(lines) + "\n")

        def run(name, *args, key=k1):
            env = {"HOME": str(home), "PATH": os.environ.get("PATH", ""), KEY_ENV: key}
            p = subprocess.run([sys.executable, str(SCRIPT), "--connection", name, *args],
                               capture_output=True, text=True, env=env, timeout=120)
            leak = any(k in p.stdout + p.stderr for k in (k1, k2))
            proof.check(f"no key in the output of {name} {' '.join(args)[:60]}", not leak)
            return p

        def call(name, model, method, kwargs, key=k1):
            p = run(name, "--model", model, "--method", method, "--kwargs", json.dumps(kwargs),
                    key=key)
            try:
                return p.returncode, json.loads(p.stdout)
            except json.JSONDecodeError:
                return p.returncode, {"_raw": p.stdout}

        conn("crm")
        p = run("crm", "--check")
        proof.check("--check says READY: yes", p.stdout.startswith("READY: yes"), p.stdout)
        proof.check("--check finds the login", "login: e2e.bot found" in p.stdout, p.stdout)
        proof.check("--check finds the form host", "form host: ok" in p.stdout, p.stdout)

        rc, v = call("crm", "oteny.form.session", "views", {"res_model": "res.partner"})
        proof.check("views names the Contacts action",
                    rc == 0 and "base.action_partner_form" in json.dumps(v), json.dumps(v)[:300])

        rc, v = call("crm", "oteny.form.session", "list",
                     {"xmlid": "base.action_partner_form",
                      "domain": [["name", "=", "Fixture Contact"]], "limit": 5})
        proof.check("list finds the fixture contact",
                    rc == 0 and "Fixture Contact" in json.dumps(v), json.dumps(v)[:300])

        rc, v = call("crm", "oteny.form.session", "open",
                     {"xmlid": "base.action_partner_form", "res_id": contact})
        handle = v.get("handle") if isinstance(v, dict) else None
        proof.check("open returns a handle", rc == 0 and handle is not None, json.dumps(v)[:300])
        rc, v = call("crm", "oteny.form.session", "set",
                     {"handle": handle, "values": {"city": "Amsterdam"}})
        proof.check("set takes {handle, values}", rc == 0, json.dumps(v)[:300])
        rc, v = call("crm", "oteny.form.session", "save", {"handle": handle})
        proof.check("save succeeds", rc == 0, json.dumps(v)[:300])
        rc, v = call("crm", "res.partner", "search_read",
                     {"domain": [["id", "=", contact]], "fields": ["city"]})
        proof.check("the raw pipe reads the saved city",
                    rc == 0 and v and v[0].get("city") == "Amsterdam", json.dumps(v)[:300])

        rc, v = call("crm", "res.partner", "search_read",
                     {"domain": [["name", "=", "Fixture Staff Contact"]], "fields": ["id"]})
        staff = v[0]["id"] if rc == 0 and v else None
        rc, v = call("crm", "oteny.form.session", "open",
                     {"xmlid": "base.action_partner_form", "res_id": staff})
        h2 = v.get("handle") if isinstance(v, dict) else None
        call("crm", "oteny.form.session", "set", {"handle": h2, "values": {"city": "X"}})
        rc, v = call("crm", "oteny.form.session", "save", {"handle": h2})
        proof.check("a staff user's contact: save is refused with the access hint",
                    rc == 1 and "HTTPError 403" in v.get("error", "")
                    and v.get("hint", "").startswith("access denied"), json.dumps(v)[:400])

        rc, v = call("crm", "mail.message", "search_read", {"domain": []})
        proof.check("a chatter scan without res_id is refused",
                    rc == 1 and "res_id" in v.get("error", ""), json.dumps(v)[:300])

        rc, v = call("crm", "res.partner", "search_read", {"domain": [], "fields": ["id"]})
        proof.check("search_read with no limit returns at most 50", rc == 0 and len(v) <= 50,
                    str(len(v)) if isinstance(v, list) else json.dumps(v)[:200])

        p = run("crm", "--doc")
        try:
            idx = json.loads(p.stdout)
        except json.JSONDecodeError:
            idx = {}
        head = json.dumps(idx)
        proof.check("--doc answers (index, cut to the chat size)",
                    p.returncode == 0 and ("res.partner" in head), p.stdout[:300])
        out = home / "map.json"
        p = run("crm", "--doc", "--out", str(out))
        summary = json.loads(p.stdout) if p.stdout.strip().startswith("{") else {}
        models = [m.get("model") for m in json.loads(out.read_text()).get("models", [])] \
            if out.exists() else []
        proof.check("--doc --out writes the whole index",
                    p.returncode == 0 and summary.get("models") == len(models) > 0
                    and "res.partner" in models, p.stdout[:300])
        print(f"info: the bot's index lists {len(models)} models", flush=True)
        p = run("crm", "--doc", "res.partner")
        doc = json.loads(p.stdout) if p.returncode == 0 else {}
        parent = (doc.get("fields") or {}).get("parent_id", {})
        proof.check("--doc res.partner gives relations",
                    parent.get("relation") == "res.partner", p.stdout[:300])

        conn("crm2", login="e2e.bot2")
        p = run("crm2", "--doc", key=k2)
        v = json.loads(p.stdout) if p.stdout.strip().startswith("{") else {}
        proof.check("--doc without the group names the group, never Access Rights",
                    p.returncode == 1 and "api_doc.group_allow_doc" in v.get("hint", ""),
                    p.stdout[:400])

        conn("wrongdb", db="nosuch_db")
        p = run("wrongdb", "--check")
        proof.check("--check names a wrong database",
                    p.stdout.startswith("READY: no") and "database" in p.stdout, p.stdout)

        conn("nodb", db=None)
        p = run("nodb", "--check")
        proof.check("--check with no database on a one-database host is ready",
                    p.stdout.startswith("READY: yes"), p.stdout)

        p = run("crm", "--check", key="not-a-real-key-0000000000000000000000")
        proof.check("--check names a refused key",
                    p.stdout.startswith("READY: no") and "refused" in p.stdout, p.stdout)

        print(f"RESULT {'GREEN' if proof.failed == 0 else 'RED'}: {proof.failed} failed",
              flush=True)
        return 0 if proof.failed == 0 else 1
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                server.kill()
        subprocess.run(["dropdb", "--if-exists", DB], check=False)
        print("step: database dropped", flush=True)


if __name__ == "__main__":
    sys.exit(main())
