#!/usr/bin/env python3
"""moses-publish, end to end through the REAL site server — no Cloudflare, no systemd.

    python3 agent/moses_publish_test.py

A scratch project with a Knight target and a tracker entry; a stand-in `systemctl` that starts a tiny
app on the port the unit names; and site's own serve.py on a local port as "the public
address" (SITE_SERVE, default ~/Projects/site/tools/serve.py). If that serve.py does
not know about published apps yet, the end-to-end checks say SKIP rather than pass — a check that could
not run is never reported as one that did. Then each guard is removed and its check must go red.
"""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "moses-publish"
SERVE = Path(os.environ.get("SITE_SERVE", str(Path.home() / "Projects" / "site" / "tools" / "serve.py")))
SHAPE = HERE.parent / "knight" / "lib" / "target-shape.py"

FAKE_SYSTEMCTL = r'''#!/usr/bin/env python3
# Stand-in for `systemctl --user`: enable/restart start a tiny app on the unit's PORT; stop kills it.
import os, re, signal, subprocess, sys
from pathlib import Path
units, state = Path(os.environ["MOSES_UNIT_DIR"]), Path(os.environ["FAKE_STATE"])
args = [a for a in sys.argv[1:] if not a.startswith("--")]
if not args or args[0] == "daemon-reload":
    sys.exit(0)
if args[0] == "show":                        # show -p MainPID --value <unit>
    pidf = state / (args[-1].removesuffix(".service") + ".pid")
    print(pidf.read_text().strip() if pidf.exists() else "0"); sys.exit(0)
unit = args[-1].removesuffix(".service")
pidf = state / f"{unit}.pid"
if pidf.exists():
    try: os.kill(int(pidf.read_text()), signal.SIGTERM)
    except (OSError, ValueError): pass
    pidf.unlink()
if args[0] in ("enable", "restart", "start") and os.environ.get("FAKE_APP_DOWN") != "1":
    bind = os.environ.get("FAKE_BIND", "127.0.0.1")
    leak = os.environ.get("FAKE_SERVE_ENV") == "1"
    port = re.search(r"^Environment=PORT=(\d+)", (units / f"{unit}.service").read_text(), re.M).group(1)
    code = ("import http.server as h\nclass H(h.BaseHTTPRequestHandler):\n def do_GET(s):\n"
            "  b=b'USAJOBS_API_KEY=secret' if (" + str(leak) + " and s.path=='/.env') else b'<h1>the-user app</h1>'\n"
            "  s.send_response(200);s.send_header('Content-Length',str(len(b)));s.end_headers();s.wfile.write(b)\n"
            " def log_message(s,*a): pass\nh.ThreadingHTTPServer(('" + bind + "'," + port + "),H).serve_forever()\n")
    p = subprocess.Popen([sys.executable, "-c", code], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pidf.write_text(str(p.pid))
'''


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def serve_supports_apps() -> bool:
    return SERVE.is_file() and "published_apps" in SERVE.read_text()


def suite(script: Path, verbose: bool):
    passed, failed, skipped = [], [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        if verbose:
            print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' — ' + str(detail)[-300:]}")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        g = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        env0 = {**{k: v for k, v in os.environ.items() if not k.startswith(("MOSES_", "KNIGHT_", "SITE_"))}, **g}
        git = lambda *a, cwd: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, env=env0)
        # The project, runnable by `npm start`.
        src = tmp / "Projects" / "gis"; src.mkdir(parents=True)
        (src / "package.json").write_text('{"scripts":{"start":"node server.js"}}')
        (src / ".env").write_text("USAJOBS_API_KEY=x\nPORT=3000\nHOST=0.0.0.0\n")   # must not win
        git("init", "-q", "-b", "main", cwd=src); git("add", "package.json", cwd=src); git("commit", "-qm", "one", cwd=src)
        # A second project that is built but says nothing about how to run it.
        nr = tmp / "Projects" / "norun"; nr.mkdir(parents=True); (nr / "README.md").write_text("x")
        git("init", "-q", "-b", "main", cwd=nr); git("add", ".", cwd=nr); git("commit", "-qm", "one", cwd=nr)
        # The Moses tree with the targets.
        moses = tmp / "moses"; targets = moses / "knight" / "targets"; targets.mkdir(parents=True)
        (targets / "norun.env").write_text(f"KT_SOURCE={nr}\nKT_BRANCH=main\nKT_PUSH_MODE=branch\n")
        (targets / "gis.env").write_text(f"KT_SOURCE={src}\nKT_BRANCH=main\nKT_PUSH_MODE=branch\nKT_PUSH_REMOTE={src}\n"
                                         "KT_PREP=(\n  './prep.sh'\n)\nKT_GATE=(\n  './gate.sh'\n)\nKT_LIVE_CMD=''\nKT_LIVE_CHECK=''\n")
        git("init", "-q", "-b", "main", cwd=moses); git("add", ".", cwd=moses); git("commit", "-qm", "seed", cwd=moses)
        reg = tmp / "projects.json"
        reg.write_text(json.dumps({"projects": [
            {"id": "gis", "name": "Listings finder", "rank": 13, "status": "active", "stage": "building",
             "kind": "internal", "milestones": [], "ideas": [], "scope": "x"},
            {"id": "norun", "name": "No run", "rank": 14, "status": "active", "stage": "building",
             "kind": "internal", "milestones": [], "ideas": [], "scope": "x"}]}))
        site = tmp / "site"; (site / "blog").mkdir(parents=True); (site / "index.html").write_text("home")
        apps = tmp / "apps.json"
        fake = tmp / "systemctl"; fake.write_text(FAKE_SYSTEMCTL); fake.chmod(0o755)
        (tmp / "state").mkdir()
        public_port = free_port()
        site_proc = None
        if serve_supports_apps():
            site_proc = subprocess.Popen([sys.executable, str(SERVE), "--root", str(site), "--port", str(public_port)],
                                         env={**env0, "SITE_APPS": str(apps)},   # serve.py's own name for it (the site's code, not Moses)
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.8)
        env = {**env0, "KNIGHT_TARGETS": str(targets), "MOSES_REGISTRY": str(reg),
               "MOSES_MCP_DIR": str(HERE.parent / "mcp"), "MOSES_SITE_DIR": str(site),
               "MOSES_SITE_APPS": str(apps), "MOSES_UNIT_DIR": str(tmp / "units"), "FAKE_STATE": str(tmp / "state"),
               "MOSES_SYSTEMCTL": f"{fake} --user", "MOSES_PUBLIC_BASE": f"http://127.0.0.1:{public_port}",
               "MOSES_PUBLISH_WAIT": "5"}
        pub = lambda *a, **e: subprocess.run([str(script), *a], capture_output=True, text=True, env={**env, **e}, timeout=120)
        try:
            r = pub("gis", "blog")
            check("a path that is a page of the site is refused", r.returncode == 1 and "already a page" in r.stdout
                  and not apps.exists(), r.stdout)
            r = pub("norun", "norun")
            check("a project that says nothing about how to run is refused, not guessed",
                  r.returncode == 1 and "how to run" in r.stdout, r.stdout)
            r = pub("gis", "listings", FAKE_APP_DOWN="1")
            check("an app that never answers locally changes nothing public",
                  r.returncode == 1 and not apps.exists(), r.stdout)
            r = pub("gis", "listings", FAKE_BIND="0.0.0.0")
            check("an app listening on every interface is refused and stopped, nothing routed",
                  r.returncode == 1 and "not only on 127.0.0.1" in r.stdout and not apps.exists(), r.stdout)
            r = pub("gis", "listings", FAKE_SERVE_ENV="1")
            check("an app that serves its own .env is refused, nothing routed",
                  r.returncode == 1 and "/.env" in r.stdout and not apps.exists(), r.stdout)
            if not site_proc:
                skipped.append("publishing end to end (needs serve.py with published apps)")
                r = pub("gis", "listings")
                dep = next(p for p in json.loads(reg.read_text())["projects"] if p["id"] == "gis").get("deploy")
                check("with no public answer it is NOT reported live and the tracker is not told",
                      r.returncode == 1 and "NOT live" in r.stdout and not dep, (r.stdout, dep))
                return passed, failed, skipped

            r = pub("gis", "listings")
            unit = (tmp / "units" / "app-gis.service")
            ut = unit.read_text() if unit.exists() else ""
            check("publishing succeeds", r.returncode == 0, r.stdout)
            check("the service runs the project's start command from its checkout, on loopback, with its .env",
                  f"WorkingDirectory={src}" in ut and "HOST=127.0.0.1" in ut and "BASE_PATH" not in ut
                  and f"EnvironmentFile=-{src}/.env" in ut
                  and re.search(r"^ExecStart=/usr/bin/env PORT=88\d\d HOST=127\.0\.0\.1 /usr/bin/npm start$", ut, re.M)
                  and "NoNewPrivileges=yes" in ut and "Listings finder" not in ut, ut)
            entry = (json.loads(apps.read_text())["apps"].get("/listings") if apps.exists() else None) or {}
            check("the route names the project and a port in 8800–8899",
                  entry.get("project") == "gis" and 8800 <= entry.get("port", 0) < 8900, entry)
            with urllib.request.urlopen(f"http://127.0.0.1:{public_port}/listings/", timeout=10) as resp:
                body = resp.read().decode()
            check("through the real site server, the address shows the app", "the-user app" in body, body)
            check("and publishing says it is live", "Live:" in r.stdout, r.stdout)
            envtext = (targets / "gis.env").read_text()
            check("landings restart the app: KT_LIVE_CMD and KT_LIVE_CHECK are set",
                  re.search(r"^KT_LIVE_CMD='.*systemctl --user restart app-gis'$", envtext, re.M)
                  and re.search(r"^KT_LIVE_CHECK='\S*moses-publish --verify gis'$", envtext, re.M), envtext)
            v = subprocess.run([str(script), "--verify", "gis"], capture_output=True, text=True, env=env, timeout=60)
            check("after a landing, --verify passes for the healthy app", v.returncode == 0, v.stdout)
            shape = subprocess.run([sys.executable, str(SHAPE), "gis"], input=envtext, capture_output=True, text=True)
            check("and the target file is still plain settings (knight-land's shape rule)", shape.returncode == 0, shape.stdout)
            check("the target change is committed by itself in the Moses tree",
                  git("show", "--name-only", "--format=", "HEAD", cwd=moses).stdout.split() == ["knight/targets/gis.env"])
            dep = next(p for p in json.loads(reg.read_text())["projects"] if p["id"] == "gis").get("deploy")
            check("the tracker records where it is live", dep == f"http://127.0.0.1:{public_port}/listings/", dep)

            first = entry.get("port")
            r = pub("gis", "listings")
            again = json.loads(apps.read_text())["apps"]["/listings"].get("port")
            check("publishing again keeps the same port", r.returncode == 0 and again == first, (first, again))
            r2 = subprocess.run([str(script), "norun", "listings"], capture_output=True, text=True,
                                env={**env, "MOSES_REGISTRY": str(reg)})
            check("another project cannot take a published path", r2.returncode == 1 and "already gis's" in r2.stdout, r2.stdout)
        finally:
            for f in (tmp / "state").glob("*.pid"):
                try:
                    os.kill(int(f.read_text()), 15)
                except (OSError, ValueError):
                    pass
            if site_proc:
                site_proc.terminate(); site_proc.wait(5)
    return passed, failed, skipped


MUTANTS = [
    ("wide bind allowed", "        if not (local.startswith(\"127.0.0.1:\") or local.startswith(\"[::1]:\")):",
     "        if False:", "an app listening on every interface is refused and stopped, nothing routed"),
    ("secrets not checked", '        if status == 200 and mark in body:', '        if False:',
     "an app that serves its own .env is refused, nothing routed"),
    ("pages shadowed", '        if (SITE_DIR / name).exists():\n', '        if False:\n',
     "a path that is a page of the site is refused"),
    # Two barriers: it must answer, and its listener must be checked. Both go.
    ("published before it answers", "        if not 0 < status < 500:\n            raise Stop(f\"the app did not answer",
     "        if False:\n            raise Stop(f\"the app did not answer", "an app that never answers locally changes nothing public",
     "        problem = listener_ok(port, unit) or exposes_secrets(port)\n", "        problem = \"\"\n"),
]

if __name__ == "__main__":
    print("MOSES-PUBLISH")
    passed, failed, skipped = suite(SCRIPT, True)
    for s in skipped:
        print(f"  SKIP {s} — {SERVE} does not support published apps yet")
    print(f"\n  {len(passed)}/{len(passed) + len(failed)} pass\n\nWATCH EACH GUARD FAIL")
    src, blind = SCRIPT.read_text(), []
    with tempfile.TemporaryDirectory() as md:
        (Path(md) / "lib").symlink_to(HERE / "lib")
        for mut in MUTANTS:
            name, old, new, expect = mut[:4]
            edits = [(old, new)] + ([(mut[4], mut[5])] if len(mut) > 4 else [])
            if any(src.count(o) != 1 for o, _ in edits):
                print(f"  FAIL {name}: the sabotage no longer applies"); blind.append(name); continue
            text = src
            for o, n in edits:
                text = text.replace(o, n)
            m = Path(md) / "moses-publish"; m.write_text(text); m.chmod(0o755)
            red = expect in suite(m, False)[1]
            print(f"  {'ok  ' if red else 'FAIL'} {name} → '{expect}' goes red")
            if not red:
                blind.append(name)
    if failed or blind:
        print(f"\nFAILED: {len(failed)} check(s), {len(blind)} guard(s) not watched failing"); sys.exit(1)
    print(f"\nall {len(passed)} pass, and every guard was watched failing")
