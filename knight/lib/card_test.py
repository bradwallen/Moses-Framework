#!/usr/bin/env python3
"""card.py — one Slack message per build run, edited in place. Driven against a fake Slack API.

    python3 knight/lib/card_test.py

Brad, 2026-10-17: too many messages from too many agents, each a black box until the next. The card is
the fix, so what is under test is that a run produces ONE message that moves forward: posted once,
edited after that, moved once into the conversation that asked, shared by the run's later jobs, and a
ping (a reply, because edits notify nobody) only when asked for. Then each guard is removed from a copy
of card.py and the check it protects must go red.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
CARD = HERE / "card.py"
calls: list[tuple[str, dict]] = []
FAIL = {"on": False}
REFUSE: set[str] = set()      # channels whose chat.postMessage answers ok:false (not_in_channel)


class Slack(BaseHTTPRequestHandler):
    def do_POST(self):
        method = self.path.rsplit("/", 1)[-1]
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        calls.append((method, body))
        if FAIL["on"]:
            self.send_response(500); self.end_headers(); return
        if method == "chat.postMessage" and body.get("channel") in REFUSE:
            out = {"ok": False, "error": "not_in_channel"}
        else:
            out = {"ok": True, "channel": body.get("channel"), "ts": f"{len(calls)}.000"}
        data = json.dumps(out).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def log_message(self, *a):
        pass


def suite(card: Path, verbose: bool):
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        if verbose:
            print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' — ' + str(detail)[:300]}")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Slack)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        envf = tmp / "knight.env"
        envf.write_text("SLACK_BOT_TOKEN=xoxb-test\nSLACK_KNIGHT_CHANNEL=C_KNIGHT\n")
        jobs = tmp / "jobs"
        first, second = jobs / "J1", jobs / "J2"
        for d in (first, second):
            d.mkdir(parents=True)
            (d / "target").write_text("example-app\n")
        (first / "task.txt").write_text("Build the listings finder\nmore detail\n")
        (second / "run-of").write_text("J1\n")
        env = {**{k: v for k, v in os.environ.items() if k != "KNIGHT_SLACK_CHANNEL"},
               "SLACK_API_BASE": f"http://127.0.0.1:{server.server_port}", "KNIGHT_SLACK_ENV": str(envf)}

        def run(job, *args, **extra):
            return subprocess.run([sys.executable, str(card), str(job), *args], capture_output=True, text=True,
                                  env={**env, **extra}, timeout=30)

        def state():
            try:
                return json.loads((first / "card.json").read_text())
            except (OSError, ValueError):
                return {}

        def text(i=0):
            return calls[i][1].get("text", "") if len(calls) > i else ""

        calls.clear()
        run(first, "building", "Job J1 started.", "--next", "Checks run when he finishes.")
        check("the first step posts one card, in Knight's channel when no conversation is known",
              [m for m, _ in calls] == ["chat.postMessage"] and calls[0][1]["channel"] == "C_KNIGHT", calls)
        check("the card says what is happening now and what happens next",
              "*Now:* 🔨 Building" in text() and "*Next:* Checks run" in text()
              and "Build the listings finder" in text(), text())
        ts = state().get("ts")

        calls.clear()
        run(first, "checking", "Knight finished; running the checks.")
        check("later steps EDIT the same message, they do not post new ones",
              [m for m, _ in calls] == ["chat.update"] and calls[0][1].get("ts") == ts, calls)
        check("and the card keeps its history", "Job J1 started." in text() and "running the checks" in text(), text())

        # Moses records the asking conversation after his reply; the next step moves the card there, once.
        (first / "notify.json").write_text(json.dumps({"channel": "C_ASK", "thread_ts": "9.9", "user": "U_BRAD"}))
        calls.clear()
        run(first, "reviewing", "Review: clean.")
        methods = [m for m, _ in calls]
        check("once the asking conversation is known, the card moves there — posted in the thread, THEN deleted in Knight's channel",
              methods == ["chat.postMessage", "chat.delete"] and calls[1][1].get("channel") == "C_KNIGHT"
              and calls[0][1].get("channel") == "C_ASK" and calls[0][1].get("thread_ts") == "9.9", calls)
        calls.clear()
        run(first, "landing", "Landing.")
        check("and after moving, it is edited there — never moved twice",
              [m for m, _ in calls] == ["chat.update"] and calls[0][1].get("channel") == "C_ASK", calls)

        calls.clear()
        run(second, "building", "Follow-up J2 started.")
        check("a later job in the run edits the run's card, not a new one",
              [m for m, _ in calls] == ["chat.update"] and calls[0][1].get("ts") == state().get("ts")
              and not (second / "card.json").exists(), calls)

        calls.clear()
        run(first, "landed", "J1 landed by itself.")
        check("progress pings nobody", all(m == "chat.update" for m, _ in calls), calls)
        calls.clear()
        report = tmp / "report.txt"; report.write_text("*What shipped* — the page.")
        run(first, "done", "Scope covered.", "--ping", "--reply-file", str(report))
        replies = [b for m, b in calls if m == "chat.postMessage"]
        check("a final step pings Brad by name in a reply under the card, and puts the report there",
              len(replies) == 2 and all(b.get("thread_ts") == "9.9" for b in replies)
              and any(b.get("text", "").startswith("<@U_BRAD>") for b in replies)
              and any("What shipped" in b.get("text", "") for b in replies), replies)

        log = (first / "run.log").read_text() if (first / "run.log").exists() else ""
        check("the run keeps ONE log of every step, of every job in it, in order (#6)",
              log.index("Job J1 started.") < log.index("Follow-up J2 started.") < log.index("Scope covered.")
              if all(x in log for x in ("Job J1 started.", "Follow-up J2 started.", "Scope covered.")) else False, log)
        check("and each line says which job it came from", "  J2  " in log and "  J1  " in log, log)

        calls.clear()
        r = run(first, "done", "x", KNIGHT_SLACK_CHANNEL="")
        check("with KNIGHT_SLACK_CHANNEL empty (the suites) nothing is posted, not even to the asking conversation",
              calls == [], calls)

        # A conversation that refuses the card (Knight is not in it, or the thread is gone).
        third = jobs / "J3"; third.mkdir(); (third / "target").write_text("x\n")
        calls.clear()
        run(third, "building", "Started.")
        (third / "notify.json").write_text(json.dumps({"channel": "C_SHUT", "thread_ts": "1.1"}))
        REFUSE.add("C_SHUT")
        calls.clear()
        r3 = run(third, "checking", "Checks.")
        check("a move the conversation refuses leaves the card where it was, still edited — never deleted",
              "chat.delete" not in [m for m, _ in calls] and [m for m, _ in calls][-1] == "chat.update", calls)
        fourth = jobs / "J4"; fourth.mkdir(); (fourth / "target").write_text("x\n")
        (fourth / "notify.json").write_text(json.dumps({"channel": "C_SHUT", "thread_ts": "1.1"}))
        calls.clear()
        r4 = run(fourth, "building", "Started.")
        check("a card the conversation refuses is posted in Knight's channel instead",
              any(m == "chat.postMessage" and b.get("channel") == "C_KNIGHT" for m, b in calls)
              and json.loads((fourth / "card.json").read_text()).get("channel") == "C_KNIGHT", calls)
        check("and the refusal is written down, with a nonzero exit so a caller's own fallback runs",
              r4.returncode == 1 and "not_in_channel" in (fourth / "card-error.txt").read_text(), r4.returncode)
        REFUSE.clear()

        # Asked from a THREAD in Knight's own channel: the card belongs in that thread, not beside it.
        fifth = jobs / "J5"; fifth.mkdir(); (fifth / "target").write_text("x\n")
        run(fifth, "building", "Started.")
        (fifth / "notify.json").write_text(json.dumps({"channel": "C_KNIGHT", "thread_ts": "5.5"}))
        calls.clear()
        run(fifth, "checking", "Checks.")
        check("asked from a thread in Knight's channel, the card moves into that thread",
              any(m == "chat.postMessage" and b.get("thread_ts") == "5.5" for m, b in calls)
              and "chat.delete" in [m for m, _ in calls], calls)

        calls.clear()
        FAIL["on"] = True
        (first / "card.json").unlink()
        (first / "notify.json").unlink()
        r = run(first, "building", "x")
        r2 = run(first, "building", "y")
        FAIL["on"] = False
        errs = (first / "card-error.txt").read_text() if (first / "card-error.txt").exists() else ""
        check("Slack failing never crashes the step: status 1, no traceback, and every failure is kept",
              r.returncode == 1 and "Traceback" not in r.stderr and errs.count("building:") >= 2, (r.returncode, r.stderr, errs))
    server.shutdown()
    return passed, failed


MUTANTS = [
    ("the suites post", 'os.environ["KNIGHT_SLACK_CHANNEL"]):\n        return 0', 'os.environ["KNIGHT_SLACK_CHANNEL"]):\n        pass',
     "with KNIGHT_SLACK_CHANNEL empty (the suites) nothing is posted, not even to the asking conversation"),
    ("every step posts anew", '        if text and card.get("ts") and card.get("channel"):\n            r = _call("chat.update"',
     '        if False:\n            r = _call("chat.update"', "later steps EDIT the same message, they do not post new ones"),
    ("the run is ignored", '        origin = (job_dir / "run-of").read_text().strip()', '        origin = ""',
     "a later job in the run edits the run's card, not a new one"),
    ("never moves", 'and not card.get("moved") \\', 'and False \\',
     "once the asking conversation is known, the card moves there — posted in the thread, THEN deleted in Knight's channel"),
    ("delete before the new copy exists", '            if r.get("ok"):\n                try:\n                    _call("chat.delete"',
     '            if True:\n                try:\n                    _call("chat.delete"',
     "a move the conversation refuses leaves the card where it was, still edited — never deleted"),
    ("no fallback channel", 'if not r.get("ok") and home_chan and', 'if False and',
     "a card the conversation refuses is posted in Knight's channel instead"),
    ("channel only, not thread", '(card.get("channel"), card.get("thread", "")) != (chan, thread)', 'card.get("channel") != chan',
     "asked from a thread in Knight's channel, the card moves into that thread"),
    ("no run log", 'with open(rd / "run.log", "a") as f:', 'with open(os.devnull, "a") as f:',
     "the run keeps ONE log of every step, of every job in it, in order (#6)"),
    ("errors overwritten", 'open(job_dir / "card-error.txt", "a")', 'open(job_dir / "card-error.txt", "w")',
     "Slack failing never crashes the step: status 1, no traceback, and every failure is kept"),
]

if __name__ == "__main__":
    print("THE STATUS CARD")
    passed, failed = suite(CARD, True)
    print(f"\n  {len(passed)}/{len(passed) + len(failed)} pass\n\nWATCH EACH GUARD FAIL")
    src, blind = CARD.read_text(), []
    with tempfile.TemporaryDirectory() as md:
        for name, old, new, expect in MUTANTS:
            if src.count(old) != 1:
                print(f"  FAIL {name}: the sabotage no longer applies"); blind.append(name); continue
            m = Path(md) / "card.py"; m.write_text(src.replace(old, new))
            red = expect in suite(m, False)[1]
            print(f"  {'ok  ' if red else 'FAIL'} {name} → '{expect}' goes red")
            if not red:
                blind.append(name)
    if failed or blind:
        print(f"\nFAILED: {len(failed)} check(s), {len(blind)} guard(s) not watched failing"); sys.exit(1)
    print(f"\nall {len(passed)} pass, and every guard was watched failing")
