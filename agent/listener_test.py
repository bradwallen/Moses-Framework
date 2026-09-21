#!/usr/bin/env python3
"""Drive listener.handle() end to end with fake Slack events. Run with the venv python:

    /home/brad/moses-venv/bin/python listener_test.py

The unit tests cover each guard in isolation. THIS covers the wiring between them, which is where
the last two real bugs of the day actually lived: logic that was correct on its own and wrong once
the caller was involved. Nothing here reaches Slack or a model — the web client and the conversation
call are both stubbed.
"""

import json


def _json_dumps_registry():
    return json.dumps({"projects": [
        {"id": "viatica", "name": "Viatica", "rank": 1, "status": "active",
         "stage": "building", "milestones": []},
    ]})
import os
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="moses-listener-")
os.environ.update({
    "SLACK_APP_TOKEN": "xapp-test", "SLACK_BOT_TOKEN": "xoxb-test",
    "MOSES_STATE": _TMP, "MOSES_CHAT_CHANNELS": "C_CHAT",
    # Its own job record, so nothing here can touch Knight's real one.
    "KNIGHT_JOBS": os.path.join(_TMP, "knight-jobs"),
    "ZRYACHIY_JOBS": os.path.join(_TMP, "zryachiy-jobs"),
})

import addressing    # noqa: E402
import listener      # noqa: E402
import pacing        # noqa: E402
import silence       # noqa: E402

CHAN, BOT, BRAD, JON = "C_CHAT", "U_MOSES", "U_BRAD", "U_JON"
ATLAS = {"user": "U_ATLAS", "bot_id": "B_ATLAS"}

# THE REAL SHAPE, copied from the live channel 2026-08-13. A message Moses posts under his own
# display name has NO `user` field at all — only bot_id/app_id/username/subtype. The first version of
# this fixture invented `{"user": BOT, ...}`, a shape Slack never produces, and that is exactly why
# every follow-up and cap test passed while both were broken in production.
def moses_said(text="Back. What are we digging into?"):
    return {"bot_id": "B_MOSES", "app_id": "A_MOSES", "username": "Moses",
            "subtype": "bot_message", "text": text}


MOSES_SAID = moses_said()

listener.BOT_USER_ID = BOT
listener.BOT_ID = "B_MOSES"
listener.OWNER_ID = BRAD

posted: list = []
history: list = []
results = []


reads: list = []
reacted: list = []
BAD_EMOJI = {"not_a_real_emoji"}


class FakeWeb:
    def chat_postMessage(self, **kw):
        posted.append(kw)
        return {"ok": True}

    def conversations_history(self, channel, limit=14):
        reads.append(("history", channel, None))
        return {"messages": history}

    def reactions_add(self, channel, timestamp, name):
        reacted.append({"channel": channel, "ts": timestamp, "name": name})
        if name in BAD_EMOJI:
            raise RuntimeError("invalid_name")
        return {"ok": True}

    def conversations_replies(self, channel, ts, limit=14):
        # Slack returns thread replies OLDEST-first (parent first) while history is NEWEST-first.
        # Verified against the live channel. The fake mirrors that, so a caller that forgets to
        # reverse gets caught here rather than by Moses answering the wrong message in production.
        reads.append(("replies", channel, ts))
        return {"messages": list(reversed(history))}


listener.web = FakeWeb()


class FakeSM:
    def send_socket_mode_response(self, *a, **k):
        pass


def fire(event: dict, ts: str = "1.0"):
    """Deliver one message event, as Slack would."""
    posted.clear()
    req = types.SimpleNamespace(
        type="events_api", envelope_id="e",
        payload={"event": {"type": "message", "channel": CHAN, "ts": ts, **event}},
    )
    listener.handle(req, FakeSM())
    return posted


def stub_conversation(text="Thought about it.", cost=0.03, kind="text", value=None):
    calls = []

    # **kwargs so a new argument on the real reply() does not silently turn every call in this suite
    # into a caught TypeError — which is what happened when `web` was added for speaker names: the
    # listener swallowed it as "conversation failed" and three checks failed somewhere else entirely.
    def _reply(hist, bot_user_id, channel, reactive=False, directed=False, images=None,
               image_notes=None, **kwargs):
        calls.append({"reactive": reactive, "channel": channel})
        if text is None:
            return "none", "", 0.0
        return kind, (value if value is not None else text), cost
    listener.conversation.reply = _reply
    return calls


def check(label, cond):
    results.append((label, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + label)

# ISOLATED ON THE REGISTRY, because that is where proposals now live. These used to unlink a
# `proposals.json`; there is no longer one. Pointing the registry at a temp file exercises the real
# store rather than a stand-in, and guarantees a test can never touch Brad's actual projects.
import json as _json, tempfile as _tf, pathlib as _pl, sys as _sys
_sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "mcp"))
import projects as _registry_mod

def _testreg():
    return _MEM2 / "projects.json"


def _fresh_registry():
    """A registry holding just the two projects the tests propose against."""
    _testreg().write_text(_json.dumps({"projects": [
        {"id": "viatica", "name": "Viatica", "rank": 1, "status": "active",
         "stage": "building", "milestones": [], "ideas": []},
        {"id": "moses", "name": "Moses", "rank": 2, "status": "active",
         "stage": "building", "milestones": [], "ideas": []},
    ]}), encoding="utf-8")
    _registry_mod.REGISTRY = _testreg()



def reset():
    silence.FLAG.unlink(missing_ok=True)
    pacing.PACING_FILE.unlink(missing_ok=True)
    # ENGAGEMENT IS PERSISTENT NOW, AND THAT MADE THIS FILE ORDER-DEPENDENT. Since 2026-09-08 a
    # message Moses considers is recorded to engaged.json, so he stays in a conversation for twelve
    # hours instead of dropping out the moment he passes. Every case here shares one MOSES_STATE, so
    # without this line the first case that engages him leaves him engaged for all the rest — six
    # checks about whether he should speak at all were failing on state left by their predecessors.
    #
    # Cleared rather than switched off. Setting MOSES_ENGAGED_WINDOW_S=0 would also go green and
    # would make this file blind to engagement leaking into routing, which is the regression most
    # worth catching here. The window itself is exercised in addressing_engage_test.py.
    addressing._ENGAGED.unlink(missing_ok=True)
    history.clear()
    reads.clear()
    reacted.clear()
    listener.SEEN.clear()


# ── A human is answered ─────────────────────────────────────────────────────
reset(); calls = stub_conversation()
out = fire({"user": JON, "text": "Moses, what do you think?"}, ts="1.1")
check("a human addressing Moses gets a reply", len(out) == 1 and out[0]["text"] == "Thought about it.")
check("the reply is not marked reactive", calls and calls[0]["reactive"] is False)
check("it posts as Moses", out[0]["username"] == listener.MOSES_NAME)

reset(); stub_conversation()
out = fire({"user": JON, "text": "just chatting, nothing for the bot"}, ts="1.2")
check("an unaddressed human message is ignored", out == [])

# ── SAFEWORD ────────────────────────────────────────────────────────────────
reset(); calls = stub_conversation()
out = fire({"user": JON, "text": "stand down"}, ts="2.1")
check("SAFEWORD anyone can trigger it", len(out) == 1 and "Standing down" in out[0]["text"])
check("SAFEWORD it engages the flag", silence.silenced())
check("SAFEWORD no model was called", calls == [])

out = fire({"user": BRAD, "text": "Moses, are you there?"}, ts="2.2")
check("SAFEWORD a silenced Moses says nothing at all", out == [])

out = fire({"user": JON, "text": "as you were"}, ts="2.3")
check("SAFEWORD only Brad can lift it", out == [] and silence.silenced())

out = fire({"user": BRAD, "text": "as you were"}, ts="2.4")
check("SAFEWORD Brad lifts it", len(out) == 1 and not silence.silenced())

reset(); stub_conversation()
# The false trigger this test found: ordinary shop talk in a channel about servers.
fire({"user": BRAD, "text": "we should stand down the old Pi service"}, ts="2.5")
check("SAFEWORD shop talk does NOT silence him", not silence.silenced())
out = fire({**ATLAS, "text": "stand down"}, ts="2.6")
check("SAFEWORD a bot saying it does NOT silence Moses", not silence.silenced())

# ── Bot-to-bot, within the limits ───────────────────────────────────────────
reset(); calls = stub_conversation()
history[:] = [{**ATLAS, "text": "Moses, thoughts?"}]
out = fire({**ATLAS, "text": "Moses, thoughts?"}, ts="3.1")
check("BOT Atlas addressing Moses gets a reply", len(out) == 1)
check("BOT the reply IS marked reactive", calls and calls[0]["reactive"] is True)

reset(); calls = stub_conversation()
out = fire({**ATLAS, "text": "anyway, as I was saying"}, ts="3.2")
check("BOT Atlas not addressing Moses is ignored", out == [] and calls == [])

# ── Bot-to-bot, over the limits ─────────────────────────────────────────────
reset(); calls = stub_conversation()
# Written against the constant, not the number: these fixtures were built for a cap of 3 and went
# green while testing nothing the day it moved to 12.
history[:] = [{**ATLAS, "text": "and another"}, moses_said("sure")] * pacing.MAX_OWN_REPLIES
out = fire({**ATLAS, "text": "Moses, thoughts?"}, ts="4.1")
check("LIMIT a runaway thread stops the model", calls == [])
check("LIMIT he says he'll pick it up later", len(out) == 1 and out[0]["text"] == pacing.BOWING_OUT)

# Bowing out must not itself become chatter.
history.insert(0, moses_said(pacing.BOWING_OUT))
out = fire({**ATLAS, "text": "Moses, still there?"}, ts="4.2")
check("LIMIT he bows out once, not every time", out == [])

reset(); calls = stub_conversation()
history[:] = [{**ATLAS, "text": "x"}] * 20
history.insert(0, {"user": BRAD, "text": "what do you two make of it?"})
out = fire({"user": BRAD, "text": "Moses, what do you make of it?"}, ts="4.3")
check("LIMIT a human is never rate-limited, even after a runaway thread", len(out) == 1)
check("LIMIT the human reply is not reactive", calls and calls[0]["reactive"] is False)

# The day's budget and the cooldown withhold IN SILENCE — the bow-out belongs to the turn cap alone.
# Saying "I'll pick this up later" for a 30-second wait is a lie, and saying it for a global budget
# would say it once in every channel he is addressed in.
reset(); calls = stub_conversation()
history[:] = [{**ATLAS, "text": "Moses, thoughts?"}]
for _ in range(pacing.DAILY_REACTIVE_CAP):
    # An HOUR ago, deliberately. Recorded at `now` these went green with the daily cap deleted —
    # the COOLDOWN was doing the withholding and the check could not tell the two apart.
    pacing.record_reply(now=time.time() - 3600, reactive=True)
out = fire({**ATLAS, "text": "Moses, thoughts?"}, ts="4.4")
check("LIMIT the day's budget stops the model", calls == [])
check("LIMIT a spent budget goes quiet without announcing it", out == [])
out = fire({"user": BRAD, "text": "Moses, what do you think?"}, ts="4.5")
check("LIMIT Brad is answered with the day's budget spent", len(out) == 1)

reset(); calls = stub_conversation()
history[:] = [{**ATLAS, "text": "Moses, thoughts?"}]
pacing.record_reply(reactive=True)          # he posted to a bot a moment ago
out = fire({**ATLAS, "text": "Moses, still there?"}, ts="4.6")
check("LIMIT a bot reply inside the cooldown is withheld silently", out == [] and calls == [])
out = fire({"user": BRAD, "text": "Moses, what do you think?"}, ts="4.7")
check("LIMIT the cooldown never reaches Brad", len(out) == 1)

# ── Degradation ─────────────────────────────────────────────────────────────
reset()
listener.conversation.reply = lambda *a, **k: ("none", "", 0.0)      # conversation off/unavailable
out = fire({"user": BRAD, "text": "Moses, status"}, ts="5.1")
check("DEGRADE with no conversation he still answers deterministically", len(out) == 1 and out[0]["text"])

reset()
def boom(*a, **k):
    raise RuntimeError("model exploded")
listener.conversation.reply = boom
out = fire({"user": BRAD, "text": "Moses, status"}, ts="5.2")
check("DEGRADE a crash in conversation falls back, it does not go silent", len(out) == 1)

# ── THREADS ─────────────────────────────────────────────────────────────────
# Two bugs from the first live outing. Moses replied in a thread to EVERY message, turning the
# channel into a wall of one-message threads; and when Atlas then replied inside a thread, Moses had
# no way to read it, because conversations.history does not return thread replies at all.
reset(); calls = stub_conversation()
out = fire({"user": BRAD, "text": "Moses, in the channel please"}, ts="7.1")
check("THREAD a top-level message is answered IN THE CHANNEL",
      len(out) == 1 and not out[0].get("thread_ts"))
check("THREAD channel context comes from history", ("history", CHAN, None) in reads)

reset(); calls = stub_conversation()
history[:] = [{"user": BRAD, "text": "Moses, what about this?"}]
out = fire({"user": BRAD, "text": "Moses, what about this?", "thread_ts": "7.0"}, ts="7.2")
check("THREAD a threaded message is answered IN THAT THREAD",
      len(out) == 1 and out[0].get("thread_ts") == "7.0")
check("THREAD thread context comes from replies, not history",
      ("replies", CHAN, "7.0") in reads and not any(r[0] == "history" for r in reads))

reset(); calls = stub_conversation()
history[:] = [{**ATLAS, "text": "Moses, still there?"}]
out = fire({**ATLAS, "text": "Moses, still there?", "thread_ts": "7.0"}, ts="7.3")
check("THREAD Atlas replying inside a thread still reaches Moses", len(out) == 1)
check("THREAD and Moses answers in the same thread", out[0].get("thread_ts") == "7.0")

reset(); calls = stub_conversation()
history[:] = [{"user": JON, "text": "stand down"}]
out = fire({"user": JON, "text": "stand down", "thread_ts": "7.0"}, ts="7.4")
check("THREAD the safeword posts to the CHANNEL, not a thread, so everyone sees it",
      len(out) == 1 and not out[0].get("thread_ts"))

# ── REACT / PASS — a channel worth reading, not a channel full of Moses ─────
reset(); stub_conversation(kind="react", value="100")
out = fire({"user": BRAD, "text": "Moses, we shipped it"}, ts="8.1")
check("REACT a reaction posts NO message", out == [])
check("REACT it reacts to the right message",
      len(reacted) == 1 and reacted[0]["ts"] == "8.1" and reacted[0]["name"] == "100")

reset(); stub_conversation(kind="react", value="not_a_real_emoji")
out = fire({"user": BRAD, "text": "Moses, thoughts"}, ts="8.2")
check("REACT an emoji Slack rejects falls back to saying it, not to silence",
      len(out) == 1 and ":not_a_real_emoji:" in out[0]["text"])

reset(); stub_conversation(kind="pass", value="")
out = fire({"user": BRAD, "text": "Moses, nothing to add here"}, ts="8.3")
check("PASS staying quiet posts nothing and reacts to nothing", out == [] and reacted == [])

# 'pass' is a DECISION and must not fall through to the deterministic roster dump — that would turn
# "I have nothing to add" into the noisiest possible message.
check("PASS does not fall back to the deterministic answer", out == [])

# A run of silences still counts toward the cap, or a stuck agent could pass forever unbounded.
reset(); stub_conversation(kind="pass", value="")
history[:] = [{**ATLAS, "text": "Moses?"}]
fire({**ATLAS, "text": "Moses, still there?"}, ts="8.4")
check("PASS a bot-prompted silence is still recorded", pacing.PACING_FILE.exists())

# ── FOLLOW-UP: staying in a conversation without being re-named ─────────────
# The real failure this fixes: Brad said "welcome back ... Moses", Moses answered, and Atlas asked
# "what are you for?" — a direct question with no "Moses" in it, because nobody repeats a name
# mid-conversation. Moses sat silent.
reset(); calls = stub_conversation()
history[:] = [{**ATLAS, "text": "what are you for, exactly?"}, MOSES_SAID,
              {"user": BRAD, "text": "welcome back Moses"}]
out = fire({**ATLAS, "text": "what are you for, exactly?"}, ts="9.1")
check("FOLLOWUP Atlas's unnamed question gets an answer", len(out) == 1)
check("FOLLOWUP it is still marked reactive", calls and calls[0]["reactive"] is True)

reset(); calls = stub_conversation()
history[:] = [{"user": JON, "text": "and what about the backups?"}, MOSES_SAID]
out = fire({"user": JON, "text": "and what about the backups?"}, ts="9.2")
check("FOLLOWUP a human follow-up needs no name either", len(out) == 1)

# Self-limiting: three messages of silence and he is out, with no timer and no state.
reset(); calls = stub_conversation()
history[:] = [{"user": JON, "text": "d"}, {"user": BRAD, "text": "c"}, {"user": JON, "text": "b"},
              MOSES_SAID]
out = fire({"user": JON, "text": "d"}, ts="9.3")
check("FOLLOWUP he drops out after 3 messages of not speaking", out == [] and calls == [])

# He must never follow up on his OWN message — addressed() returns None for those too, so without
# an explicit check this is a loop with a single participant.
reset(); calls = stub_conversation()
history[:] = [MOSES_SAID, MOSES_SAID]
out = fire(moses_said("thinking out loud"), ts="9.4")
check("FOLLOWUP he never follows up on himself", out == [] and calls == [])

# A follow-up must not be read as a command that writes to the memory corpus.
reset(); calls = stub_conversation()
history[:] = [{"user": JON, "text": "x"}, MOSES_SAID]
out = fire({"user": JON, "text": "add a retry to my list of things that broke"}, ts="9.5")
check("FOLLOWUP prose is never captured to the corpus",
      calls and (not out or "Noted" not in (out[0].get("text") or "")))

# With conversation unavailable, a follow-up stays quiet rather than dumping the roster at someone
# who never addressed him.
reset()
listener.conversation.reply = lambda *a, **k: ("none", "", 0.0)
history[:] = [{"user": JON, "text": "y"}, MOSES_SAID]
out = fire({"user": JON, "text": "y"}, ts="9.6")
check("FOLLOWUP no conversation means silence, not a roster dump", out == [])

# But being addressed BY NAME still falls back to the deterministic answer.
reset()
listener.conversation.reply = lambda *a, **k: ("none", "", 0.0)
out = fire({"user": JON, "text": "Moses, status"}, ts="9.7")
check("FOLLOWUP being named still gets the deterministic fallback", len(out) == 1)

# ── LEARNED end to end: strip, file, credit, tell Brad ──────────────────────
import pathlib, tempfile as _tf  # noqa: E402
_MEM = pathlib.Path(_tf.mkdtemp(prefix="moses-mem-"))
_orig_file = listener.dispatch.file_learning
listener.dispatch.file_learning = lambda body, source="Atlas", link="", memory=None: \
    _orig_file(body, source=source, link=link, memory=_MEM)
listener.web.chat_getPermalink = lambda channel, message_ts: {"permalink": "https://slack/p1"}

ATLAS_NAMED = {"user": "U_ATLAS", "bot_id": "B_ATLAS", "username": "Atlas"}

reset(); stub_conversation(text="Fair point.\nLEARNED: bound it, don't ban it")
history[:] = [{**ATLAS_NAMED, "text": "Moses, thoughts?"}]
out = fire({**ATLAS_NAMED, "text": "Moses, thoughts?"}, ts="10.1")
posted_text = out[0]["text"] if out else ""
check("LEARNED the marker never reaches the channel", "LEARNED:" not in posted_text)
check("LEARNED the actual reply still posts", posted_text.startswith("Fair point."))
check("LEARNED Brad is told in the SAME message", "look into further" in posted_text.lower())

filed = (_MEM / "look-into-further.md").read_text()
check("LEARNED it reached the list", "bound it, don't ban it" in filed)
check("LEARNED credited to Atlas by his display name", "from Atlas" in filed)
check("LEARNED carries the permalink", "https://slack/p1" in filed)

# A reply that is ONLY a learning must still say something, not post an empty message.
reset(); stub_conversation(text="LEARNED: a second distinct lesson")
history[:] = [{**ATLAS_NAMED, "text": "Moses?"}]
out = fire({**ATLAS_NAMED, "text": "Moses?"}, ts="10.2")
check("LEARNED a marker-only reply still posts a note, never an empty message",
      len(out) == 1 and out[0]["text"].strip())

# Filing the same lesson twice must not announce it again.
reset(); stub_conversation(text="Same again.\nLEARNED: bound it, don't ban it")
history[:] = [{**ATLAS_NAMED, "text": "Moses?"}]
out = fire({**ATLAS_NAMED, "text": "Moses?"}, ts="10.3")
check("LEARNED a repeat lesson is not announced again",
      "look into further" not in (out[0]["text"] if out else "").lower())

listener.dispatch.file_learning = _orig_file

# ── THE 8AM MISFIRE: #ops, 2026-08-14 ──────────────────────────────────────
# Birdeye posted his ops report; Moses counted it as his own reply (shared bot_id), tripped the cap,
# and announced "I'll pick this up later" into a channel he is not conversational in. Two faults:
# identity, and running the pacing gate before knowing the message was even for him.
OPS = "C_OPS"          # deliberately NOT in MOSES_CHAT_CHANNELS


def fire_in(chan, event, ts="11.0"):
    posted.clear()
    req = types.SimpleNamespace(type="events_api", envelope_id="e",
                                payload={"event": {"type": "message", "channel": chan, "ts": ts, **event}})
    listener.handle(req, FakeSM())
    return posted


BIRDEYE = {"bot_id": "B_MOSES", "username": "Birdeye", "subtype": "bot_message"}
THERAPIST = {"bot_id": "B_MOSES", "username": "Therapist", "subtype": "bot_message"}

reset(); calls = stub_conversation()
history[:] = [moses_said(pacing.BOWING_OUT)] * 3
out = fire_in(OPS, {**BIRDEYE, "text": ":white_check_mark: *Reserve* — all clear"}, ts="11.1")
check("OPS Moses says NOTHING to a persona report in a non-chat channel", out == [])
check("OPS and never calls the model there", calls == [])

reset(); calls = stub_conversation()
out = fire_in(OPS, {**THERAPIST, "text": "something needs attention"}, ts="11.2")
check("OPS the same holds for Therapist", out == [] and calls == [])

# Even in the conversational channel, the bow-out only fires if he was actually being talked to.
# He is over the cap, but the conversation has moved on without him — three machine messages since
# he last spoke, and this one does not name him. Announcing a withdrawal from a conversation he is
# no longer in is noise. (When he IS mid-conversation, bowing out is correct — the next case.)
reset(); calls = stub_conversation()
history[:] = ([{**ATLAS, "text": "1"}, {**ATLAS, "text": "2"}, {**ATLAS, "text": "3"}]
              + [moses_said("a")] * pacing.MAX_OWN_REPLIES)
out = fire({**ATLAS, "text": "just thinking out loud over here"}, ts="11.3")
check("CAP no bow-out once the conversation has moved on without him", out == [])

reset(); calls = stub_conversation()
history[:] = [moses_said("a")] * pacing.MAX_OWN_REPLIES
out = fire({**ATLAS, "text": "Moses, still with me?"}, ts="11.4")
check("CAP he DOES bow out when addressed and over the cap",
      len(out) == 1 and out[0]["text"] == pacing.BOWING_OUT)

# And the identity fix: another persona's posts must not count toward his cap.
reset(); calls = stub_conversation()
history[:] = [{**BIRDEYE, "text": "x"}, {**THERAPIST, "text": "y"}, {**BIRDEYE, "text": "z"}]
out = fire({**ATLAS, "text": "Moses, thoughts?"}, ts="11.5")
check("CAP other personas' posts do not count as his own replies", len(out) == 1 and calls)

# ── PHASE 1: diagnose an alarm, never fix it ────────────────────────────────
os.environ["MOSES_ALARM_CHANNELS"] = OPS
_diag_calls = []


def stub_diagnose(report="VERDICT: false-alarm\nSUMMARY: the remote is fine", cost=0.13):
    _diag_calls.clear()

    def _d(alarm, by="a persona"):
        _diag_calls.append({"alarm": alarm, "by": by})
        return report, cost
    listener.diagnose.diagnose = _d


reset(); stub_diagnose()
out = fire_in(OPS, {**THERAPIST, "text": ":warning: *Knight is not ready* — cannot reach the push remote"}, ts="12.1")
check("DIAG an alarm from a persona is diagnosed", len(_diag_calls) == 1)
check("DIAG it is credited to whoever raised it", _diag_calls[0]["by"] == "Therapist")
check("DIAG the diagnosis is posted", len(out) == 1 and "Diagnosis" in out[0]["text"])
check("DIAG threaded under the alarm, not loose in the channel", out[0].get("thread_ts") == "12.1")
check("DIAG a false-alarm verdict is flagged green", out[0]["text"].startswith("🟢"))

# The measurement Phase 2 depends on.
rows = [json.loads(l) for l in listener.diagnose.LEDGER.read_text().splitlines()]
check("DIAG the verdict is recorded to the ledger", rows and rows[-1]["verdict"] == "false-alarm")
check("DIAG the ledger names the persona", rows[-1]["by"] == "Therapist")

reset(); stub_diagnose(report="VERDICT: real\nSUMMARY: backup is 3 days stale")
out = fire_in(OPS, {**BIRDEYE, "text": "backup FAILED overnight"}, ts="12.2")
check("DIAG a real verdict is flagged red", out and out[0]["text"].startswith("🔴"))

# A GREEN morning must cost nothing at all — this runs unattended, every day.
reset(); stub_diagnose()
out = fire_in(OPS, {**BIRDEYE, "text": ":white_check_mark: *Reserve* — all clear (backup ok, disks ok)"}, ts="12.3")
check("DIAG a clean report is never diagnosed", _diag_calls == [] and out == [])

# Moses must never diagnose his own diagnosis — that is a loop with one participant.
reset(); stub_diagnose()
out = fire_in(OPS, moses_said("🟢 *Diagnosis* — VERDICT: false-alarm, nothing is wrong"), ts="12.4")
check("DIAG he never diagnoses himself", _diag_calls == [] and out == [])

# Outside the alarm channels the whole path is off.
reset(); stub_diagnose()
out = fire_in("C_SOMEWHERE_ELSE", {**THERAPIST, "text": ":warning: not ready"}, ts="12.5")
check("DIAG the path is off outside the alarm channels", _diag_calls == [] and out == [])

# A human saying something worrying is NOT a persona alarm — it goes down the conversation path.
reset(); stub_diagnose(); stub_conversation()
out = fire_in(OPS, {"user": BRAD, "text": ":warning: something needs attention here"}, ts="12.6")
check("DIAG a human's message never triggers the diagnosis path", _diag_calls == [])

# Degradation: a crash in diagnosis must not take the listener down or post a half-answer.
reset()
listener.diagnose.diagnose = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
out = fire_in(OPS, {**THERAPIST, "text": ":warning: not ready"}, ts="12.7")
check("DIAG a crash degrades to silence, not a broken post", out == [])

# ── PROPOSE → confirm → file. The model never writes. ───────────────────────
import proposals as _prop  # noqa: E402
_MEM2 = pathlib.Path(_tf.mkdtemp(prefix="moses-todo-"))
_orig_file_tasks = _prop.file_tasks
_prop.file_tasks = lambda items, mod, memory=None: _orig_file_tasks(items, mod, memory=_MEM2)


def clear_proposals():
    _fresh_registry()


def _project_ideas():
    """Ideas filed against viatica in the test registry — where a confirmed proposal now lands."""
    import json as _json
    try:
        d = _json.loads((_MEM2 / "projects.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    # ACCEPTED only. A proposal now lives on the project too, in the `proposed` state, so counting
    # every idea would report a suggestion as filed work — the exact distinction under test.
    return [i.get("title", "") for p in d.get("projects", []) for i in (p.get("ideas") or [])
            if i.get("state") != "proposed"]


(_MEM2 / "projects.json").write_text(_json_dumps_registry(), encoding="utf-8")

reset(); clear_proposals()
stub_conversation(text="That generalizes.\nPROPOSE: [viatica] make probes emit identity and scope")
out = fire({"user": BRAD, "text": "Moses, what should we do about it?"}, ts="13.1")
# NOT named `posted` — that is the module-level list every test reads from, and shadowing it with a
# string broke every subsequent fire().
body = out[0]["text"] if out else ""
check("PROPOSE the marker never reaches the channel", "PROPOSE:" not in body)
check("PROPOSE the reply still posts", body.startswith("That generalizes."))
check("PROPOSE it is offered, not filed", "Proposed" in body and len(_prop.pending()) == 1)
check("PROPOSE nothing is filed against the project yet", not _project_ideas())

# Brad confirms with a bare word — one pending, so no id needed.
reset()
out = fire({"user": BRAD, "text": "confirm"}, ts="13.2")
check("CONFIRM a bare confirm files it", out and "Filed" in out[0]["text"])
check("CONFIRM it reached the project it named",
      "make probes emit identity and scope" in _project_ideas())
check("CONFIRM pending is emptied", _prop.pending() == [])

# ONLY BRAD. Jon confirming must do nothing at all.
clear_proposals(); _prop.add("a task only Brad may file", CHAN, project="viatica")
reset()
out = fire({"user": JON, "text": "confirm"}, ts="13.3")
check("OWNER Jon cannot file a task", len(_prop.pending()) == 1)

# Ordinary conversation must never file — the whole-message rule.
reset(); stub_conversation()
out = fire({"user": BRAD, "text": "Moses, yes that's a good point about the probes"}, ts="13.4")
check("SAFE a 'yes' inside a sentence does not file", len(_prop.pending()) == 1)

# Two pending and a bare yes: say which, never guess.
_prop.add("a second task", CHAN, project="viatica")
reset()
out = fire({"user": BRAD, "text": "confirm"}, ts="13.5")
check("AMBIGUOUS two pending asks which, and files neither",
      out and "Which one" in out[0]["text"] and len(_prop.pending()) == 2)
reset()
out = fire({"user": BRAD, "text": "confirm all"}, ts="13.6")
check("MULTI 'confirm all' files both", _prop.pending() == [])

# ── A checkmark from Brad files it; from anyone else it does not ───────────
def react_event(user, emoji, ts):
    posted.clear()
    req = types.SimpleNamespace(type="events_api", envelope_id="e", payload={"event": {
        "type": "reaction_added", "user": user, "reaction": emoji,
        "item": {"type": "message", "channel": CHAN, "ts": ts}}})
    listener.handle(req, FakeSM())
    return posted


clear_proposals()
it = _prop.add("file me with a checkmark", CHAN, project="viatica")
_prop.attach_message(it["id"], "14.1")
listener.SEEN.clear()
out = react_event(JON, "white_check_mark", "14.1")
check("REACT Jon's checkmark does NOT file", len(_prop.pending()) == 1 and out == [])
listener.SEEN.clear()
out = react_event(BRAD, "popcorn", "14.1")
check("REACT an unrelated emoji does not file", len(_prop.pending()) == 1)
listener.SEEN.clear()
out = react_event(BRAD, "white_check_mark", "14.1")
check("REACT Brad's checkmark files it", _prop.pending() == [] and out and "Filed" in out[0]["text"])
listener.SEEN.clear()
out = react_event(BRAD, "white_check_mark", "99.9")
check("REACT a checkmark on an unrelated message does nothing", out == [])

_prop.file_tasks = _orig_file_tasks
clear_proposals()

# ── THE SEAM: is the coalescer actually ON the conversation path? ───────────
#
# coalesce_test.py proves the Coalescer class works. It says nothing about whether listener.handle
# calls it, or calls it in the right place — and "the logic was right, the wiring was not" is the
# failure this project keeps hitting. So this drives handle() itself, concurrently, the way Socket
# Mode does.
import threading as _th
listener.coalesce.COALESCER.window = 0.4

reset(); calls = stub_conversation()
_barrier = _th.Barrier(3)
_replies = []


def _burst(i):
    _barrier.wait()
    req = types.SimpleNamespace(
        type="events_api", envelope_id="e",
        payload={"event": {"type": "message", "channel": CHAN, "ts": f"70.{i}",
                           "user": BRAD, "text": f"Moses, thought {i}"}})
    before = len(posted)
    listener.handle(req, FakeSM())
    _replies.append(len(posted) - before)


posted.clear()
_ts = [_th.Thread(target=_burst, args=(i,)) for i in range(3)]
for t in _ts:
    t.start()
for t in _ts:
    t.join()
check("SEAM three simultaneous messages get ONE reply, not three", len(posted) == 1)
check("SEAM and the model was only asked once", len(calls) == 1)

# NOT A MUTE. The next message, after the window, is answered normally.
reset(); calls = stub_conversation()
posted.clear()
fire({"user": BRAD, "text": "Moses, a later question"}, ts="71.0")
check("SEAM a message after the window is still answered", len(posted) == 1)

# ── The deterministic paths must NOT be debounced ──────────────────────────
# Each of these needs its own response, and each must be instant. Putting them behind the window
# would make the off switch laggy — the one control that has to work the moment it is typed.
reset(); stub_conversation()
listener.coalesce.COALESCER.window = 30.0        # long enough that a debounce would be obvious
listener.coalesce.COALESCER.claim(("C_CHAT", ""))  # a window is already open on this channel

posted.clear()
_t0 = time.time()
fire({"user": BRAD, "text": "Moses, stand down"}, ts="72.0")
check("SEAM the safeword is NOT debounced — it fires with a window open",
      len(posted) == 1 and silence.FLAG.exists())
check("SEAM and it fires immediately", time.time() - _t0 < 1.0)
silence.FLAG.unlink(missing_ok=True)

reset(); stub_conversation()
listener.coalesce.COALESCER._open.clear()
listener.coalesce.COALESCER.claim((CHAN, ""))
_held = _prop.add("A task worth doing that is filed by confirming", CHAN, project="viatica")
posted.clear()
_t0 = time.time()
fire({"user": BRAD, "text": "confirm"}, ts="73.0")
check("SEAM a confirmation is NOT debounced", len(posted) == 1 and time.time() - _t0 < 1.0)
clear_proposals()
listener.coalesce.COALESCER._open.clear()
listener.coalesce.COALESCER.window = 0.05


# ── SEAM: Brad's multi-action line reaches the mechanism ───────────────────
reset(); stub_conversation()
clear_proposals()
_a = _prop.add("First real task", CHAN, project="viatica")
_b = _prop.add("A duplicate to drop", CHAN, project="viatica")
_c = _prop.add("Another to drop", CHAN, project="viatica")
_filed_now = []
_orig_ft2 = _prop.file_tasks
_prop.file_tasks = lambda items, mod, memory=None: (
    _filed_now.extend(t["task"] for t in items) or _prop.drop([t["id"] for t in items])
    or ([t["task"] for t in items], []))

out = fire({"user": BRAD, "text": f"confirm `{_a['id']}`, dismiss `{_b['id']}` and dismiss `{_c['id']}`"},
           ts="80.0")
check("SEAM Brad's exact line files one and drops two",
      _filed_now == ["First real task"] and _prop.pending() == [])
check("SEAM and he is told what happened", out and "Filed" in out[0]["text"] and "Dropped" in out[0]["text"])

# An id that matches nothing must produce a WORD, not silence — the original failure was invisible.
clear_proposals()
_d = _prop.add("Still pending", CHAN, project="viatica")
listener.SEEN.clear()
out = fire({"user": BRAD, "text": f"confirm {_d['id']} deadbeef"}, ts="81.0")
check("SEAM an unknown id is called out", out and "deadbeef" in out[0]["text"])

# Prose must still not reach it, through the real listener rather than the parser alone.
clear_proposals()
_e = _prop.add("Must survive prose", CHAN, project="viatica")
listener.SEEN.clear()
fire({"user": BRAD, "text": f"I'll confirm {_e['id']} with Jon before we file it"}, ts="82.0")
check("SEAM prose naming an id files nothing", len(_prop.pending()) == 1)

# Only Brad. Jon naming ids must not file or drop anything.
listener.SEEN.clear()
fire({"user": JON if "JON" in dir() else "U_JON", "text": f"dismiss {_e['id']}"}, ts="83.0")
check("SEAM someone else naming an id changes nothing", len(_prop.pending()) == 1)

_prop.file_tasks = _orig_ft2
clear_proposals()


# ── Knight reports back to the conversation that started him ────────────────
# 2026-09-11: "Ping me here when Knight is done." Promised twice, delivered never — nothing could.
# The model's turn starts the job (through the MCP server, which never learns the channel), so the
# listener must notice the job appear during the turn and record where it came from.
reset(); stub_conversation()
_jobs = os.environ["KNIGHT_JOBS"]
os.makedirs(os.path.join(_jobs, "older-job"), exist_ok=True)
_stubbed = listener.conversation.reply


def _starts_a_job(*a, **kw):
    os.makedirs(os.path.join(_jobs, "job-from-this-turn"))
    return _stubbed(*a, **kw)


listener.conversation.reply = _starts_a_job
history[:] = []
out = fire({"user": BRAD, "text": "Moses, what do you think?"}, ts="95.1")
_n = os.path.join(_jobs, "job-from-this-turn", "notify.json")
check("KNIGHT a job started during Brad's turn will report back to this channel",
      os.path.exists(_n) and json.load(open(_n)) == {"channel": CHAN, "thread_ts": "", "user": BRAD})
check("KNIGHT a job that already existed is not re-pointed here",
      not os.path.exists(os.path.join(_jobs, "older-job", "notify.json")))
check("KNIGHT and Brad still gets his reply", len(out) == 1)


# ── Deduplication ───────────────────────────────────────────────────────────
reset(); stub_conversation()
fire({"user": BRAD, "text": "Moses, hello"}, ts="6.1")
out = fire({"user": BRAD, "text": "Moses, hello"}, ts="6.1")      # same ts = same message
check("the same message is never answered twice", out == [])

passed = sum(1 for _, ok in results if ok)
failed = len(results) - passed
print(f"\n  {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
