#!/usr/bin/env python3
"""Task grants (#17): typed actions, and root's own path to Brad's eyes — each guard watched failing.

    python3 agent/task_grant_test.py

Runs the REAL agent/moses-mfa as the operator against a scratch seed, state and a FAKE Slack (a local
HTTP server standing in for slack.com), with systemctl and apt-get replaced by recorders. Every
MOSES_MFA_* override it uses is ignored when the helper runs as root. Then it breaks the helper on
purpose, one guard at a time, and requires the check for that guard to go red (commandment 8).
"""
import base64
import hashlib
import hmac
import http.server
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
HELPER = HERE / "moses-mfa"
T0 = 1_800_000_000.0
SECRET = base64.b32encode(b"moses-task-test-seed").decode().rstrip("=")
OWNER, OTHER, TOKEN, CHAN = "UBRAD", "UOTHER", "xoxb-gate-test", "COPS"


def code_at(t: float) -> str:
    key = base64.b32decode(SECRET + "=" * (-len(SECRET) % 8))
    mac = hmac.new(key, struct.pack(">Q", int(t // 30)), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    return str((struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000).zfill(6)


class FakeSlack:
    """chat.postMessage and conversations.replies, enough to see what root posts and to play Brad."""

    def __init__(self):
        self.messages: list[dict] = []
        self.n = 0
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
                p = {k: v[0] for k, v in body.items()}
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    out = {"ok": False, "error": "invalid_auth"}
                elif self.path.endswith("/chat.postMessage"):
                    out = {"ok": True, "channel": p["channel"],
                           "ts": fake.add(p["channel"], p["text"], bot=True, thread=p.get("thread_ts"))}
                elif self.path.endswith("/conversations.replies"):
                    ts = p["ts"]
                    out = {"ok": True, "messages": [m for m in fake.messages if m["channel"] == p["channel"]
                                                    and (m["ts"] == ts or m.get("thread_ts") == ts)]}
                else:
                    out = {"ok": False, "error": "unknown_method"}
                data = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def add(self, channel, text, bot=False, user=None, thread=None, at=None):
        self.n += 1
        # `at` plays Slack's server clock for one reply; the micro part keeps every ts unique.
        ts = f"{1800000000 + self.n}.000100" if at is None else f"{int(at)}.{self.n:06d}"
        m = {"channel": channel, "ts": ts, "text": text}
        if bot:
            m["bot_id"] = "BGATE"
        if user:
            m["user"] = user
        if thread:
            m["thread_ts"] = thread
        self.messages.append(m)
        return ts

    def thread_of(self, ts):
        return [m for m in self.messages if m.get("thread_ts") == ts]


class World:
    def __init__(self, helper: Path, root: Path, name: str, slack: FakeSlack, gate=True):
        self.helper, self.dir, self.slack = helper, root / name, slack
        for d in ("etc", "dest/etc/systemd/system", "dest/usr/local/bin", "units", "bin"):
            (self.dir / d).mkdir(parents=True, exist_ok=True)
        (self.dir / "etc" / "totp.seed").write_text(SECRET + "\n")
        self.gate = self.dir / "etc" / "slack-gate.env"
        if gate:
            self.gate.write_text(f"SLACK_GATE_TOKEN={TOKEN}\nSLACK_GATE_CHANNEL={CHAN}\nSLACK_OWNER_ID={OWNER}\n")
        self.log = self.dir / "tools.log"
        # The recorders. systemctl `show` answers from units/<name>.props; anything else is logged.
        sc = self.dir / "bin" / "systemctl"
        sc.write_text(f"""#!/bin/sh
if [ "$1" = show ]; then cat "{self.dir}/units/$2.props" 2>/dev/null; exit 0; fi
echo "systemctl $*" >> "{self.log}"
umask > "{self.dir}/umask.seen"
case "$*" in *die.service*) kill -TERM $$ ;; *fail.service*) exit 3 ;; esac
exit 0
""")
        ap = self.dir / "bin" / "apt-get"
        ap.write_text(f'#!/bin/sh\necho "apt-get $*" >> "{self.log}"\nexit 0\n')
        sc.chmod(0o755)
        ap.chmod(0o755)
        self.t = T0

    def unit(self, name, user="", exec_path="/usr/bin/true", envfile=None, writable=False, uid=None,
             workdir="", environment="", argv0=None, triggers="", extra=()):
        frag = self.dir / "units" / f"{name}.file"
        frag.write_text("[Service]\n")
        frag.chmod(0o666 if writable else 0o644)
        props = [f"Id={uid or name}", f"FragmentPath={frag}", f"User={user}", f"WorkingDirectory={workdir}",
                 f"Environment={environment}", f"Triggers={triggers}",
                 f"ExecStart={{ path={exec_path} ; argv[]={argv0 or exec_path} --flag ; ignore_errors=no }}"]
        if envfile:
            props.append(f"EnvironmentFiles={envfile} (ignore_errors=no)")
        props += list(extra)
        (self.dir / "units" / f"{name}.props").write_text("\n".join(props) + "\n")

    def call(self, *args, stdin="", t=None, extra_env=None, popen=False):
        env = {k: v for k, v in os.environ.items() if not k.startswith("MOSES_MFA_")}
        env.update(MOSES_MFA_SEED=str(self.dir / "etc" / "totp.seed"), MOSES_MFA_STATE=str(self.dir / "state"),
                   MOSES_MFA_NOW=str(self.t if t is None else t), MOSES_MFA_GATE_ENV=str(self.gate),
                   MOSES_MFA_SLACK_API=self.slack.url, MOSES_MFA_SYSTEMCTL=str(self.dir / "bin" / "systemctl"),
                   MOSES_MFA_APT_GET=str(self.dir / "bin" / "apt-get"), MOSES_MFA_DEST_PREFIX=str(self.dir / "dest"))
        env.update(extra_env or {})
        if popen:
            return subprocess.Popen([sys.executable, "-I", str(self.helper), *[str(a) for a in args]], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            r = subprocess.run([sys.executable, "-I", str(self.helper), *[str(a) for a in args]], input=stdin,
                               capture_output=True, text=True, env=env, timeout=60)
        except subprocess.TimeoutExpired:
            return -1, "", "the helper hung (killed after 60s)"
        return r.returncode, r.stdout, r.stderr

    def ask(self, actions, title="a test task", hours=None):
        spec = {"title": title, "actions": actions}
        if hours is not None:
            spec["hours"] = hours
        rc, out, err = self.call("challenge", "task", stdin=json.dumps(spec))
        if rc == 0:
            self.posted = json.loads(out)
            # Root's post carries no id any more (Brad, 2026-11-05: too robotic): it is the newest
            # top-level post from the gate right after the request opened.
            tops = [m for m in self.slack.messages if m.get("bot_id") and not m.get("thread_ts")]
            self.posted["ts"] = tops[-1]["ts"] if tops else ""
        return (json.loads(out) if rc == 0 else None), err

    def post_ts(self):
        """The ts of root's own post for this world's open request."""
        return getattr(self, "posted", {}).get("ts", "")

    def reply(self, text, user=OWNER, bot=False, thread=None, at=None):
        return self.slack.add(CHAN, text, bot=bot, user=user, thread=thread or self.post_ts(), at=at)

    def grant(self, actions, **kw):
        c, err = self.ask(actions, **kw)
        if not c:
            return None, err
        self.reply(code_at(self.t))
        rc, out, err = self.call("verify-slack")
        return (json.loads(out)["authorization"] if rc == 0 else None), err

    def tools(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def ledger(self):
        p = self.dir / "state" / "audit.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def suite(helper: Path):
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        if helper == HELPER:
            print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' — ' + str(detail)[:240]}")

    os.umask(0o022)                          # scratch dirs must not be group-writable, or the helper refuses them
    slack = FakeSlack()
    # Under HOME, not /tmp: the helper refuses directories others can write, and /tmp is one.
    base = Path(tempfile.mkdtemp(dir=os.path.expanduser("~"), prefix=".task-grant-test-"))
    os.chmod(base, 0o700)
    try:
        # ── No gate, no task ──
        w = World(helper, base, "nogate", slack, gate=False)
        c, err = w.ask([{"type": "apt-update"}])
        check("with no Moses Gate set up, a task is not asked at all (could not check), and nothing is posted",
              c is None and "Moses Gate" in err and not slack.messages, err)

        # ── Root posts what Brad reads ──
        w = World(helper, base, "happy", slack)
        w.unit("app.service")
        src = w.dir / "app.service.src"
        src.write_text("[Service]\nExecStart=/usr/bin/true\n")
        acts = [{"type": "unit", "verb": "restart", "unit": "app.service"},
                {"type": "apt-install", "packages": ["nodejs", "npm"]},
                {"type": "install-file", "source": str(src), "dest": "/etc/systemd/system/app.service"},
                {"type": "unit", "verb": "daemon-reload"}]
        c, err = w.ask(acts, title="Set up the app", hours=2)
        post = next((m for m in slack.messages if m.get("bot_id") and "Set up the app" in m["text"]), None)
        check("root posts the task itself, from the gate's token, in the gate's channel",
              bool(c) and bool(post) and post["channel"] == CHAN, err)
        text = post["text"] if post else ""
        check("what Brad reads names every action exactly as root will do it",
              all(x in text for x in ("restart -- app.service", "install -y -q --no-remove -- nodejs npm",
                                      "/etc/systemd/system/app.service", "daemon-reload", "2 hour")), text)
        check("…and shows what the installed file will contain", "ExecStart=/usr/bin/true" in text, text)
        check("the prompt root prints is the one it posted", bool(c) and c["prompt"] in text)

        # ── Only Brad, only in that thread ──
        rc, out, err = w.call("verify", c["challenge"] if c else "x", stdin=code_at(w.t))
        check("a task's code handed in on stdin is refused, even the right one", rc != 0 and "never handed in" in err, (rc, err))
        rc, out, err = w.call("verify-slack")
        check("with no reply yet, nothing is granted", rc != 0 and "no new code" in err, (rc, err))
        slack.add(CHAN, code_at(w.t), user=OWNER)                    # top level, not in the thread
        rc, out, err = w.call("verify-slack")
        check("Brad's code outside the request's thread does not count", rc != 0 and "no new code" in err, (rc, err))
        w.reply("000000")
        rc, out, err = w.call("verify-slack")
        check("a wrong code is refused, and root says so in the thread",
              rc != 0 and "wrong code" in err and any("Didn't take" in m["text"] for m in slack.thread_of(w.post_ts())), (rc, err))
        rc, out, err = w.call("verify-slack")
        check("a reply already tried is not tried again", rc != 0 and "no new code" in err, (rc, err))
        w.reply(code_at(w.t))
        rc, out, err = w.call("verify-slack")
        g = json.loads(out)["authorization"] if rc == 0 else ""
        check("Brad's right code, in the thread, grants the task", rc == 0 and bool(g), err)
        check("…and root posts the grant in the thread itself",
              any("Approved" in m["text"] and m.get("bot_id") for m in slack.thread_of(w.post_ts())))
        check("no code reaches the ledger", code_at(w.t) not in json.dumps(w.ledger()))

        # ── Root does the actions; the caller only names one ──
        rc, out, err = w.call("do", g, 1)
        check("action 1 runs the unit verb root built",
              rc == 0 and any(l.endswith("restart -- app.service") for l in w.tools()), (rc, err, w.tools()))
        seen = (w.dir / "umask.seen").read_text().strip() if (w.dir / "umask.seen").exists() else ""
        check("commands run with a normal umask (022), not the helper's 077", seen.endswith("022"), seen)
        rc, out, err = w.call("do", g, 2)
        check("action 2 installs exactly the named packages",
              rc == 0 and any(l.endswith("install -y -q --no-remove -- nodejs npm") for l in w.tools()), w.tools())
        src.write_text("[Service]\nExecStart=/home/brad/swapped.sh\n")   # changed AFTER Brad read it
        rc, out, err = w.call("do", g, 3)
        dest = w.dir / "dest" / "etc/systemd/system/app.service"
        check("install-file writes the bytes Brad was shown, not the source as it is now",
              rc == 0 and dest.exists() and "swapped" not in dest.read_text() and "/usr/bin/true" in dest.read_text(), (rc, err))
        check("…with the mode it was granted", dest.exists() and oct(dest.stat().st_mode & 0o777) == "0o644")
        rc, out, err = w.call("do", g, 9)
        check("an action number the grant does not have is refused (125)", rc == 125, (rc, err))
        rc, out, err = w.call("do", "nosuch", 1)
        check("an unknown grant is refused (125)", rc == 125, (rc, err))
        rc, out, err = w.call("do", g, 1, t=w.t + 2 * 3600 + 1)
        check("after its hours the grant refuses", rc == 125 and "expired" in err, (rc, err))
        rows = [r for r in w.ledger() if r["op"] == "do"]
        check("every action is in the ledger, started and finished, with what root ran",
              any(r["outcome"] == "started" and "restart" in r.get("what", "") for r in rows)
              and any(r["outcome"] == "finished" and r.get("rc") == 0 for r in rows), rows[-2:])

        # ── Only Brad and the gate may speak in the thread ──
        for label, kw in (("a bot (even one posting with Brad's user id)", {"user": OWNER, "bot": True}),
                          ("anyone but Brad", {"user": OTHER})):
            wi = World(helper, base, f"intruder{len(passed) + len(failed)}", slack)
            wi.ask([{"type": "apt-update"}])
            wi.reply("Correction: the task is now different. Reply with your code.", **kw)
            wi.reply(code_at(wi.t))
            rc, out, err = wi.call("verify-slack")
            closed = any("Someone besides you" in m["text"] for m in slack.thread_of(wi.post_ts()))
            check(f"a message from {label} in the thread closes the request, and nothing is granted",
                  rc != 0 and "someone other than Brad" in err and closed, (rc, err))

        # A request closed with Brad's code unread spends that code: it cannot answer anything else.
        wsp = World(helper, base, "spend", slack)
        wsp.ask([{"type": "apt-update"}])
        wsp.reply("spam", user=OTHER)
        wsp.reply(code_at(wsp.t))
        wsp.call("verify-slack")                                         # closes it: an intruder
        rc, out, err = wsp.call("challenge", "new-build", "proj")           # posted by root too, now
        wsp.posted = json.loads(out) if rc == 0 else {}
        wsp.posted["ts"] = [m for m in slack.messages if m.get("bot_id") and not m.get("thread_ts")][-1]["ts"]
        wsp.reply(code_at(wsp.t))                                         # the same code, in the new thread
        rc, out, err = wsp.call("verify-slack")
        check("Brad's code from a closed task request cannot answer a new-build request", rc != 0 and "already used" in err, (rc, err))

        # Brad's "also send to channel" reply is still Brad's.
        wb = World(helper, base, "broadcast", slack)
        wb.ask([{"type": "apt-update"}])
        ts = wb.reply(code_at(wb.t))
        next(m for m in slack.messages if m["ts"] == ts)["subtype"] = "thread_broadcast"
        rc, out, err = wb.call("verify-slack")
        check("Brad's reply sent to the channel as well still counts as Brad's", rc == 0, (rc, err))

        # Drop-ins: the unit's .d directory is made when it is the first.
        wd = World(helper, base, "dropin", slack)
        sd = wd.dir / "o.conf"
        sd.write_text("[Service]\nNice=5\n")
        gd, ed = wd.grant([{"type": "install-file", "source": str(sd), "dest": "/etc/systemd/system/app.service.d/override.conf"}])
        rc, out, err = wd.call("do", gd, 1)
        check("a unit's first drop-in creates its .d directory",
              rc == 0 and (wd.dir / "dest/etc/systemd/system/app.service.d/override.conf").exists(), (rc, err))

        # An install that fails still finishes its ledger row, and says why.
        wf = World(helper, base, "installfail", slack)
        sf = wf.dir / "lr"
        sf.write_text("/var/log/x {\n}\n")
        gf, ef = wf.grant([{"type": "install-file", "source": str(sf), "dest": "/etc/logrotate.d/x"}])
        rc, out, err = wf.call("do", gf, 1)                            # dest/etc/logrotate.d does not exist
        check("an install that fails exits 1, says why, and the ledger has its finished row",
              rc == 1 and "install-file failed" in err
              and any(r["op"] == "do" and r["outcome"] == "finished" for r in wf.ledger()), (rc, err))

        # A symlink planted at the destination is not followed.
        w2 = World(helper, base, "symlink", slack)
        s2 = w2.dir / "x.src"
        s2.write_text("x\n")
        g2, e2 = w2.grant([{"type": "install-file", "source": str(s2), "dest": "/usr/local/bin/tool", "mode": "0755"}])
        os.symlink(w2.dir / "victim", w2.dir / "dest/usr/local/bin/tool")
        rc, out, err = w2.call("do", g2, 1)
        check("a symlink planted at the destination is never followed (the victim is untouched)",
              not (w2.dir / "victim").exists(), (rc, err))

        # Exit codes come through as the command's own.
        w3 = World(helper, base, "rc", slack)
        w3.unit("fail.service")
        w3.unit("die.service")
        g3, e3 = w3.grant([{"type": "unit", "verb": "restart", "unit": "fail.service"},
                           {"type": "unit", "verb": "restart", "unit": "die.service"}])
        rc, out, err = w3.call("do", g3, 1)
        check("a command's own failure comes back as its exit code, not a refusal", rc == 3 and "moses-mfa:" not in err, (rc, err))
        rc, out, err = w3.call("do", g3, 2)
        check("a command killed by a signal comes back as 128+signal", rc == 143, (rc, err))

        # Revocation.
        w4 = World(helper, base, "revoke", slack)
        g4, _ = w4.grant([{"type": "apt-update"}])
        w4.call("revoke", g4)
        rc, out, err = w4.call("do", g4, 1)
        check("a revoked grant refuses", rc == 125 and "revoked" in err, (rc, err))

        # ── Units that would hand root to the operator ──
        w5 = World(helper, base, "units", slack)
        mine = w5.dir / "mine.sh"
        mine.write_text("#!/bin/sh\n")
        mine.chmod(0o777)
        w5.unit("runsmine.service", exec_path=str(mine))
        w5.unit("asuser.service", user="www-data", exec_path=str(mine))
        w5.unit("asuserdir.service", user="www-data", workdir=str(w5.dir))
        w5.unit("alias.service", uid="systemd-logind.service")
        lib = w5.dir / "evil.so"
        lib.write_text("x")
        lib.chmod(0o666)
        w5.unit("preload.service", environment=f"LD_PRELOAD={lib} LANG=C")
        w5.unit("atpath.service", exec_path=str(mine), argv0="@/usr/bin/true")
        w5.unit("bad.service", exec_path=str(mine))
        w5.unit("nightly.timer", exec_path="/usr/bin/true", triggers="bad.service")
        opdir = w5.dir / "opdir"
        opdir.mkdir()
        opdir.chmod(0o777)
        os.symlink("/usr/bin/true", opdir / "tool")
        w5.unit("vialink.service", exec_path=str(opdir / "tool"))
        w5.unit("missing.service", exec_path="/usr/bin/python3", argv0=f"/usr/bin/python3 {opdir}/not-yet.py")
        w5.unit("optpath.service", exec_path="/usr/bin/true", argv0=f"/usr/bin/true --config={opdir}/c.ini")
        w5.unit("onfail.service", exec_path="/usr/bin/true", extra=("OnFailure=bad.service",))
        w5.unit("wantsjournal.service", exec_path="/usr/bin/true", extra=("Wants=sysjournal.service",))
        w5.unit("sysjournal.service", uid="systemd-journald.service", exec_path="/usr/bin/true")
        import socket as _socket
        sockpath = w5.dir / "d.sock"
        _sk = _socket.socket(_socket.AF_UNIX); _sk.bind(str(sockpath)); os.chmod(sockpath, 0o666)
        w5.unit("withsock.service", exec_path="/usr/bin/true", argv0=f"/usr/bin/true --socket={sockpath}")
        w5.unit("slashes.service", exec_path="/usr/bin/true", argv0="/usr/bin/true //etc //")
        envf = w5.dir / "app.env"
        envf.write_text("LD_PRELOAD=/x\n")
        envf.chmod(0o666)
        w5.unit("envmine.service", envfile=str(envf))
        w5.unit("fragmine.service", writable=True)
        names = ("runsmine.service", "asuser.service", "envmine.service", "fragmine.service", "asuserdir.service",
                 "alias.service", "preload.service", "atpath.service", "nightly.timer", "vialink.service",
                 "missing.service", "optpath.service", "onfail.service", "slashes.service", "wantsjournal.service", "withsock.service")
        r = {}
        for part in (names[:7], names[7:]):                              # 12 actions at most per task
            wpart = World(helper, base, f"units{len(r)}", slack)
            wpart.dir = w5.dir                                           # same units, same recorders
            wpart.gate, wpart.log = w5.gate, w5.log
            wpart.t = w5.t + 60 * (1 if r else 0)                       # its own time step: a code is used once
            g5, e5 = wpart.grant([{"type": "unit", "verb": "restart", "unit": u} for u in part])
            check(f"the units test's task {1 if not r else 2} was granted (so its refusals below are real)", bool(g5), e5)
            r.update(zip(part, (wpart.call("do", g5, n) for n in range(1, len(part) + 1))))
        check("a root unit that runs a file the operator can change is refused",
              r["runsmine.service"][0] == 125 and "operator can change" in r["runsmine.service"][2], r["runsmine.service"])
        check("…and so is one that runs as another user (a `+` prefix would still run it as root)",
              r["asuser.service"][0] == 125 and "operator can change" in r["asuser.service"][2], r["asuser.service"])
        check("a unit running as another user may keep its working directory in the operator's space",
              r["asuserdir.service"][0] == 0, r["asuserdir.service"])
        check("a unit whose environment file the operator can change is refused", r["envmine.service"][0] == 125 and "operator can change" in r["envmine.service"][2], r["envmine.service"])
        check("a unit whose own file the operator can change is refused", r["fragmine.service"][0] == 125 and "operator can change" in r["fragmine.service"][2], r["fragmine.service"])
        check("an alias of a Never-list unit is refused by what it resolves to",
              r["alias.service"][0] == 125 and "Never list" in r["alias.service"][2], r["alias.service"])
        check("a root unit whose environment points at an operator file (LD_PRELOAD=) is refused",
              r["preload.service"][0] == 125 and "operator can change" in r["preload.service"][2], r["preload.service"])
        check("the program that runs (path=) is checked, not just what argv[0] claims",
              r["atpath.service"][0] == 125 and "operator can change" in r["atpath.service"][2], r["atpath.service"])
        check("a symlink in a directory the operator can write is refused, even when it points somewhere safe",
              r["vialink.service"][0] == 125 and "operator can change" in r["vialink.service"][2], r["vialink.service"])
        check("a script that does not exist yet, in a place the operator could create it, is refused",
              r["missing.service"][0] == 125 and "operator can change" in r["missing.service"][2], r["missing.service"])
        check("a path given as --option=/path is checked too", r["optpath.service"][0] == 125 and "operator can change" in r["optpath.service"][2], r["optpath.service"])
        check("a unit started on failure (OnFailure=) is checked too", r["onfail.service"][0] == 125 and "operator can change" in r["onfail.service"][2], r["onfail.service"])
        check("a path written with a leading // is judged, not walked forever (a real unit hung the check)",
              r["slashes.service"][0] == 0, r["slashes.service"])
        check("a unit that merely pulls in the journal (as nearly all do) may still be restarted",
              r["wantsjournal.service"][0] == 0, r["wantsjournal.service"])
        check("a socket the operator may connect to (mode 666) is not code they can change",
              r["withsock.service"][0] == 0, r["withsock.service"])
        check("starting a timer checks the service it triggers",
              r["nightly.timer"][0] == 125 and "also starts bad.service" in r["nightly.timer"][2], r["nightly.timer"])

        # ── What no code can grant (refused when asked) ──
        def refused(name, actions, needle, hours=None):
            wx = World(helper, base, f"r{len(passed) + len(failed)}", slack)
            cx, err = wx.ask(actions, hours=hours)
            check(name, cx is None and needle in err, err)

        f = w.dir / "any.src"
        f.write_text("x\n")
        refused("a file may not be installed into sudoers",
                [{"type": "install-file", "source": str(f), "dest": "/etc/sudoers.d/x"}], "not a place")
        refused("…nor into cron (root runs what is there)",
                [{"type": "install-file", "source": str(f), "dest": "/etc/cron.d/x"}], "not a place")
        refused("…nor anywhere through '..'",
                [{"type": "install-file", "source": str(f), "dest": "/usr/local/bin/../../etc/shadow"}], "not a place")
        refused("…and a double slash does not sneak past",
                [{"type": "install-file", "source": str(f), "dest": "/etc/systemd//system/x.service"}], "not a place")
        refused("the journal is on the Never list", [{"type": "unit", "verb": "stop", "unit": "systemd-journald.service"}], "Never list")
        refused("so is ssh", [{"type": "unit", "verb": "stop", "unit": "ssh.service"}], "Never list")
        refused("a unit name with a path in it is refused", [{"type": "unit", "verb": "start", "unit": "../x.service"}], "not a unit name")
        refused("`systemctl edit` is not a verb a task can use", [{"type": "unit", "verb": "edit", "unit": "x.service"}], "unit verb")
        refused("apt options cannot ride in as a package", [{"type": "apt-install", "packages": ["-oAPT::X=1"]}], "plain package names")
        refused("…nor a local .deb", [{"type": "apt-install", "packages": ["./evil.deb"]}], "plain package names")
        refused("…nor a pinned version", [{"type": "apt-install", "packages": ["nodejs=1.0"]}], "plain package names")
        refused("…nor a name ending in '-', which tells apt to REMOVE it", [{"type": "apt-install", "packages": ["sudo-"]}], "plain package names")
        refused("root does not read a file for the caller that the caller cannot read",
                [{"type": "install-file", "source": "/etc/shadow", "dest": "/etc/logrotate.d/x"}], "cannot read")
        long = w.dir / "long.src"
        long.write_text("x\n" * 61)
        refused("a file Brad cannot read whole (over 60 lines) cannot be installed",
                [{"type": "install-file", "source": str(long), "dest": "/usr/local/bin/t"}], "shown in full")
        sneaky = w.dir / "rtl.src"
        sneaky.write_text("echo ok \u202e;)live(\n")
        refused("a file with hidden or reordering characters cannot be installed",
                [{"type": "install-file", "source": str(sneaky), "dest": "/usr/local/bin/t"}], "shown in full")
        refused("nothing may be installed under a system command's name (it would shadow it on PATH)",
                [{"type": "install-file", "source": str(f), "dest": "/usr/local/bin/systemctl"}], "elsewhere on root's PATH")
        refused("no drop-in for a Never-list unit, either",
                [{"type": "install-file", "source": str(f), "dest": "/etc/systemd/system/ssh.service.d/x.conf"}], "Never list")
        refused("…nor a whole file for one",
                [{"type": "install-file", "source": str(f), "dest": "/etc/systemd/system/auditd.service"}], "Never list")
        ls = w.dir / "ls.src"
        ls.write_text("ok\u2028hidden line\n")
        refused("only printable ASCII is shown: a line separator that is not counted as a line is refused",
                [{"type": "install-file", "source": str(ls), "dest": "/usr/local/bin/t"}], "shown in full")
        big = []
        for k in range(4):
            bf = w.dir / f"big{k}.src"
            bf.write_text(("y" * 60 + "\n") * 58)
            big.append({"type": "install-file", "source": str(bf), "dest": f"/usr/local/bin/big{k}"})
        refused("a task too long to show whole is refused, not cut off", big, "split it")
        refused("…nor as the helper itself", [{"type": "install-file", "source": str(f), "dest": "/usr/local/bin/moses-mfa"}], "Never list")
        wt = World(helper, base, "rtltitle", slack)
        ct, et = wt.ask([{"type": "apt-update"}], title="harmless \u202e evil")
        check("a title with a reordering character is refused", ct is None and "hidden characters" in et, et)
        refused("a raw command is not an action type", [{"type": "run", "argv": ["/bin/sh"]}], "not an action type")
        refused("more than 12 hours is refused", [{"type": "apt-update"}], "hours", hours=13)
        refused("more than 12 actions is refused", [{"type": "apt-update"}] * 13, "1 to 12")
        link = w.dir / "link.src"
        os.symlink(f, link)
        refused("an install-file source that is a symlink is refused",
                [{"type": "install-file", "source": str(link), "dest": "/usr/local/bin/t"}], "cannot read")

        # ── What brad writes cannot become Slack markup in root's post ──
        we = World(helper, base, "escape", slack)
        md = we.dir / "md.src"
        md.write_text("<http://evil.example/$(id)|echo ok> <!channel>\n")
        ce, ee = we.ask([{"type": "install-file", "source": str(md), "dest": "/usr/local/bin/tool"}], title="ping <!channel> now")
        post = next((m for m in slack.messages if m.get("bot_id") and ce and m["ts"] == we.post_ts()), {})
        txt = post.get("text", "")
        check("a title or file text cannot ping the channel or hide a link behind a label",
              "<!channel>" not in txt and "&lt;!channel&gt;" in txt and "&lt;http://evil.example/$(id)|echo ok&gt;" in txt,
              (ee, txt[:300]))

        # ── A code is judged by when Brad gave it (live test, 2026-11-04) ──
        wl = World(helper, base, "late", slack)
        wl.t = T0 + 50_000
        cl, el = wl.ask([{"type": "apt-update"}], title="read late")
        wl.reply(code_at(wl.t + 5), at=wl.t + 5)
        rc, out, err = wl.call("verify-slack", t=wl.t + 120)
        check("a code is judged by when Brad posted it (Slack's stamp), not by when root got around to reading it",
              bool(cl) and rc == 0 and "authorization" in out, (el, rc, err))
        wf = World(helper, base, "future", slack)
        wf.t = T0 + 60_000
        cf, ef = wf.ask([{"type": "apt-update"}], title="stamped ahead")
        wf.reply(code_at(wf.t + 240), at=wf.t + 240)
        rc, out, err = wf.call("verify-slack", t=wf.t + 10)
        check("a reply stamped ahead of root's clock is not judged at that future time",
              bool(cf) and rc != 0 and "wrong code" in err, (ef, rc, err))

        # ── Root watches the thread itself: nothing else can see the gate channel ──
        def waiting(world, wait_env, then=None, delay=1.0):
            box = {}
            th = threading.Thread(target=lambda: box.update(r=world.call("verify-slack", "--wait",
                                                                         extra_env={"MOSES_MFA_WAIT": wait_env})))
            th.start()
            time.sleep(delay)
            for text in (then or []):
                world.reply(text)
                time.sleep(delay)
            th.join(70)
            return box.get("r", (-1, "", "the wait never returned"))
        ww = World(helper, base, "wait", slack)
        ww.t = T0 + 70_000
        cw, ew = ww.ask([{"type": "apt-update"}], title="wait for me")
        rc, out, err = waiting(ww, "0.2,20", then=[code_at(ww.t)])
        check("verify-slack --wait picks up a reply Brad posts after it started, and grants",
              bool(cw) and rc == 0 and "authorization" in out, (ew, rc, err))
        wn = World(helper, base, "nowait", slack)
        wn.t = T0 + 80_000
        cn, en = wn.ask([{"type": "apt-update"}], title="nobody answers")
        rc, out, err = waiting(wn, "0.1,1.5", delay=0.1)
        empties = [r for r in wn.ledger() if r.get("op") == "verify"]
        check("a wait that sees no reply ends when it should, recorded once (its empty reads are not attempts)",
              bool(cn) and rc != 0 and "before the wait ended" in err and len(empties) == 1, (en, rc, err, empties))
        wt = World(helper, base, "wrongthenright", slack)
        wt.t = T0 + 90_000
        ct, et = wt.ask([{"type": "apt-update"}], title="fat fingers")
        rc, out, err = waiting(wt, "0.2,20", then=["000000" if code_at(wt.t) != "000000" else "111111", code_at(wt.t)])
        check("after a wrong code with tries left, the wait keeps watching and takes Brad's next reply",
              bool(ct) and rc == 0 and "authorization" in out and "Still watching" in err, (et, rc, err))

        # ── Review fixes, 2026-11-04 ──
        wp = World(helper, base, "pinned", slack)
        wp.t = T0 + 100_000
        cp, ep = wp.ask([{"type": "apt-update"}], title="pinned A")
        box = {}
        th = threading.Thread(target=lambda: box.update(r=wp.call("verify-slack", "--wait",
                                                                  extra_env={"MOSES_MFA_WAIT": "3,20"})))
        th.start()
        time.sleep(1)
        wp.reply(code_at(wp.t))
        rc_o, out_o, err_o = wp.call("verify-slack")                     # another reader answers A first
        cb, eb = wp.ask([{"type": "apt-install", "packages": ["bee"]}], title="request B")
        th.join(40)
        rc, out, err = box.get("r", (-1, "", "never returned"))
        a_id = json.loads(out_o)["authorization"] if rc_o == 0 else "?"
        check("a wait stays on its own request: when another reader answered it, the wait reports that grant, "
              "and never judges a newer request", bool(cp) and bool(cb) and rc == 0 and a_id in out,
              (rc_o, err_o, eb, rc, out, err))
        we2 = World(helper, base, "edited", slack)
        we2.t = T0 + 110_000
        ce2, ee2 = we2.ask([{"type": "apt-update"}], title="edited reply")
        ts = we2.reply("one sec", at=we2.t + 10)
        m = next(x for x in slack.messages if x["ts"] == ts)
        m["text"], m["edited"] = code_at(we2.t + 100), {"user": OWNER, "ts": f"{int(we2.t + 100)}.000001"}
        rc, out, err = we2.call("verify-slack", t=we2.t + 110)
        check("a reply edited into the code is judged at the edit's stamp, not its first one",
              bool(ce2) and rc == 0, (ee2, rc, err))
        wb = World(helper, base, "before", slack)
        wb.t = T0 + 120_000
        cbf, ebf = wb.ask([{"type": "apt-update"}], title="stamped before")
        wb.reply(code_at(wb.t - 500), at=wb.t - 500)
        rc, out, err = wb.call("verify-slack", t=wb.t + 5)
        check("a reply stamped before the request opened is not judged at that earlier time",
              bool(cbf) and rc != 0 and "wrong code" in err, (ebf, rc, err))
        wlt = World(helper, base, "lasttry", slack)
        wlt.t = T0 + 130_000
        clt, elt = wlt.ask([{"type": "apt-update"}], title="three wrong")
        bad = "000000" if code_at(wlt.t) != "000000" else "111111"
        t_start = time.monotonic()
        rc, out, err = waiting(wlt, "0.2,20", then=[bad, bad, bad], delay=0.6)
        check("a wait ends at the last wrong try, at once, and says so",
              bool(clt) and rc != 0 and "last try" in err and time.monotonic() - t_start < 10, (elt, rc, err))
        wd = World(helper, base, "deadslack", slack)
        wd.t = T0 + 140_000
        cd, ed = wd.ask([{"type": "apt-update"}], title="slack goes away")
        rc, out, err = wd.call("verify-slack", "--wait", extra_env={"MOSES_MFA_WAIT": "0.1,10",
                                                                     "MOSES_MFA_SLACK_API": "http://127.0.0.1:9"})
        rows = [r for r in wd.ledger() if r.get("op") == "verify"]
        check("a wait that ends because Slack keeps failing leaves a ledger row naming the request",
              bool(cd) and rc == 2 and any(r.get("outcome") == "could-not-check" and r.get("challenge") for r in rows),
              (ed, rc, err, rows))
        wx = World(helper, base, "closedlate", slack)
        wx.t = T0 + 150_000
        cx, ex = wx.ask([{"type": "apt-update"}], title="too late")
        post = wx.post_ts()
        rc, out, err = wx.call("verify-slack", "--wait", t=wx.t + 400, extra_env={"MOSES_MFA_WAIT": "0.1,5"})
        check("when a request closes during a wait, Brad is told in its thread (never just silence)",
              bool(cx) and rc != 0 and any("⌛" in m["text"] for m in slack.thread_of(post)), (ex, rc, err))
        wk = World(helper, base, "killed", slack)
        wk.t = T0 + 160_000
        ck, ek = wk.ask([{"type": "apt-update"}], title="killed wait")
        pk = wk.call("verify-slack", "--wait", extra_env={"MOSES_MFA_WAIT": "0.2,30"}, popen=True)
        time.sleep(1.5)
        pk.terminate()
        pk.wait(10)
        rows = [r for r in wk.ledger() if r.get("op") == "verify"]
        check("a wait killed by its caller still leaves a ledger row",
              bool(ck) and any("ended by SystemExit" in r.get("detail", "") for r in rows), (ek, rows))

        # ── At most four task requests an hour ──
        wr = World(helper, base, "rate", slack)
        got = []
        for k in range(5):
            cr, er = wr.ask([{"type": "apt-install", "packages": [f"p{k}"]}])
            got.append(cr is not None)
            wr.t += 301                                                  # let it expire before the next
        check("a fifth task request within the hour is refused (nobody can keep Brad's channel busy)",
              got == [True, True, True, True, False], got)

        # ── One request at a time, and a caller cannot hog it ──
        w6 = World(helper, base, "slot", slack)
        w6.ask([{"type": "apt-update"}], title="first")
        c6, err6 = w6.ask([{"type": "apt-update"}], title="first")
        check("the same task cannot be re-posted while it is open (no hogging the one request)",
              c6 is None and "still open" in err6, err6)
        c6, err6 = w6.ask([{"type": "apt-install", "packages": ["x"]}], title="second")
        check("a different task cannot replace the open request", c6 is None and "still open" in err6, err6)
    finally:
        slack.srv.shutdown()
        shutil.rmtree(base, ignore_errors=True)
    return passed, failed


def listener_route() -> tuple[list, list]:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' — ' + str(detail)[:220]}")

    src = (HERE / "listener.py").read_text()
    tg = (HERE / "task_grant.py").read_text()
    i_task, i_new = src.find("task_grant.gate_open()"), src.find("new_build.answer(code")
    check("the listener sends a code typed in chat nowhere while a gate request is open, before anything else reads it",
          0 < i_task < i_new)
    check("task_grant.py never handles a code: no verify through stdin, only root reading its own thread",
          '_mfa("verify",' not in tg and '_mfa("verify-slack", "--wait"' in tg)
    return passed, failed


MUTANTS = [
    ("a wait forgets which request it was on", "rc = _verify_slack_once(quiet_empty=True, cid=cid)",
     "rc = _verify_slack_once(quiet_empty=True, cid=None)",
     "a wait stays on its own request: when another reader answered it, the wait reports that grant, and never judges a newer request"),
    ("a grant by another reader reported as a refusal", "                if a:\n                    done = True", "                if False:\n                    done = True",
     "a wait stays on its own request: when another reader answered it, the wait reports that grant, and never judges a newer request"),
    ("an edit's stamp ignored", 'given = (m.get("edited") or {}).get("ts") or m["ts"]', 'given = m["ts"]',
     "a reply edited into the code is judged at the edit's stamp, not its first one"),
    ("a reply's stamp may reach back before the request", 'code_t = max(min(float(code_t), t), float(c.get("opened", code_t)))',
     "code_t = min(float(code_t), t)", "a reply stamped before the request opened is not judged at that earlier time"),
    ("a wait swallows every refusal", "        except (NoReplyYet, WrongCodeTriesLeft) as e:\n                errors = 0",
     "        except Refused as e:\n                errors = 0", "a wait ends at the last wrong try, at once, and says so"),
    ("a Slack failure ends a wait unrecorded", 'record("verify", "could-not-check",', '(lambda *a, **k: None)("verify", "could-not-check",',
     "a wait that ends because Slack keeps failing leaves a ledger row naming the request"),
    ("a request closing mid-wait is silent in Slack", '                try:\n                    slack("chat.postMessage", {"channel": where["channel"]',
     '                try:\n                    raise CouldNotCheck("muted")\n                    slack("chat.postMessage", {"channel": where["channel"]',
     "when a request closes during a wait, Brad is told in its thread (never just silence)"),
    ("a killed wait leaves no row", "        if not done and not isinstance(e, (Refused, CouldNotCheck)):", "        if False:",
     "a wait killed by its caller still leaves a ledger row"),
    ("the code judged at read time again", "a = _check_code(s, c, code, g[\"SLACK_OWNER_ID\"], t, code_t=given)",
     "a = _check_code(s, c, code, g[\"SLACK_OWNER_ID\"], t)",
     "a code is judged by when Brad posted it (Slack's stamp), not by when root got around to reading it"),
    ("a reply's stamp not clamped to root's clock", "code_t = max(min(float(code_t), t), ", "code_t = max(float(code_t), ",
     "a reply stamped ahead of root's clock is not judged at that future time"),
    ("--wait reads once and gives up", "            time.sleep(WAIT_POLL)\n", "            raise\n",
     "verify-slack --wait picks up a reply Brad posts after it started, and grants"),
    ("a wait's empty reads recorded as attempts", "            if quiet_empty:                       # a wait's",
     "            if False:                       # a wait's",
     "a wait that sees no reply ends when it should, recorded once (its empty reads are not attempts)"),
    ("a wrong code ends the wait", "        except (NoReplyYet, WrongCodeTriesLeft) as e:", "        except (NoReplyYet,) as e:",
     "after a wrong code with tries left, the wait keeps watching and takes Brad's next reply"),
    ("the path walk restored to its pre-fix form (it hung on a real unit)",
     '            p = "/" + start.lstrip("/")\n            while not os.path.lexists(p) and os.path.dirname(p) != p:\n'
     '                p = os.path.dirname(p)\n            while True:\n                st = os.lstat(p)\n'
     '                if operator_can_change(st, p):\n                    return f"{p} can be changed by someone other than root"\n'
     '                parent = os.path.dirname(p)\n                if parent == p:\n                    break\n                p = parent\n',
     '            p = start\n            while not os.path.lexists(p) and p != "/":\n'
     '                p = os.path.dirname(p)\n            while True:\n                st = os.lstat(p)\n'
     '                if operator_can_change(st, p):\n                    return f"{p} can be changed by someone other than root"\n'
     '                if p == "/":\n                    break\n                p = os.path.dirname(p)\n',
     "a path written with a leading // is judged, not walked forever (a real unit hung the check)"),
    # (stdin for a gate request is now refused first by "the gate exists" — its mutant lives in chat_gate_test)
    ("a bot posting as Brad counted as Brad", 'not (m.get("user") == g["SLACK_OWNER_ID"] and not m.get("bot_id")\n',
     'not (m.get("user") == g["SLACK_OWNER_ID"]\n',
     "a message from a bot (even one posting with Brad's user id) in the thread closes the request, and nothing is granted"),
    ("other posters in the thread allowed", "        if intruders:\n", "        if False:\n",
     "a message from anyone but Brad in the thread closes the request, and nothing is granted"),
    ("destinations not limited", "or not any(r.match(dest) for r in DEST_RES)", "or False",
     "a file may not be installed into sudoers"),
    ("the Never list for units removed", "if NEVER_UNITS.match(unit):", "if False:", "the journal is on the Never list"),
    ("units not checked for operator-changeable code", 'def unit_why_unsafe(unit: str) -> str | None:\n',
     'def unit_why_unsafe(unit: str) -> str | None:\n    return None\n',
     "a root unit that runs a file the operator can change is refused"),
    ("package names loosened", 'PKG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9+.-]{0,61}[a-z0-9])?$")',
     'PKG_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,62}$")', "…nor a name ending in '-', which tells apt to REMOVE it"),
    ("files not required to be shown in full", 'def shown_in_full(data: bytes) -> str | None:\n',
     'def shown_in_full(data: bytes) -> str | None:\n    return data.decode("utf-8", "replace")\n',
     "a file Brad cannot read whole (over 60 lines) cannot be installed"),
    ("hidden characters allowed in titles", "            or HIDDEN.search(title)):", "            or False):",
     "a title with a reordering character is refused"),
    ("PATH shadowing allowed", "            if name in NEVER_NAMES or any(", "            if False and any(",
     "nothing may be installed under a system command's name (it would shadow it on PATH)"),
    ("Slack text not escaped", 'return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")', "return text",
     "a title or file text cannot ping the channel or hide a link behind a label"),
    ("a socket's write bit read as permission to replace it",
     "    if _stat.S_ISSOCK(st.st_mode):\n        return False", "    if False:\n        return False",
     "a socket the operator may connect to (mode 666) is not code they can change"),
    ("the Never list applied to dependencies too", "    if direct and NEVER_UNITS.match(real):", "    if NEVER_UNITS.match(real):",
     "a unit that merely pulls in the journal (as nearly all do) may still be restarted"),
    ("an alias walks past the Never list", "    if direct and NEVER_UNITS.match(real):\n        return", "    if False:\n        return",
     "an alias of a Never-list unit is refused by what it resolves to"),
    ("Exec lines skipped for non-root units",
     '    for k, v in vals.items():\n        if k.startswith("Exec"):\n            paths += re.findall',
     '    for k, v in vals.items():\n        if k.startswith("Exec") and vals.get("User", "") in ("", "root"):\n            paths += re.findall',
     "…and so is one that runs as another user (a `+` prefix would still run it as root)"),
    ("path= not checked", '            paths += re.findall(r"path=(/[^ ;]+)", v)\n', "",
     "the program that runs (path=) is checked, not just what argv[0] claims"),
    ("environment paths not checked", '            paths += [x for x in m.group(1).split(":") if x.startswith("/")]', "            pass",
     "a root unit whose environment points at an operator file (LD_PRELOAD=) is refused"),
    ("what a unit starts not checked", 'for dep_prop in ("Triggers", "Wants",', 'for dep_prop in () and ("Triggers", "Wants",',
     "starting a timer checks the service it triggers"),
    ("a closed task request leaves Brad's code live", "    s.d[\"last_counter\"] = max(s.d[\"last_counter\"], int(t // STEP) + WINDOW)",
     "    pass", "Brad's code from a closed task request cannot answer a new-build request"),
    ("only the resolved path is checked", "        for start in (os.path.abspath(path), os.path.realpath(path)):",
     "        for start in (os.path.realpath(path),):",
     "a symlink in a directory the operator can write is refused, even when it points somewhere safe"),
    ("missing argv paths skipped", "                    if tok.startswith(\"/\"):\n                        paths.append(tok)",
     "                    if tok.startswith(\"/\") and os.path.exists(tok):\n                        paths.append(tok)",
     "a script that does not exist yet, in a place the operator could create it, is refused"),
    ("OnFailure not followed", '"ConsistsOf", "OnFailure", "OnSuccess"):', '"ConsistsOf"):',
     "a unit started on failure (OnFailure=) is checked too"),
    ("Never units reachable through install-file", "            if NEVER_UNITS.match(named):", "            if False:",
     "no drop-in for a Never-list unit, either"),
    ("a deny-list of hidden characters again", r'HIDDEN = re.compile(r"[^\x20-\x7e\t\n]")',
     r'HIDDEN = re.compile(r"[\x00-\x08\u202a-\u202e]")',
     "only printable ASCII is shown: a line separator that is not counted as a line is refused"),
    ("no cap on the whole prompt", "    if len(slack_escape(out)) > PROMPT_MAX:", "    if False:",
     "a task too long to show whole is refused, not cut off"),
    ("apt may remove packages", '"install", "-y", "-q", "--no-remove",', '"install", "-y", "-q",',
     "action 2 installs exactly the named packages"),
    ("no rate limit on task requests", '            if len(s.d["task_asks"]) >= 4:', "            if False:",
     "a fifth task request within the hour is refused (nobody can keep Brad's channel busy)"),
    ("install-file reads the source again instead of the staged bytes",
     'data = staged_bytes(act["sha256"])\n    dest =', 'data = open(act["source"], "rb").read()\n    dest =',
     "install-file writes the bytes Brad was shown, not the source as it is now"),
    ("install-file follows a planted symlink", "    if os.path.islink(dest):\n        raise Refused(",
     "    if os.path.islink(dest):\n        open(os.path.realpath(dest), 'wb').write(data); return 0\n        raise Refused(",
     "a symlink planted at the destination is never followed (the victim is untouched)"),
    ("expiry ignored", 'if a["expires"] <= t:\n        raise Refused(f"grant', 'if False:\n        raise Refused(f"grant',
     "after its hours the grant refuses"),
    ("revocation ignored", 'if a.get("revoked"):\n        raise Refused', 'if False:\n        raise Refused', "a revoked grant refuses"),
    ("a task may be re-posted while open", 'if old["purpose"] != purpose or gated:', 'if old["purpose"] != purpose:',
     "the same task cannot be re-posted while it is open (no hogging the one request)"),
    ("the gate not required", "        g = gate_settings()                       # no gate, no task",
     "        g = {}                                    # no gate, no task",
     "with no Moses Gate set up, a task is not asked at all (could not check), and nothing is posted"),
    ("the child umask left at 077", "old = os.umask(0o022)", "old = os.umask(0o077)",
     "commands run with a normal umask (022), not the helper's 077"),
]


def main() -> int:
    print("task grants — typed actions, and root's own path to Brad's eyes (#17)")
    # Inside Knight's gate sandbox root's files show as owned by "nobody" (a user namespace maps only the
    # operator), so ownership checks cannot be judged there. moses-selftest and install-mfa.sh run this.
    if os.stat("/usr/bin/true").st_uid != 0:
        if os.environ.get("KNIGHT_SANDBOX"):
            print("  skip  the whole suite — root's files are not visible as root's inside the gate sandbox; "
                  "moses-selftest and install-mfa.sh run it outside")
            return 0
        print("  FAIL  /usr/bin/true is not owned by root here, so ownership checks cannot be tested")
        return 1
    passed, failed = suite(HELPER)
    print("\nthe listener")
    lp, lf = listener_route()
    passed += lp
    failed += lf
    print("\nwatch each guard fail — the helper broken on purpose, one guard at a time")
    src = HELPER.read_text()
    blind = []
    # In parallel: each run has its own fake Slack, scratch tree and helper copy, and most of a run is
    # waiting (real sleeps in the wait tests), so 48 runs one after another took ~17 minutes.
    from concurrent.futures import ThreadPoolExecutor
    with tempfile.TemporaryDirectory() as td:
        def run(i_m):
            i, (label, old, new, expect) = i_m
            if old not in src:
                return label, None, expect
            d = Path(td) / f"m{i}"
            d.mkdir()
            m = d / "moses-mfa"
            m.write_text(src.replace(old, new, 1))
            return label, expect in suite(m)[1], expect
        with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
            for label, ok, expect in pool.map(run, enumerate(MUTANTS)):
                if ok is None:
                    print(f"  FAIL {label} — the sabotage no longer applies (update the mutant)")
                else:
                    print(f"  {'ok  ' if ok else 'FAIL'} {label} → '{expect}' goes red")
                if not ok:
                    blind.append(label)
    total = len(passed) + len(failed)
    print(f"\n{len(passed)}/{total} checks pass; {len(MUTANTS) - len(blind)}/{len(MUTANTS)} guards watched failing")
    return 0 if not failed and not blind else 1


if __name__ == "__main__":
    sys.exit(main())
