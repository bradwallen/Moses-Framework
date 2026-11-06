#!/usr/bin/env python3
"""Chat to root through the Moses Gate (#17), end to end: the real listener (model and Slack stubbed),
the real helper unprivileged, and a fake Slack that plays the gate's channel and Brad's replies.

  - Brad asks in plain words → Moses declares `DO: task …` → root posts in the gate's channel → Brad's
    code in THAT thread → the actions run → the grant is revoked → Moses says what happened.
  - Only the owner can ask; a failing action stops the rest; a code typed in chat is handed to nothing.
  - A new build's prompt is posted by root too, and its code is read from the gate's thread, never stdin.

Then each guard is broken on purpose, in a copy of agent/, and must turn its check red.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
REAL_AGENT = Path(os.environ.get("CHAT_GATE_REAL_AGENT", str(HERE)))
sys.path.insert(0, str(HERE))
import task_grant_test as TG  # noqa: E402 — its fake Slack and scratch world

TMP = Path(tempfile.mkdtemp(dir=os.path.expanduser("~"), prefix=".chat-gate-test-"))
os.chmod(TMP, 0o700)
os.umask(0o022)
SLACK = TG.FakeSlack()
W = TG.World(HERE / "moses-mfa", TMP, "world", SLACK)
W.t = TG.T0 + 7_000
for u in ("backup.timer", "fail.service"):
    W.unit(u)
REG = TMP / "projects.json"
REG.write_text(json.dumps({"projects": [
    {"id": "example-app", "name": "Listings finder", "rank": 13, "status": "active", "stage": "scope",
     "kind": "internal", "milestones": [], "ideas": [], "scope": "Aggregate GIS internships."}]}))
(TMP / "targets").mkdir()
(TMP / "fake-new-target").write_text("#!/bin/sh\necho \"BUILT $1\"\n")
(TMP / "fake-new-target").chmod(0o755)
os.environ.update({
    "SLACK_APP_TOKEN": "xapp-test", "SLACK_BOT_TOKEN": "xoxb-test",
    "MOSES_STATE": str(TMP / "state"), "MOSES_CHAT_CHANNELS": "C_CHAT", "MOSES_COALESCE_WINDOW": "0",
    "MOSES_PUBLIC_BASE": "https://example.com",
    "KNIGHT_JOBS": str(TMP / "knight-jobs"), "ZRYACHIY_JOBS": str(TMP / "zryachiy-jobs"),
    "MOSES_REGISTRY": str(REG), "KNIGHT_TARGETS": str(TMP / "targets"),
    "MOSES_MCP_DIR": str(REAL_AGENT.parent / "mcp"),
    "MOSES_NEW_TARGET": str(TMP / "fake-new-target"),
    "MOSES_MFA_CMD": f"{sys.executable} -I {HERE / 'moses-mfa'}",
    "MOSES_MFA_SEED": str(W.dir / "etc" / "totp.seed"), "MOSES_MFA_STATE": str(W.dir / "state"),
    "MOSES_MFA_NOW": str(W.t), "MOSES_MFA_GATE_ENV": str(W.gate), "MOSES_MFA_SLACK_API": SLACK.url,
    "MOSES_MFA_SYSTEMCTL": str(W.dir / "bin" / "systemctl"), "MOSES_MFA_APT_GET": str(W.dir / "bin" / "apt-get"),
    "MOSES_MFA_DEST_PREFIX": str(W.dir / "dest"), "MOSES_MFA_WAIT": "0.2,15",
})

import listener      # noqa: E402

CHAN, BOT, BRAD, JON = "C_CHAT", "U_MOSES", TG.OWNER, "U_JON"
listener.BOT_USER_ID, listener.BOT_ID, listener.OWNER_ID = BOT, "B_MOSES", BRAD
listener.scout.check_target = lambda *a, **k: None
listener._spawn = lambda fn, *a: fn(*a)
posted: list = []


class FakeWeb:
    def chat_postMessage(self, **kw):
        posted.append(kw.get("text") or "")
        return {"ok": True, "ts": f"{time.time():.6f}"}

    def conversations_history(self, channel, limit=14):
        return {"messages": []}

    def conversations_replies(self, channel, ts, limit=14):
        return {"messages": []}

    def reactions_add(self, **kw):
        return {"ok": True}


listener.web = FakeWeb()


class FakeSM:
    def send_socket_mode_response(self, *a, **k):
        pass


def stub_model(text: str):
    listener.conversation.reply = lambda hist, bot_user_id, channel, **kw: ("text", text, 0.01)


N = [0]


def fire(user: str, text: str, answer=None) -> list[str]:
    """Deliver one message. `answer(gate_post)` plays Brad in the gate's thread once root has posted."""
    posted.clear()
    N[0] += 1
    before = len([m for m in SLACK.messages if m.get("bot_id") and not m.get("thread_ts")])
    ev = {"type": "message", "channel": CHAN, "ts": f"{1_900_000_000 + N[0]}.0001", "user": user, "text": text}
    th = threading.Thread(target=lambda: listener.handle(
        types.SimpleNamespace(type="events_api", envelope_id="e", payload={"event": ev}), FakeSM()))
    th.start()
    if answer:
        for _ in range(100):
            roots = [m for m in SLACK.messages if m.get("bot_id") and not m.get("thread_ts")]
            if len(roots) > before:
                answer(roots[-1])
                break
            time.sleep(0.1)
    th.join(60)
    return list(posted)


def brad_answers(code_text=None):
    return lambda post: SLACK.add(TG.CHAN, code_text or TG.code_at(W.t), user=BRAD, thread=post["ts"])


def gate_posts(needle: str) -> list[dict]:
    return [m for m in SLACK.messages if m.get("bot_id") and not m.get("thread_ts") and needle in m["text"]]


def mfa(*args, stdin=""):
    r = subprocess.run([sys.executable, "-I", str(HERE / "moses-mfa"), *args], input=stdin,
                       capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout, r.stderr


passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' — ' + str(detail)[:300]}", flush=True)


def tools() -> list[str]:
    return W.tools()


def main_checks():
    # ── Brad asks in plain words; the task runs behind his code, and nothing stays open ──
    stub_model("On it.\nDO: task Restart the backup timer | restart backup.timer")
    out = fire(BRAD, "moses, restart the backup timer", answer=brad_answers())
    said = "\n".join(out)
    rc, g_out, _ = mfa("grants")
    check("Brad asks in chat → root posts the task in the gate → his code there → it runs, and Moses says so",
          any(p.startswith("🔐") for p in out) and "✅ Done. Restarted backup.timer." in said
          and "systemctl restart -- backup.timer" in tools(), (out, tools()))
    check("a chat task's grant is revoked as soon as its actions ran (nothing stays open)",
          "✅ Done" in said and json.loads(g_out or "[]") == [], (said, g_out))
    check("the task's progress is said as it happens, not only at the end",
          sum(1 for p in out if p.startswith("🔐") or p.startswith("✅ Done")) == 2 and len(out) >= 3, out)

    # ── Only the owner ──
    W.t += 120
    os.environ["MOSES_MFA_NOW"] = str(W.t)
    n_posts = len(gate_posts("Root work, needs your OK"))
    stub_model("Sure.\nDO: task Restart it | restart backup.timer")
    out = fire(JON, "moses restart the backup timer")
    check("only the owner can ask for root work from chat; nothing is posted in the gate",
          any("Only the owner can ask for root work" in p for p in out)
          and len(gate_posts("Root work, needs your OK")) == n_posts, out)

    # ── A failing action stops the rest ──
    W.t += 120
    os.environ["MOSES_MFA_NOW"] = str(W.t)
    before = len(tools())
    stub_model("On it.\nDO: task Two things | restart fail.service; restart backup.timer")
    out = fire(BRAD, "restart those two", answer=brad_answers())
    said = "\n".join(out)
    after = tools()[before:]
    check("when an action fails, the rest do not run, Moses says so, and the grant is still revoked",
          "❌ Restarting fail.service failed" in said and "the last one didn't run" in said
          and json.loads(mfa("grants")[1] or "[]") == [] and not any("backup.timer" in x for x in after), (said, after))

    # ── What chat cannot ask for ──
    n_posts = len(gate_posts("Root work, needs your OK"))
    stub_model("On it.\nDO: task Put a file | install-file /tmp/a:/usr/local/bin/a")
    out = fire(BRAD, "install this script")
    check("install-file cannot be asked for from chat (it needs a session to stage the file); nothing is posted",
          any("couldn't turn that into something" in p for p in out)
          and len(gate_posts("Root work, needs your OK")) == n_posts, out)

    # ── A code typed in chat is handed to nothing ──
    W.t += 400
    os.environ["MOSES_MFA_NOW"] = str(W.t)
    rc, out_c, err_c = mfa("challenge", "task", stdin=json.dumps({"title": "open one", "actions": [{"type": "apt-update"}]}))
    rows_before = [r for r in W.ledger() if r.get("op") == "verify"]
    out = fire(BRAD, TG.code_at(W.t))
    rows_after = [r for r in W.ledger() if r.get("op") == "verify"]
    check("a code Brad types in chat while a gate request is open is not used, and he is told where it counts",
          rc == 0 and any("didn't count" in p for p in out) and rows_after == rows_before, (err_c, out, rows_after[-2:]))

    # ── A new build: root posts the prompt, and reads the code from its thread ──
    W.t += 400
    os.environ["MOSES_MFA_NOW"] = str(W.t)
    stub_model("Asking.\nDO: new-build 13")
    out = fire(BRAD, "build #13", answer=brad_answers())
    said = "\n".join(out)
    check("a new build's prompt is posted by root with the Moses Gate, and his code there starts the build",
          len(gate_posts("New build for")) >= 1 and "🔐 New build for" in said
          and "✅ Got it" in said and "BUILT example-app" in said, (said, [m["text"][:80] for m in SLACK.messages[-4:]]))
    W.t += 3700                                       # past the hour: at most 4 gate requests in one
    os.environ["MOSES_MFA_NOW"] = str(W.t)
    rc, out_c, err_c = mfa("challenge", "new-build", "example-app")
    cid = json.loads(out_c)["challenge"] if rc == 0 else ""
    rc2, _, err2 = mfa("verify", cid, stdin=TG.code_at(W.t))
    check("a new build's code handed in on stdin is refused once root posted its prompt, even the right one",
          rc == 0 and rc2 != 0 and "the Moses Gate is set up" in err2, (err_c, rc2, err2))
    # ── Review fixes, 2026-11-04 (second review) ──
    import conversation
    check("a DO line inside a fenced code block is quoted text, and is not dispatched",
          conversation.extract_actions("Here it is:\n```\nDO: task X | restart backup.timer\n```")[1] == []
          and conversation.extract_actions("On it.\nDO: task X | restart backup.timer")[1] != [])
    # The MCP server needs the MCP SDK, which lives in its own venv: call the real tool through it.
    mcp_py = os.environ.get("MOSES_MCP_PYTHON", os.path.expanduser("~/Projects/moses/mcp/venv/bin/python"))
    n_posts = len(gate_posts("New build for"))
    r = subprocess.run([mcp_py, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import mcp_server; "
                        "print(mcp_server.new_build('13'))", os.environ["MOSES_MCP_DIR"]],
                       capture_output=True, text=True, timeout=120)
    said = (r.stdout + r.stderr)[-400:]
    ok = "go through the Moses Gate" in r.stdout and len(gate_posts("New build for")) == n_posts
    check("the MCP new_build tool refuses where the gate is set up, before anything is posted or spent", ok, said)

    W.t += 3700
    os.environ["MOSES_MFA_NOW"] = str(W.t)
    task_posts = gate_posts("Root work, needs your OK")
    stub_model("On it.\nDO: task Restart the backup timer | restart backup.timer")
    fire(BRAD, "moses, restart the backup timer once more", answer=brad_answers())
    newest = gate_posts("Root work, needs your OK")[-1]["text"] if len(gate_posts("Root work, needs your OK")) > len(task_posts) else ""
    check("Brad reads what root will run before the asker's title, and the title is labeled as the asker's",
          0 < newest.find("1. ") < newest.find("What they called it (their words, unchecked)"), newest[:300])

    W.t += 3700
    os.environ["MOSES_MFA_NOW"] = str(W.t)
    mfa("challenge", "new-build", "example-app")
    W.t += 400                                                     # it expires unread
    os.environ["MOSES_MFA_NOW"] = str(W.t)
    mfa("verify-slack")
    rc, out_c, err_c = mfa("challenge", "new-build", "example-app")
    post = gate_posts("New build for")[-1]
    SLACK.add(TG.CHAN, TG.code_at(W.t), user=BRAD, thread=post["ts"])
    rc2, out2, err2 = mfa("verify-slack")
    check("right after a request expires, Brad's code on the retry is accepted (not 'already used')",
          rc == 0 and rc2 == 0, (err_c, rc2, err2))

    W.t += 3700
    os.environ["MOSES_MFA_NOW"] = str(W.t)
    got = []
    for k in range(5):
        rc, _, e = mfa("challenge", "new-build", "example-app")
        got.append(rc == 0)
        W.t += 301
        os.environ["MOSES_MFA_NOW"] = str(W.t)
    check("new builds count toward the gate's 4 requests an hour, like tasks", got == [True, True, True, True, False], got)

    W.t += 3700
    os.environ["MOSES_MFA_NOW"] = str(W.t)
    bad = TMP / "bad-gate.env"
    bad.write_text("SLACK_GATE_CHANNEL=C1\nSLACK_OWNER_ID=U1\n")
    os.environ["MOSES_MFA_GATE_ENV"] = str(bad)
    rc, out_c, err_c = mfa("challenge", "new-build", "example-app")
    os.environ["MOSES_MFA_GATE_ENV"] = str(W.gate)
    check("a gate settings file that exists but is broken fails closed (it never falls back to stdin)",
          rc == 2 and "SLACK_GATE_TOKEN" in err_c, (rc, out_c, err_c))

    import new_build
    import task_grant
    W.t += 3700
    os.environ["MOSES_MFA_NOW"] = str(W.t)
    rc, out_c, err_c = mfa("challenge", "task", stdin=json.dumps({"title": "someone else", "actions": [{"type": "apt-update"}]}))
    lines = []
    th = threading.Thread(target=lambda: lines.extend(new_build._through_gate(
        "example-app", "Listings finder", "new-build:example-app", "", "scope", "")))
    th.start()
    time.sleep(1)
    SLACK.add(TG.CHAN, TG.code_at(W.t), user=BRAD, thread=gate_posts("Root work, needs your OK")[-1]["ts"])
    th.join(60)
    check("a new build never builds on a grant that came back for another request",
          rc == 0 and any("went to a different request" in x for x in lines) and not any("BUILT" in x for x in lines), (err_c, lines))

    W.t += 3700
    os.environ["MOSES_MFA_NOW"] = str(W.t)
    real = task_grant._mfa

    def lying(*a, **k):
        rc, out, err = real(*a, **k)
        if a[:2] == ("challenge", "task") and rc == 0:
            d = json.loads(out)
            d["purpose"] = "task:000000000000"
            out = json.dumps(d)
        return rc, out, err
    task_grant._mfa = lying
    lines = []
    before = len(tools())
    th = threading.Thread(target=lambda: lines.extend(task_grant.chat("Mine | restart backup.timer")))
    th.start()
    for _ in range(50):
        if len(gate_posts("Root work, needs your OK")) and "Mine" in gate_posts("Root work, needs your OK")[-1]["text"]:
            break
        time.sleep(0.1)
    SLACK.add(TG.CHAN, TG.code_at(W.t), user=BRAD, thread=gate_posts("Root work, needs your OK")[-1]["ts"])
    th.join(60)
    task_grant._mfa = real
    rc_g, g_out, _ = mfa("grants")
    check("a chat task never runs or revokes a grant that came back for another request",
          any("went to a different request" in x for x in lines) and len(tools()) == before and json.loads(g_out or "[]") != [],
          (lines, tools()[before:], g_out))



MUTANTS = [
    ("../mcp/mcp_server.py", "the MCP opens gate requests it cannot wait on", "        if nb.gate_configured():", "        if False:",
     "the MCP new_build tool refuses where the gate is set up, before anything is posted or spent"),
    ("conversation.py", "a fenced DO line dispatched", '    unfenced = re.sub(r"```.*?(?:```|\\Z)", "", text or "", flags=re.S)', '    unfenced = text or ""',
     "a DO line inside a fenced code block is quoted text, and is not dispatched"),
    ("moses-mfa", "the asker's title shown first", '''    lines.append(f"What they called it (their words, unchecked): {spec['title']}")''',
     '''    lines.insert(0, f"What they called it (their words, unchecked): {spec['title']}")''',
     "Brad reads what root will run before the asker's title, and the title is labeled as the asker's"),
    ("moses-mfa", "an expired request spends Brad's code", "        s.save()\n        # The code is not even compared",
     "        _spend_window(s, t)\n        s.save()\n        # The code is not even compared",
     "right after a request expires, Brad's code on the retry is accepted (not 'already used')"),
    ("moses-mfa", "new builds outside the hourly limit", '        if gated:\n            s.d["task_asks"] = [x', '        if spec:\n            s.d["task_asks"] = [x',
     "new builds count toward the gate's 4 requests an hour, like tasks"),
    ("moses-mfa", "a broken gate file falls back", "        g = gate_settings() if os.path.exists(GATE_ENV) else None",
     "        try:\n            g = gate_settings()\n        except CouldNotCheck:\n            g = None",
     "a gate settings file that exists but is broken fails closed (it never falls back to stdin)"),
    ("moses-mfa", "stdin allowed while the gate exists", "    if os.path.exists(GATE_ENV):\n        # With a gate, every code",
     "    if False:\n        # With a gate, every code",
     "a new build's code handed in on stdin is refused once root posted its prompt, even the right one"),
    ("new_build.py", "a new build on someone else's grant", "    if a.get(\"authorized\") != purpose:", "    if False:",
     "a new build never builds on a grant that came back for another request"),
    ("task_grant.py", "a chat task on someone else's grant", '    if got.get("authorized") != c.get("purpose"):', "    if False:",
     "a chat task never runs or revokes a grant that came back for another request"),
    ("listener.py", "chat tasks open to anyone", '''                if event.get("user") != OWNER_ID:
                    note += "\\n_Only the owner can ask for root work. Nothing was asked._"''',
     '''                if False:
                    note += "\\n_Only the owner can ask for root work. Nothing was asked._"''',
     "only the owner can ask for root work from chat; nothing is posted in the gate"),
    ("listener.py", "a code in chat handed on", "        if code and task_grant.gate_open():", "        if False:",
     "a code Brad types in chat while a gate request is open is not used, and he is told where it counts"),
    ("listener.py", "progress said only as one blob", "        if isinstance(got, str) or got is None:",
     "        if True:\n            got = '\\n'.join(got) if not isinstance(got, (str, type(None))) else got",
     "the task's progress is said as it happens, not only at the end"),
    ("task_grant.py", "the grant left open", '    rrc, _, rerr = _mfa("revoke", grant)', "    rrc, rerr = 1, 'skipped'",
     "a chat task's grant is revoked as soon as its actions ran (nothing stays open)"),
    ("task_grant.py", "a failure does not stop the rest", "            break                                  # the rest may",
     "            pass                                   # the rest may",
     "when an action fails, the rest do not run, Moses says so, and the grant is still revoked"),
    ("task_grant.py", "chat may ask for any action shape", '            raise ValueError(f"`{part.strip()}` is not an action I can ask for from chat")',
     '            acts.append({"type": verb, "raw": w[1:]})',
     "install-file cannot be asked for from chat (it needs a session to stage the file); nothing is posted"),
    ("moses-mfa", "new builds not posted through the gate", "    gated = bool(spec) or g is not None", "    gated = bool(spec)",
     "a new build's prompt is posted by root with the Moses Gate, and his code there starts the build"),
]


def watch_fail() -> list[str]:
    blind = []
    from concurrent.futures import ThreadPoolExecutor

    def run(i_m):
        i, (fname, label, old, new, expect) = i_m
        src = (HERE / fname).read_text()
        if old not in src:
            return label, None, expect
        d = Path(tempfile.mkdtemp(dir=os.path.expanduser("~"), prefix=".chat-gate-mut-"))
        try:
            os.chmod(d, 0o700)
            shutil.copytree(HERE, d / "agent", ignore=shutil.ignore_patterns("__pycache__", ".venv"))
            shutil.copytree(HERE.parent / "mcp", d / "mcp", ignore=shutil.ignore_patterns("__pycache__", ".venv"))
            (d / "agent" / fname).write_text(src.replace(old, new, 1))
            env = {k: v for k, v in os.environ.items() if not k.startswith(("MOSES_", "KNIGHT_", "ZRYACHIY_"))}
            env.update(CHAT_GATE_CHILD="1")
            r = subprocess.run([sys.executable, str(d / "agent" / "chat_gate_test.py")], capture_output=True,
                               text=True, timeout=600, env=env)
            return label, f"FAIL {expect}" in r.stdout, expect
        finally:
            shutil.rmtree(d, ignore_errors=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for label, ok, expect in pool.map(run, enumerate(MUTANTS)):
            if ok is None:
                print(f"  FAIL {label} — the sabotage no longer applies (update the mutant)")
            else:
                print(f"  {'ok  ' if ok else 'FAIL'} {label} → '{expect}' goes red")
            if not ok:
                blind.append(label)
    return blind


if __name__ == "__main__":
    try:
        main_checks()
    finally:
        SLACK.srv.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)
    if os.environ.get("CHAT_GATE_CHILD"):
        sys.exit(0)
    print("\nwatch each guard fail — broken on purpose, one at a time")
    blind = watch_fail()
    print(f"\n{len(passed)}/{len(passed) + len(failed)} checks pass; "
          f"{len(MUTANTS) - len(blind)}/{len(MUTANTS)} guards watched failing")
    sys.exit(1 if failed or blind else 0)
