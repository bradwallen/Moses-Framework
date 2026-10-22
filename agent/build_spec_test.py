#!/usr/bin/env python3
"""A Slack build starts only after Brad confirms a spec with concrete examples (#2) — through the
real listener, with the real build_spec store and a stand-in for `knight start`.

    /home/brad/moses-venv/bin/python agent/build_spec_test.py

Brad, 2026-10-17: "When you said 'civil and water' earlier I didn't think you meant civil engineer."
So Moses proposes (knight_propose) with examples of what will and will not be built; Brad says yes or
reacts ✅; the LISTENER starts the build — the model never does. Then each guard is removed and its
check must go red.
"""
import json
import os
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = Path(tempfile.mkdtemp(prefix="moses-buildspec-"))
(TMP / "jobs").mkdir()
FAKE = TMP / "fake-knight"
FAKE.write_text(f"""#!/usr/bin/env python3
import json, os, sys, pathlib, time
jobs = pathlib.Path({str(TMP / 'jobs')!r}); jid = f"J{{time.time_ns()}}{{os.getpid()}}"
(jobs / jid).mkdir(); (jobs / jid / "argv.json").write_text(json.dumps(sys.argv[1:]))
print(f"started  {{jid}}")
""")
FAKE.chmod(0o755)
# Proposals live on the project registry: point it at a temp file, so no "yes" here can ever file one of
# Brad's real ideas.
REG = TMP / "projects.json"
REG.write_text(json.dumps({"projects": [{"id": "moses", "name": "Moses", "rank": 1, "status": "active",
                                        "stage": "building", "milestones": [], "ideas": []}]}))
CARDLOG = TMP / "card.log"
FAKECARD = TMP / "fake-card"
FAKECARD.write_text(f"#!/bin/sh\necho \"$@\" >> {CARDLOG}\n")
FAKECARD.chmod(0o755)
os.environ.update({
    "SLACK_APP_TOKEN": "xapp-test", "SLACK_BOT_TOKEN": "xoxb-test", "MOSES_STATE": str(TMP / "state"),
    "MOSES_CHAT_CHANNELS": "C_CHAT", "KNIGHT_JOBS": str(TMP / "jobs"), "ZRYACHIY_JOBS": str(TMP / "zjobs"),
    "KNIGHT_BIN": str(FAKE), "MOSES_COALESCE_WINDOW": "0", "KNIGHT_CARD": str(FAKECARD),
    # The Slack turn's MCP server (conversation.MCP_URL's port): a spec recorded there is this turn's.
    "MOSES_MCP_PORT": "8765", "MOSES_REGISTRY": str(REG),
})

import threading     # noqa: E402

import build_spec    # noqa: E402
import listener      # noqa: E402
import pacing        # noqa: E402
import silence       # noqa: E402

CHAN, BOT, BRAD, JON = "C_CHAT", "U_MOSES", "U_BRAD", "U_JON"
listener.BOT_USER_ID, listener.BOT_ID, listener.OWNER_ID = BOT, "B_MOSES", BRAD
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
        return {"messages": []}

    def reactions_add(self, **kw):
        return {"ok": True}


listener.web = FakeWeb()


class SM:
    def send_socket_mode_response(self, *a, **k):
        pass


def model_proposing(include="GIS Analyst Intern", exclude="Civil Engineering Intern that mentions ArcGIS",
                    then="text", port="8765"):
    """The model's turn: it calls knight_propose (the MCP tool's body), then says a line WITHOUT pasting
    the spec — or fails outright (then="raise") — so what Brad sees is whatever the listener posts."""
    def _reply(hist, bot_user_id, channel, **kw):
        model_calls.append(channel)
        os.environ["MOSES_MCP_PORT"] = port
        build_spec.propose("Restrict the job list to GIS analyst roles by title",
                           "Only roles whose title names GIS work are listed", include, exclude, "example-app")
        os.environ["MOSES_MCP_PORT"] = "8765"
        if then == "raise":
            raise RuntimeError("the model fell over after proposing")
        return "text", "I drafted it — it's waiting on your yes.", 0.01
    listener.conversation.reply = _reply


def model_plain():
    def _reply(hist, bot_user_id, channel, **kw):
        model_calls.append(channel)
        return "text", "Sure.", 0.01
    listener.conversation.reply = _reply


def fire(user, text, ts, channel=CHAN):
    posted.clear()
    ev = {"type": "message", "channel": channel, "ts": ts, "user": user, "text": text}
    listener.handle(types.SimpleNamespace(type="events_api", envelope_id="e", payload={"event": ev}), SM())
    return [p.get("text", "") for p in posted]


def react(user, ts, channel=CHAN, reaction="white_check_mark"):
    posted.clear()
    ev = {"type": "reaction_added", "user": user, "reaction": reaction, "item": {"channel": channel, "ts": ts}}
    listener.handle(types.SimpleNamespace(type="events_api", envelope_id="e", payload={"event": ev}), SM())
    return [p.get("text", "") for p in posted]


def started():
    return sorted((TMP / "jobs").glob("J*/argv.json"))


def reset():
    silence.FLAG.unlink(missing_ok=True)
    pacing.PACING_FILE.unlink(missing_ok=True)
    listener.SEEN.clear()
    build_spec.STORE.unlink(missing_ok=True)
    CARDLOG.unlink(missing_ok=True)
    HISTORY.clear()
    listener._TURNS.clear()          # a mutant that never ends a turn must not leak into the next run
    for f in (TMP / "jobs").glob("J*"):
        for x in f.iterdir():
            x.unlink()
        f.rmdir()
    model_calls.clear()


def suite(verbose):
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        if verbose:
            print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' — ' + str(detail)[:240]}")

    reset()
    out = build_spec.propose("Restrict the list to GIS roles", "Only GIS-titled roles are listed", "", "x")
    check("a spec without examples on both sides is refused", "EXAMPLES" in out and not build_spec.ids(), out)

    reset(); model_proposing()
    out = fire(BRAD, "Moses, make the listings page GIS analyst roles only", "10.1")
    check("the LISTENER posts the recorded spec, though the model's reply never pasted it",
          any("Will include" in t and "GIS Analyst Intern" in t and "Civil Engineering Intern" in t for t in out), out)
    check("and nothing is built yet", started() == [], started())

    before = len(model_calls)
    out = fire(BRAD, "yes", "10.2")
    argv = json.loads(started()[0].read_text()) if started() else []
    check("Brad's yes starts the build — by code, without the model",
          len(started()) == 1 and len(model_calls) == before and any("Started" in t for t in out), out)
    acc = argv[argv.index("--acceptance") + 1] if "--acceptance" in argv else ""
    check("the confirmed examples become acceptance criteria",
          "It INCLUDES: GIS Analyst Intern" in acc and "It does NOT include: Civil Engineering Intern" in acc, acc)
    note = json.loads((started()[0].parent / "notify.json").read_text()) if started() else {}
    check("the build reports to the conversation that confirmed it", note.get("channel") == CHAN and note.get("user") == BRAD, note)
    card = CARDLOG.read_text() if CARDLOG.exists() else ""
    check("and its status card is brought here right away", started() and str(started()[0].parent) in card
          and " building " in card, card)
    out = fire(BRAD, "yes", "10.3")
    check("a second yes does not start it twice", len(started()) == 1, started())

    reset(); model_proposing()
    fire(BRAD, "Moses, make the listings page GIS analyst roles only", "11.1")
    model_plain()
    fire(JON, "yes", "11.2")
    check("someone else's yes starts nothing", started() == [], started())
    fire(BRAD, "yes", "11.3", channel="C_OTHER")
    check("a yes in another conversation starts nothing", started() == [], started())
    ev = {"type": "message", "channel": CHAN, "ts": "11.4", "thread_ts": "11.1", "user": BRAD, "text": "yes"}
    listener.handle(types.SimpleNamespace(type="events_api", envelope_id="e", payload={"event": ev}), SM())
    check("a yes in another thread of the same channel starts nothing", started() == [], started())

    reset(); model_proposing()
    fire(BRAD, "Moses, make the listings page GIS analyst roles only", "14.1")
    model_plain()
    fire(BRAD, "hmm, let me think about the NGA part", "14.2")
    fire(BRAD, "ok", "14.3")
    check("anything else Brad says retires the spec: a later 'ok' starts nothing", started() == [], started())

    reset(); model_proposing(then="raise")
    out = fire(BRAD, "Moses, make the listings page GIS analyst roles only", "15.1")
    check("a spec is still shown when the reply itself failed", any("Will include" in t for t in out), out)

    reset(); model_proposing(port="8766")
    model_calls.clear()
    fire(BRAD, "Moses, how is the GIS build going", "16.1")
    check("a spec from the iOS app's server is never claimed by a Slack turn",
          not build_spec.pending(CHAN) and not any("Will include" in t for t in [p.get("text", "") for p in posted]),
          build_spec._load())

    reset(); model_proposing()
    other = listener._turn_begin()
    out = fire(BRAD, "Moses, make the listings page GIS analyst roles only", "17.1")
    listener._turn_end(other)
    check("while another turn runs, a new spec is not guessed into this conversation — and he says so",
          not build_spec.pending(CHAN) and any("can't be sure" in t for t in out), out)

    reset(); model_proposing()
    fire(BRAD, "Moses, make the listings page GIS analyst roles only", "18.1")
    HISTORY.append({"ts": "99.0", "user": "U_ATLAS", "bot_id": "B_ATLAS", "text": "Sounds good!"})
    model_plain()
    out = fire(BRAD, "yes", "18.2")
    check("a yes after another agent spoke is asked about, not obeyed",
          started() == [] and any("Was that for me" in t for t in out), out)
    sid = build_spec.pending(CHAN)["id"] if build_spec.pending(CHAN) else "?"
    fire(BRAD, f"start {sid}", "18.3")
    check("and `start <id>` starts it", len(started()) == 1, started())

    reset(); model_proposing()
    fire(BRAD, "Moses, make the listings page GIS analyst roles only", "19.1")
    keep_r, keep_p, keep_f = listener.proposals.resolve, listener.proposals.pending, listener.proposals.file_tasks
    idea = {"id": "p1", "task": "Look into Atlas's digest format", "msg_ts": "", "channel": CHAN}
    listener.proposals.resolve = lambda raw: ("confirm", [idea], "")
    listener.proposals.pending = lambda: [idea]
    listener.proposals.file_tasks = lambda items, d: (["the idea"], [])
    try:
        out = fire(BRAD, "yes", "19.2")
    finally:
        listener.proposals.resolve, listener.proposals.pending, listener.proposals.file_tasks = keep_r, keep_p, keep_f
    check("a yes while an idea is ALSO waiting asks which, and starts nothing",
          started() == [] and any("Two things are waiting" in t for t in out), out)

    reset(); model_proposing()
    fire(BRAD, "Moses, make the listings page GIS analyst roles only", "20.1")
    spec = build_spec.pending(CHAN)
    outs = []
    ts = [threading.Thread(target=lambda: outs.append(build_spec.start(spec, BRAD))) for _ in range(3)]
    [t.start() for t in ts]; [t.join() for t in ts]
    check("three confirmations at once start ONE build", len(started()) == 1, (started(), outs))

    reset(); model_proposing(include="GIS Analyst Intern", exclude="Surveyor")
    fire(BRAD, "Moses, make the listings page GIS analyst roles only", "12.1")
    model_proposing(include="GIS Analyst Intern; Geospatial Analyst at NGA", exclude="Civil Engineering Intern")
    fire(BRAD, "Moses, also keep NGA analyst roles", "12.2")
    fire(BRAD, "yes", "12.3")
    argv = json.loads(started()[0].read_text()) if started() else []
    acc = argv[argv.index("--acceptance") + 1] if "--acceptance" in argv else ""
    check("a revised spec replaces the old one, and yes starts the revision",
          len(started()) == 1 and "Geospatial Analyst at NGA" in acc and "Surveyor" not in acc, acc)

    reset(); model_proposing()
    fire(BRAD, "Moses, make the listings page GIS analyst roles only", "13.1")
    spec_ts = posted[-1].get("ts") if posted and "ts" in posted[-1] else ""
    msg_ts = next((s.get("message_ts") for s in build_spec._load()["specs"]), "")
    react(BRAD, msg_ts, reaction="+1")
    check("a 👍 on the spec is not a go-ahead", started() == [], started())
    out = react(BRAD, msg_ts)
    check("a ✅ on the spec message starts it too", len(started()) == 1 and any("Started" in t for t in out), (msg_ts, out))

    reset(); model_proposing()
    fire(BRAD, "Moses, make the listings page GIS analyst roles only", "21.1")
    d = build_spec._load()
    for s_ in d["specs"]:
        s_["created"] -= 25 * 3600       # a day and an hour old: past the 24-hour TTL
    build_spec._save(d)
    react(BRAD, d["specs"][-1].get("message_ts", ""))
    check("a ✅ on a spec past its TTL starts nothing", started() == [], started())
    return passed, failed


MUTANTS = [
    ("anyone's yes", lambda: setattr(listener, "OWNER_ID", JON), "someone else's yes starts nothing"),
    ("any conversation", lambda: setattr(build_spec, "pending", lambda c, t="": next(
        (s for s in reversed(build_spec._load()["specs"]) if s.get("status") == "pending"), None)),
     "a yes in another conversation starts nothing"),
    ("no examples needed", lambda: setattr(build_spec, "_lines", lambda v: ["x"]),
     "a spec without examples on both sides is refused"),
    ("any thread", lambda: setattr(build_spec, "pending", lambda c, t="": next(
        (s for s in reversed(build_spec._load()["specs"]) if s.get("status") == "pending" and s.get("channel") == c), None)),
     "a yes in another thread of the same channel starts nothing"),
    ("never retired", lambda: setattr(build_spec, "expire_on_reply", lambda c, t="": ""),
     "anything else Brad says retires the spec: a later 'ok' starts nothing"),
    ("the model's word for it", lambda: setattr(build_spec, "get", lambda sid: None),
     "the LISTENER posts the recorded spec, though the model's reply never pasted it"),
    ("any server", lambda: setattr(build_spec, "_same_server", lambda s, p: True),
     "a spec from the iOS app's server is never claimed by a Slack turn"),
    ("overlap ignored", lambda: setattr(listener, "_turn_end", lambda t: False),
     "while another turn runs, a new spec is not guessed into this conversation — and he says so"),
    ("another agent ignored", lambda: setattr(listener.proposals, "confirmation_is_for_me", lambda *a, **k: True),
     "a yes after another agent spoke is asked about, not obeyed"),
    ("any reaction", lambda: setattr(build_spec, "SPEC_REACTIONS", listener.proposals.CONFIRM_REACTIONS),
     "a 👍 on the spec is not a go-ahead"),
    ("no TTL on reactions", lambda: setattr(build_spec, "TTL", 10 ** 9),
     "a ✅ on a spec past its TTL starts nothing"),
    ("no take", lambda: setattr(build_spec, "_take", lambda sid: True), "three confirmations at once start ONE build"),
]

if __name__ == "__main__":
    print("BUILD SPECS")
    passed, failed = suite(True)
    print(f"\n  {len(passed)}/{len(passed) + len(failed)} pass\n\nWATCH EACH GUARD FAIL")
    blind = []
    keep_mod = {m: dict(vars(m)) for m in (build_spec, listener, listener.proposals)}
    for name, apply, expect in MUTANTS:
        apply()
        try:
            _, mf = suite(False)
        finally:
            for m, v in keep_mod.items():
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
