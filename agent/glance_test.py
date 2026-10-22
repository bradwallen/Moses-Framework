#!/usr/bin/env python3
"""A project is done when it is live, green, and Brad has looked — his look is matched by CODE (#5).

    .venv/bin/python agent/glance_test.py

Through the real listener, with the real glance store and a scratch project registry: knight-next has
already proved the checks pass and the page answers (knight/test-next.sh covers that half), and asked
on the run's card. What is under test here is who can close it, where, and with which words. Then each
guard is removed and its check must go red.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = Path(tempfile.mkdtemp(prefix="moses-glance-"))
REG = TMP / "projects.json"
CARDLOG = TMP / "card.log"
FAKECARD = TMP / "fake-card"
FAKECARD.write_text(f"#!/bin/sh\necho \"$@\" >> {CARDLOG}\n")
FAKECARD.chmod(0o755)
os.environ.update({
    "SLACK_APP_TOKEN": "xapp-test", "SLACK_BOT_TOKEN": "xoxb-test", "MOSES_STATE": str(TMP / "state"),
    "MOSES_CHAT_CHANNELS": "C_CHAT", "KNIGHT_JOBS": str(TMP / "jobs"), "ZRYACHIY_JOBS": str(TMP / "zjobs"),
    "MOSES_COALESCE_WINDOW": "0", "MOSES_REGISTRY": str(REG), "KNIGHT_CARD": str(FAKECARD),
    "KNIGHT_BIN": "/bin/false",
})

import build_spec  # noqa: E402
import glance      # noqa: E402
import listener    # noqa: E402
import pacing      # noqa: E402
import silence     # noqa: E402

CHAN, BRAD, JON = "C_CHAT", "U_BRAD", "U_JON"
THREAD, CARD = "5.5", "6.6"
listener.BOT_USER_ID, listener.BOT_ID, listener.OWNER_ID = "U_MOSES", "B_MOSES", BRAD
listener.scout.check_target = lambda *a, **k: None
listener._spawn = lambda fn, *a: fn(*a)
posted, model_calls, HISTORY = [], [], []


class FakeWeb:
    def chat_postMessage(self, **kw):
        posted.append(kw)
        return {"ok": True, "ts": f"{len(posted)}.5"}

    def conversations_history(self, channel, limit=14):
        return {"messages": list(HISTORY)}

    def conversations_replies(self, channel, ts, limit=14):
        return {"messages": list(HISTORY)}

    def reactions_add(self, **kw):
        return {"ok": True}


listener.web = FakeWeb()


def _reply(hist, bot_user_id, channel, **kw):
    model_calls.append(channel)
    return "text", "Noted.", 0.01


listener.conversation.reply = _reply


class SM:
    def send_socket_mode_response(self, *a, **k):
        pass


def fire(user, text, ts, thread_ts=THREAD, channel=CHAN):
    posted.clear()
    ev = {"type": "message", "channel": channel, "ts": ts, "user": user, "text": text}
    if thread_ts:
        ev["thread_ts"] = thread_ts
    listener.handle(types.SimpleNamespace(type="events_api", envelope_id="e", payload={"event": ev}), SM())
    return [p.get("text", "") for p in posted]


def react(user, ts, reaction="white_check_mark"):
    posted.clear()
    ev = {"type": "reaction_added", "user": user, "reaction": reaction, "item": {"channel": CHAN, "ts": ts}}
    listener.handle(types.SimpleNamespace(type="events_api", envelope_id="e", payload={"event": ev}), SM())
    return [p.get("text", "") for p in posted]


def stage():
    return next(p for p in json.loads(REG.read_text())["projects"] if p["id"] == "gis")["stage"]


def reset(top_level=False):
    silence.FLAG.unlink(missing_ok=True)
    pacing.PACING_FILE.unlink(missing_ok=True)
    listener.SEEN.clear()
    listener._TURNS.clear()
    HISTORY.clear()
    model_calls.clear()
    glance.STORE.unlink(missing_ok=True)
    build_spec.STORE.unlink(missing_ok=True)
    CARDLOG.unlink(missing_ok=True)
    REG.write_text(json.dumps({"projects": [{
        "id": "gis", "name": "Listings finder", "rank": 1, "status": "active", "stage": "building",
        "kind": "internal", "milestones": [], "ideas": [],
        "next": "Waiting for Brad's look at https://example.com/listings/"}]}))
    rd = TMP / "jobs" / "J1"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "card.json").write_text(json.dumps({"ts": CARD, "channel": CHAN, "thread": "" if top_level else THREAD}))
    (rd / "notify.json").write_text(json.dumps({"channel": CHAN, "thread_ts": "" if top_level else THREAD, "user": BRAD}))
    # The same command knight-next runs.
    r = subprocess.run([sys.executable, str(HERE / "glance.py"), "request", "gis",
                        "https://example.com/listings/", str(rd)], capture_output=True, text=True,
                       env=os.environ.copy())
    return r


def suite(verbose):
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        if verbose:
            print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' — ' + str(detail)[:240]}")

    r = reset()
    check("knight-next's request is recorded where the run's card is",
          r.returncode == 0 and glance.pending(CHAN, THREAD) and glance.by_message(CARD), (r.stdout, r.stderr))

    out = fire(BRAD, "looks good", "7.1")
    check("Brad's 'looks good' under the card marks the project shipped — by code, without the model",
          stage() == "shipped" and not model_calls and any("Marked" in t for t in out), (stage(), out))
    nxt = next(p for p in json.loads(REG.read_text())["projects"] if p["id"] == "gis").get("next")
    check("and clears the tracker's 'waiting for Brad's look'", not nxt, nxt)
    check("and the question is gone", glance.pending(CHAN, THREAD) is None)
    check("and the run's card says so too", CARDLOG.exists() and " done Brad looked" in CARDLOG.read_text(),
          CARDLOG.read_text() if CARDLOG.exists() else "")

    # A card posted at the TOP of a channel (no conversation known): only a reply in ITS thread counts.
    reset(top_level=True)
    fire(BRAD, "looks good", "11.1", thread_ts=None)
    check("a top-level 'looks good' in the channel closes nothing — it could answer anything", stage() == "building", stage())
    fire(BRAD, "looks good", "11.2", thread_ts=CARD)
    check("a reply in the card's own thread does", stage() == "shipped", stage())

    reset()
    fire(BRAD, "done", "12.1")
    fire(BRAD, "ship it", "12.2")
    check("'done' and 'ship it' are not a look: they answer other questions", stage() == "building", stage())
    out = fire(BRAD, "mark gis done", "12.3", thread_ts=None)
    check("'mark gis done' anywhere in the channel names the project, and closes it",
          stage() == "shipped" and any("Marked" in t for t in out), (stage(), out))

    reset()
    build_spec.propose("Add a remote-only filter to the job list", "Remote roles can be shown alone",
                       "Remote GIS Analyst", "On-site GIS Technician in Denver", "gis")
    build_spec.claim(set(), channel=CHAN, thread_ts=THREAD, user=BRAD, port="")
    out = fire(BRAD, "looks good", "13.1")
    check("with a build plan also waiting here, 'looks good' asks which — it closes nothing and starts nothing",
          stage() == "building" and build_spec.pending(CHAN, THREAD) and any("Two things are waiting" in t for t in out),
          (stage(), out))

    reset()
    REG.write_text(json.dumps({"projects": [{"id": "gis-other", "name": "Another GIS", "rank": 1,
                                             "status": "active", "stage": "building", "kind": "internal",
                                             "milestones": [], "ideas": []}]}))
    out = fire(BRAD, "looks good", "14.1")
    check("a project the tracker cannot find exactly is not closed, and the request stays open",
          glance.pending(CHAN, THREAD) is not None and any("still waiting" in t for t in out)
          and json.loads(REG.read_text())["projects"][0]["stage"] == "building", out)

    reset()
    fire(JON, "looks good", "8.1")
    check("someone else's 'looks good' closes nothing", stage() == "building", stage())
    fire(BRAD, "looks good", "8.2", thread_ts="99.9")
    check("in another thread it closes nothing", stage() == "building", stage())
    fire(BRAD, "Moses, the header is cut off on my phone", "8.3")
    check("anything else Brad says is conversation, and the question stays open",
          stage() == "building" and model_calls and glance.pending(CHAN, THREAD), (stage(), model_calls))
    fire(BRAD, "looks good", "8.4")
    check("so a later 'looks good' still closes it", stage() == "shipped", stage())

    reset()
    react(BRAD, CARD, reaction="+1")
    check("a 👍 on the card is not a look", stage() == "building", stage())
    out = react(BRAD, CARD)
    check("a ✅ on the card closes it", stage() == "shipped" and any("Marked" in t for t in out), (stage(), out))

    reset()
    HISTORY.append({"ts": "99.0", "user": "U_ATLAS", "bot_id": "B_ATLAS", "text": "Looks good to me!"})
    fire(BRAD, "looks good", "9.1")
    check("after another agent spoke, 'looks good' is not taken as the look", stage() == "building", stage())

    reset()
    d = glance._load()
    for g in d["glances"]:
        g["at"] -= 8 * 24 * 3600
    glance._save(d)
    fire(BRAD, "looks good", "10.1")
    react(BRAD, CARD)
    check("a request older than a week closes nothing", stage() == "building", stage())
    return passed, failed


_real_pending = glance.pending
MUTANTS = [
    ("top level counts", lambda: setattr(glance, "pending", lambda c, t="": _real_pending(c, t or CARD)),
     "a top-level 'looks good' in the channel closes nothing — it could answer anything"),
    ("'done' counts", lambda: setattr(glance, "LOOKS_GOOD", __import__("re").compile(r"^(looks good|done|ship it)$")),
     "'done' and 'ship it' are not a look: they answer other questions"),
    ("first matcher wins", lambda: setattr(build_spec, "is_yes", lambda t: False),
     "with a build plan also waiting here, 'looks good' asks which — it closes nothing and starts nothing"),
    ("anyone's look", lambda: setattr(listener, "OWNER_ID", JON), "someone else's 'looks good' closes nothing"),
    ("any thread", lambda: setattr(glance, "pending", lambda c, t="": next(
        (g for g in glance._load()["glances"] if g.get("channel") == c), None)), "in another thread it closes nothing"),
    ("any words", lambda: setattr(glance, "is_looks_good", lambda t: True),
     "anything else Brad says is conversation, and the question stays open"),
    ("any reaction", lambda: setattr(glance, "REACTIONS", listener.proposals.CONFIRM_REACTIONS),
     "a 👍 on the card is not a look"),
    ("another agent ignored", lambda: setattr(listener.proposals, "confirmation_is_for_me", lambda *a, **k: True),
     "after another agent spoke, 'looks good' is not taken as the look"),
    ("no TTL", lambda: setattr(glance, "TTL", 10 ** 9), "a request older than a week closes nothing"),
]

if __name__ == "__main__":
    print("A CLEAR DONE")
    passed, failed = suite(True)
    print(f"\n  {len(passed)}/{len(passed) + len(failed)} pass\n\nWATCH EACH GUARD FAIL")
    keep = {m: dict(vars(m)) for m in (glance, listener, listener.proposals, build_spec)}
    blind = []
    for name, apply, expect in MUTANTS:
        apply()
        try:
            _, mf = suite(False)
        finally:
            for m, v in keep.items():
                for k, x in v.items():
                    if getattr(m, k, None) is not x:
                        setattr(m, k, x)
        red = expect in mf
        print(f"  {'ok  ' if red else 'FAIL'} {name} → '{expect}' goes red")
        if not red:
            blind.append(name)
    if failed or blind:
        print(f"\nFAILED: {len(failed)} check(s), {len(blind)} guard(s) not watched failing"); sys.exit(1)
    print(f"\nall {len(passed)} pass, and every guard was watched failing")
