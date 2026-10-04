#!/usr/bin/env python3
"""standup_post_test — the standup's Slack post leaves a record, and a post that failed is swept up later.

    ~/moses-venv/bin/python agent/standup_post_test.py

WHY (2026-10-03): the roster showed the morning's standup had reported, Slack never got it, and nothing
readable said why — the post was a bare curl whose outcome went to stderr. These run the real
agent/moses end to end against a throwaway state dir and a stand-in curl first on PATH, so nothing
reaches slack.com and the live /var/lib/moses is never touched.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOSES = HERE / "moses"
fails = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  —  {detail}" if detail and not cond else ""))
    if not cond:
        fails += 1


# Stand-in for curl with the contract post_job relies on: the body goes to -o, the status to stdout
# via -w, and a transport failure is a non-zero exit with curl's own wording on stderr.
FAKE_CURL = """#!/usr/bin/env bash
out=/dev/null; data=""
while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift ;; --data) data=$2; shift ;; esac; shift; done
jq -c . <<<"$data" >> "$FAKE_CURL_LOG"
if [ "${FAKE_CURL_RC:-0}" != 0 ]; then
  echo "curl: (6) Could not resolve host: slack.com" >&2; printf 000; exit "$FAKE_CURL_RC"; fi
printf '%s' "$FAKE_CURL_BODY" > "$out"; printf '%s' "$FAKE_CURL_CODE"
"""

OK = ("200", '{"ok":true,"ts":"1.2"}')
# One persona whose check is always refused (no exec guard), so the standup always has news to post.
BEHIND = {"version": 1, "personas": [
    {"id": "p", "name": "Pat", "cadence_hours": 0, "check": {"type": "command", "argv": ["/bin/true"]}}]}
QUIET = {"version": 1, "personas": []}


class World:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.state = tmp / "state"
        self.home = tmp / "home"
        (self.home / ".config" / "moses").mkdir(parents=True)
        bin_ = tmp / "bin"
        bin_.mkdir()
        (bin_ / "curl").write_text(FAKE_CURL)
        (bin_ / "curl").chmod(0o755)
        self.path = f"{bin_}:{os.environ['PATH']}"
        self.log = tmp / "curl.log"

    def standup(self, roster: dict, code: str = "200", body: str = "", rc: int = 0):
        (self.tmp / "roster.json").write_text(json.dumps(roster))
        self.log.write_text("")
        env = {**os.environ, "PATH": self.path, "MOSES_OP_HOME": str(self.home),
               "MOSES_REGISTRY": str(self.tmp / "roster.json"), "MOSES_STATE": str(self.state),
               "MOSES_EXEC_GUARD": "/nonexistent", "MOSES_ROOT": str(self.tmp / "no-checkout"),
               "SLACK_BOT_TOKEN": "xoxb-test", "SLACK_OPS_CHANNEL": "C0TEST",
               "FAKE_CURL_LOG": str(self.log), "FAKE_CURL_CODE": code, "FAKE_CURL_BODY": body,
               "FAKE_CURL_RC": str(rc)}
        return subprocess.run([str(MOSES), "standup"], env=env, capture_output=True, text=True, timeout=60)

    def posts(self) -> list[dict]:
        return [json.loads(l) for l in self.log.read_text().splitlines() if l.strip()]

    def jobs(self) -> list[Path]:
        d = self.state / "standups"
        return sorted(d.iterdir()) if d.is_dir() else []

    def record_with(self, text: str) -> Path:
        """The one record whose message contains `text` — found by content, never by sort order."""
        hits = [j for j in self.jobs() if text in (j / "message.txt").read_text()]
        assert len(hits) == 1, f"{len(hits)} records contain {text!r}"
        return hits[0]

    def late(self) -> list[str]:
        """What the sweep re-posted this run (a quiet morning's own all-clear line is not one)."""
        return [p["text"] for p in self.posts() if p["text"].startswith("_Late")]

    @staticmethod
    def outcome(job: Path) -> dict:
        return json.loads((job / "post.json").read_text())


print("the post's own outcome is recorded")
with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    r = w.standup(BEHIND, code="503", body="upstream connect error")
    jobs = w.jobs()
    check("a standup with news makes one job record", len(jobs) == 1, f"{jobs} {r.stderr}")
    o = w.outcome(jobs[0]) if jobs else {}
    check("a non-2xx post records posted=false", o.get("posted") is False, str(o))
    check("… with the HTTP status", o.get("http_status") == 503, str(o))
    check("… and the response body", "upstream connect error" in (o.get("error") or ""), str(o))
    check("the message itself is kept for the retry", "Pat" in (jobs[0] / "message.txt").read_text())
    check("the failure is said in the journal too", "NOT posted" in r.stderr, r.stderr)

with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    w.standup(BEHIND, rc=6)
    o = w.outcome(w.jobs()[0])
    check("a post that throws records posted=false", o.get("posted") is False, str(o))
    check("… with no status, because there was no answer", o.get("http_status") is None, str(o))
    check("… and curl's error", "Could not resolve host" in (o.get("error") or ""), str(o))

with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    w.standup(BEHIND, code="200", body='{"ok":false,"error":"channel_not_found"}')
    o = w.outcome(w.jobs()[0])
    check("Slack's 200-with-ok:false is a failure, with its reason",
          o.get("posted") is False and o.get("http_status") == 200 and o.get("error") == "channel_not_found", str(o))

with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    w.standup(BEHIND, *OK)
    o = w.outcome(w.jobs()[0])
    check("a delivered post records posted=true, status 200, no error",
          o.get("posted") is True and o.get("http_status") == 200 and o.get("error") is None, str(o))

print("the sweep")
with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    w.standup(BEHIND, code="500", body="boom")
    failed = w.jobs()[0]
    # A record with no post.json at all — a run killed before it could write one.
    bare = w.state / "standups" / "20260101-080000-1"
    bare.mkdir()
    (bare / "message.txt").write_text("the bare one")

    r = w.standup(QUIET, *OK)
    posts = w.posts()
    texts = [p["text"] for p in posts]
    check("the sweep re-posts the unposted record", any("Pat" in x for x in texts), str(texts))
    check("… and the one with no outcome recorded", any("the bare one" in x for x in texts), str(texts))
    check("… labeled late, with why it missed", any(x.startswith("_Late") and "boom" in x for x in texts), str(texts))
    check("… and marks both posted", w.outcome(failed).get("posted") is True and w.outcome(bare).get("posted") is True)
    check("… counting the attempt", w.outcome(failed).get("attempts") == 2, str(w.outcome(failed)))
    check("a quiet morning records its own all-clear line too", len(w.jobs()) == 3, str(w.jobs()))

    w.standup(QUIET, *OK)
    check("the sweep does not re-post one already marked posted", w.late() == [], str(w.posts()))

with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    w.standup(BEHIND, code="500", body="boom")             # attempt 1
    w.standup(QUIET, code="500", body="boom")              # sweep: attempt 2
    w.standup(QUIET, code="500", body="boom")              # sweep: attempt 3
    job = w.record_with("Pat")
    check("each sweep retries until the bound", w.outcome(job).get("attempts") == 3, str(w.outcome(job)))
    r = w.standup(QUIET, *OK)
    check("past the bound the sweep stops retrying",
          not any("Pat" in x for x in w.late()), str(w.posts()))
    check("… says it gave up, with the last error", "giving up" in r.stderr and "boom" in r.stderr, r.stderr)
    check("… and marks the record so", w.outcome(job).get("gave_up") is True, str(w.outcome(job)))
    r = w.standup(QUIET, *OK)
    check("… and says so once, not every morning",
          f"giving up on standup {job.name}" not in r.stderr and w.late() == [], r.stderr)

print("every path that speaks is recorded")
with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    mem = w.home / ".claude" / "memory"
    mem.mkdir(parents=True)
    (mem / "deferred.md").write_text("## Revisit the backups\n- id: backups\n- after: 2020-01-01\n- why: because\n")
    r = w.standup(QUIET, *OK)
    # This path called a function that never existed, so a deferred-only morning posted nothing.
    check("a deferred-only morning posts, and is recorded",
          any("Revisit the backups" in p["text"] for p in w.posts()) and len(w.jobs()) == 1
          and w.outcome(w.jobs()[0]).get("posted") is True, r.stderr)
    check("… under the all-clear line", w.posts()[0]["text"].startswith("✅ *Standup* — all clear"),
          str(w.posts()))

with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    r = w.standup(QUIET, *OK)
    # Brad, 2026-10-04: silence meant both "all clear" and "crashed", and three crashes hid behind it.
    check("a clean morning says so in one line", len(w.posts()) == 1 and w.posts()[0]["text"].startswith("✅ *Standup* — all clear (0 on the roster"),
          str(w.posts()) + r.stderr)
    check("… and that line is recorded like any other post",
          len(w.jobs()) == 1 and w.outcome(w.jobs()[0]).get("posted") is True, str(w.jobs()))

print(f"\n{'FAILED' if fails else 'OK'}{f' — {fails} failure(s)' if fails else ''}")
raise SystemExit(1 if fails else 0)
