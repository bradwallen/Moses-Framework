#!/usr/bin/env python3
"""Moses — the Slack surface for Reserve's ops, and a participant in one channel.

STILL NO ANTHROPIC API CALLS. Not a reduced number — none. Brad's rule (2026-08-05): a Pro plan
covers claude.ai and Claude Code, NOT `sk-ant-` pay-per-token, and he does not want a meter running
on a box nobody watches. An earlier version sent the whole ~26K memory corpus to the Messages API on
every question; that is gone and is not coming back.

Conversation (added 2026-08-13) shells out to the installed `claude` CLI as brad, on the
subscription he already pays for — so it is model-ful without being API-billed. See conversation.py,
which holds the reasoning and every flag that keeps the call small and toolless.

WHAT RUNS WHERE
  Moses add "call the CPA" to my list   -> deterministic. String handling with a correct answer;
                                           sending it to a model would make it less reliable.
  Moses <anything else>                 -> conversation.py in an opted-in channel, otherwise the
                                           deterministic roster-and-status answer.
  /birdeye status|backup|jobs|job|log   -> unchanged.

ADDRESSING HIM (Brad, 2026-08-13)
Like a person: his name in the vocative — "Hey Moses, …", "…, Moses?", or "Moses" alone. Talk ABOUT
him ("I'll ask Moses") is not a command. That needs message.channels / message.groups, which deliver
EVERY message in every channel he is in, so addressing.py — not the subscription — is what keeps him
quiet. See addressing.py; pinned by addressing_test.py.

Being named STARTS a conversation; it does not have to continue it. If his own last message is
within the last few, the next one reaches him without his name — and he can answer PASS and say
nothing. See addressing.in_conversation.

TALKING TO OTHER AGENTS
He shares a channel with Atlas, and two agents answering each other have no natural stopping point.
He is not forbidden from replying to a bot; he is BOUNDED — pacing.py caps how many times he may
reply in a row without a human speaking, how many bot-prompted replies he may post in a day, and how
close together two of them may be. Humans are exempt from all three entirely.

THE OFF SWITCH
silence.py. "Stand down" from anyone in the channel, checked before any of this.

Socket Mode, because Reserve is behind home NAT with no public URL. This remains the SINGLE Socket
Mode process for Reserve: Slack DISTRIBUTES payloads across an app's open connections rather than
broadcasting, so a second process under the same app would swallow some /birdeye commands silently.
"""

import sys as _sys, pathlib as _pl  # noqa: E401
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent / "lib"))
import moses_env as _env  # noqa: E402 — whose home, where Moses lives, the operator's settings
import os
import re
import subprocess
import sys
import datetime as _dt
import time
import json
import threading
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import addressing  # noqa: E402
import coalesce  # noqa: E402
import attachments
import conversation  # noqa: E402
import diagnose  # noqa: E402
import dispatch  # noqa: E402
import pacing  # noqa: E402
import proposals  # noqa: E402
import silence  # noqa: E402
import knight_notify  # noqa: E402

APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")
BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
# Defaults to the operator's own corpus. It used to default to /opt/moses/memory — a symlink
# that existed only while Moses ran from a copy under /opt, and which went with that directory
# on 2026-09-02. A stale default here is not harmless: if the env line were ever lost he would
# read an empty path and answer from nothing while looking perfectly healthy.
MEMORY_DIR = Path(os.environ.get("MOSES_MEMORY_DIR", str(Path.home() / ".claude" / "memory")))
MOSES_NAME = _env.setting("MOSES_NAME", "Moses")
MOSES_EMOJI = _env.setting("MOSES_EMOJI", ":scroll:")
BIRDEYE_NAME = os.environ.get("BIRDEYE_NAME", "Birdeye")
BIRDEYE_EMOJI = os.environ.get("BIRDEYE_EMOJI", ":eagle:")

# Who may lift the safeword. Anyone can stop Moses; only Brad restarts him — stopping must be
# frictionless in the moment you need it, restarting is a deliberate act by the person who owns the
# consequences. Derived from the channel member list on 2026-08-13; override if it is ever wrong.
OWNER_ID = _env.setting("MOSES_OWNER_SLACK_ID")


def alarm_channels() -> set[str]:
    """Where a persona's trouble report gets diagnosed. Opt-in, like conversation.

    Defaults to #ops, which is where Birdeye and Therapist report. Empty means the whole diagnosis
    path is off — the same safe default as the chat allowlist.
    """
    raw = _env.setting("MOSES_ALARM_CHANNELS")
    return {c.strip() for c in raw.replace(",", " ").split() if c.strip()}

# ANTHROPIC_API_KEY is deliberately NOT read. If this file ever needs it again, that is a decision
# to make out loud, not a line to quietly add back.
missing = [n for n, v in (("SLACK_APP_TOKEN", APP_TOKEN), ("SLACK_BOT_TOKEN", BOT_TOKEN)) if not v]
if missing:
    print(f"moses: missing {', '.join(missing)}", file=sys.stderr)
    sys.exit(1)

JOB_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9]+$")

BIRDEYE_HELP = "\n".join([
    "*Birdeye* — Reserve ops",
    "`/birdeye status` — full ops report (backup, quota, disks, what's in flight)",
    "`/birdeye backup` — last backup result only",
    "`/birdeye jobs` — every handed-off job: state and how long it's been going",
    "`/birdeye job <job-id>` — one job in detail, with live progress",
    "`/birdeye log <job-id>` — that job's full output",
])


# WHERE THE ACCOUNTABILITY ANSWER LIVES — and why this is a second variable rather than a reuse of
# MOSES_STATE.
#
# Two different things were sharing one name. The 08:00 standup runs as root and records each
# persona's last successful report under /var/lib/moses/personas/. The listener runs as brad and
# needs somewhere it can WRITE (pacing, proposals, diagnoses), so MOSES_STATE points at
# ~/.local/state/moses. That directory has no personas/ in it and never will.
#
# The consequence, seen in Slack on 2026-08-22: Brad asked Moses to fix something and Moses replied
# that Birdeye, Big Pipe, Tagilla, Therapist and himself had "never reported successfully" — while
# their reports from 08:00 that morning sat in the same channel, and the real state file said every
# one of them had succeeded. He was reading an empty store and stating the result as fact.
#
# That is worse than being unable to act. An agent that reports its own world incorrectly cannot be
# trusted with anything, and this is the exact number Brad would use to decide whether a persona had
# gone quiet. Reads of the shared registry now come from the canonical store; the listener's own
# writable state stays where it is.
REGISTRY_STATE = os.environ.get("MOSES_REGISTRY_STATE", "/var/lib/moses")


def _run(argv: list[str], limit: int = 3500) -> str:
    """Run a whitelisted command. Slash-command text is attacker-controllable in principle (anyone
    in the workspace can type it), so nothing from Slack is ever passed to a shell.

    The `moses` CLI is invoked against the CANONICAL registry state, not the listener's own — see
    REGISTRY_STATE. Truncation says so out loud: the roster once came back tail-clipped and Moses
    posted a fragment beginning mid-sentence, which reads as a malfunction rather than as a long
    answer that got cut.
    """
    env = {**os.environ, "MOSES_STATE": REGISTRY_STATE}
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=120, env=env)
    except subprocess.TimeoutExpired:
        return "timed out"
    out = ((p.stdout or "") + (p.stderr or "")).strip() or "(no output)"
    if len(out) <= limit:
        return out
    return out[:limit].rstrip() + f"\n… (truncated — run `{argv[0].split('/')[-1]} {' '.join(argv[1:])}` on the box for all of it)"


def birdeye_command(text: str) -> tuple[str, bool]:
    """Return (message, already_posted) — the report modes post to the channel themselves."""
    parts = text.split()
    cmd = parts[0].lower() if parts else "status"

    if cmd in ("help", "-h", "--help"):
        return BIRDEYE_HELP, False
    if cmd in ("status", "report"):
        _run(["/usr/local/bin/birdeye-report", "full"])
        return "", True
    if cmd == "backup":
        _run(["/usr/local/bin/birdeye-report", "backup"])
        return "", True
    if cmd in ("jobs", "list"):
        return "```" + _run(["/usr/local/bin/birdeye", "list"]) + "```", False
    if cmd in ("job", "show"):
        if len(parts) < 2:
            return "Usage: `/birdeye job <job-id>` — get ids from `/birdeye jobs`", False
        if not JOB_ID.match(parts[1]):
            return f"`{parts[1][:40]}` is not a job id. Try `/birdeye jobs`.", False
        return "```" + _run(["/usr/local/bin/birdeye", "show", parts[1]]) + "```", False
    if cmd == "log":
        if len(parts) < 2:
            return "Usage: `/birdeye log <job-id>` — get ids from `/birdeye jobs`", False
        if not JOB_ID.match(parts[1]):
            return f"`{parts[1][:40]}` is not a job id. Try `/birdeye jobs`.", False
        return "```" + _run(["/usr/local/bin/birdeye", "log", parts[1]]) + "```", False

    return f"Don't know `{cmd[:30]}`.\n\n{BIRDEYE_HELP}", False


def answer(question: str, channel: str = "") -> str:
    """Everything that is not a capture, in a channel where no model runs.

    THIS REPLY USED TO CONTAIN THREE UNTRUE THINGS, all posted with total confidence:

      1. "I don't call the Claude API any more, so there's no model here to reason with." Moses DOES
         reason — through the `claude` CLI on Brad's subscription — just not in every channel. The
         allowlist is a deliberate cost and blast-radius control, and saying "there is no model"
         instead of "not in this channel" turns a config into a permanent incapacity. Brad read it
         as the latter.
      2. The accountability list, which claimed every persona had never reported while their reports
         from that morning sat in the same channel. Cause was a state directory split; see
         REGISTRY_STATE.
      3. A tail-truncated roster dump that began mid-sentence.

    So: say what is actually true, name where the conversational half lives, and — the part that
    matters — do not dead-end an instruction. Anything that reads as a request to ACT gets written
    down before this returns, because "I can't do that" losing the request is the failure Brad
    actually feels; a recorded request survives until something can pick it up.
    """
    status = _run(["/usr/local/bin/moses", "status"], limit=900)
    where = ", ".join(f"<#{c}>" for c in sorted(conversation.enabled_channels())) or "(nowhere — conversation is switched off)"

    captured = _capture_unactionable(question)
    head = (
        "I can't act on that here. This channel is set up for commands only — my conversational "
        f"half runs in {where}, so ask me there and I'll reason it through.\n"
    )
    if captured:
        head += (f"\n:memo: I've written the request down so it isn't lost — {captured} "
                 "Tell me which project it belongs to and I'll file it there.\n")

    return (
        head
        + "\n*What I can do in this channel*\n"
        "   • `add \"<thing>\" to my list` / `I have an idea: <thing>` — I'll write it down\n"
        "   • `/birdeye status` — Reserve ops\n"
        "   • `moses roster|status|standup|agents` on the box\n\n"
        f"*Who's behind*\n{status}"
    )


def _capture_unactionable(text: str) -> str:
    """Record a request this channel cannot act on, and return what was recorded (or "").

    Deliberately narrow: only an imperative aimed at Moses. A broad "capture anything unhandled"
    would fill the todo list with half-sentences and greetings, and a list nobody trusts is a list
    nobody reads — the same failure as an alert that fires on everything.

    Never raises. Losing the reply because the capture failed would be a worse outcome than losing
    the capture.
    """
    t = (text or "").strip()
    if len(t) < 12 or len(t) > 400:
        return ""
    if t.endswith("?"):
        return ""                      # a question is not an instruction
    if not _IMPERATIVE.search(t):
        return ""
    try:
        # dispatch.MEMORY is read HERE rather than relied on as a default argument. Python binds
        # default arguments once at import, so `dispatch.capture(intent)` would write to the real
        # corpus no matter what a test set afterwards — which is exactly what happened while this
        # was being written: a test appended a line to Brad's actual todo list. A seam that cannot
        # be redirected is a seam that gets tested against production.
        return dispatch.capture(
            dispatch.Intent(kind="capture", body=t, target="ideas"),
            memory=dispatch.MEMORY,
        )
    except Exception as e:                                   # noqa: BLE001
        print(f"moses: could not capture request ({type(e).__name__})", flush=True)
        return ""


# Leading verbs that mean "do something", matched at the start of the message only. Explicit, not
# inferred — same reasoning as dispatch.classify: a regex that misses costs nothing, a classifier
# that guesses wrong files noise forever.
_IMPERATIVE = re.compile(
    r"^\s*(?:<@[A-Z0-9]+>[\s,]*)?(?:moses[\s,:]*)?"
    r"(?:go\s+ahead\s+and\s+)?"
    r"(fix|resolve|investigate|task|assign|delegate|hand|build|implement|deploy|check|look\s+into|"
    r"sort|handle|take\s+care|get\s+\w+\s+to)\b",
    re.I,
)


def recent(channel: str, thread_ts: str = "", limit: int = 14) -> list[dict]:
    """The last few messages, NEWEST FIRST — context for a reply and input to pacing.

    In a thread, read the thread; otherwise read the channel. `conversations.history` does not
    include thread replies at all, so without this a threaded conversation would look empty to
    Moses and he would answer with no idea what was said.

    **The two endpoints disagree about order** — verified against the live channel, not assumed:
    history returns newest-first, replies returns oldest-first (parent, then ascending). Replies are
    reversed here so everything downstream sees one convention. Getting this wrong would feed him
    the conversation backwards, which reads as a plausible answer to the wrong message.

    Returns [] on any failure. A reply without context degrades to the deterministic answer; a
    listener that raised here would go silent, which is worse.
    """
    try:
        if thread_ts:
            r = web.conversations_replies(channel=channel, ts=thread_ts, limit=limit)
            return list(reversed(list(r.get("messages") or [])))
        r = web.conversations_history(channel=channel, limit=limit)
        return list(r.get("messages") or [])
    except Exception as e:
        print(f"moses: could not read {channel} ({type(e).__name__})", flush=True)
        return []


def speaker_name(event: dict) -> str:
    """Who said it, for attribution on a filed learning.

    Slack stamps a bot's display name on the message itself (`username`, or `bot_profile.name`), so
    no `users:read` scope is needed for the case that matters — the other agents. A human falls back
    to their id, which is honest: inventing a name on a provenance record would defeat the record.
    """
    for key in ("username",):
        if event.get(key):
            return str(event[key])
    prof = event.get("bot_profile") or {}
    if prof.get("name"):
        return str(prof["name"])
    return str(event.get("user") or "someone")


def permalink(channel: str, ts: str) -> str:
    """A link back to the message. Best-effort — a learning is worth filing without one."""
    if not ts:
        return ""
    try:
        return str(web.chat_getPermalink(channel=channel, message_ts=ts).get("permalink") or "")
    except Exception:
        return ""


def react(channel: str, ts: str, name: str) -> bool:
    """Add an emoji reaction to a message. True if Slack accepted it.

    Needs `reactions:write`. Returns False rather than raising on any failure — an unknown emoji
    name comes back as `invalid_name`, and "already_reacted" is a success in every sense that
    matters. The caller posts the emoji as text instead, so a rejected reaction never turns into a
    dropped reply.
    """
    if not ts or not name:
        return False
    try:
        web.reactions_add(channel=channel, timestamp=ts, name=name)
        return True
    except Exception as e:
        if "already_reacted" in str(e):
            return True
        print(f"moses: reactions.add :{name}: failed ({type(e).__name__}: {str(e)[:80]})", flush=True)
        return False


def handle(req: SocketModeRequest, sm: SocketModeClient) -> None:
    # Ack first — Slack retries anything not acknowledged within 3 seconds.
    sm.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

    payload = req.payload or {}
    # Log the envelope TYPE only, never message text. Since message.channels arrived, this process
    # sees every message in every channel Moses is in; writing those to the journal would turn an
    # ops log into a transcript of Brad's workspace. What's needed to debug "Moses didn't answer" is
    # whether the event was delivered at all — that, and nothing more, is what gets recorded.
    ev_type = (payload.get("event") or {}).get("type")
    # RECORD THAT SOMETHING ARRIVED, for every envelope including messages. The journal deliberately
    # does not log message events — that would make an ops log into a transcript of Brad's workspace
    # — and on 2026-09-05 that left no way to tell a quiet weekend from a listener that had stopped
    # receiving. It had: Moses processed nothing for 26 hours, Atlas messaged him twice, and every
    # watcher stayed green. This stamp carries no content, only the fact and the time.
    _beat(event=True)
    if req.type != "events_api" or ev_type != "message":
        print(f"moses: rx type={req.type} event={ev_type} cmd={payload.get('command')}", flush=True)

    if req.type == "slash_commands":
        if payload.get("command") != "/birdeye":
            return
        try:
            message, already_posted = birdeye_command((payload.get("text") or "").strip())
        except Exception as e:
            message, already_posted = f"Command failed: `{type(e).__name__}: {e}`", False
        if not already_posted and message:
            web.chat_postMessage(
                channel=payload.get("channel_id"), text=message,
                username=BIRDEYE_NAME, icon_emoji=BIRDEYE_EMOJI, unfurl_links=False,
            )
        return

    if req.type != "events_api":
        return
    event = payload.get("event", {})

    # ── A checkmark on a proposal files it ──────────────────────────────────
    # Brad only. Reacting is the lowest-friction confirmation there is, and the friction is the whole
    # point: watching Jon type "affirm" three times at a matcher that wanted "confirm" is why this
    # accepts a reaction at all.
    if event.get("type") == "reaction_added":
        if event.get("user") != OWNER_ID:
            return
        if (event.get("reaction") or "") not in proposals.CONFIRM_REACTIONS:
            return
        item = proposals.by_message((event.get("item") or {}).get("ts") or "")
        if not item:
            return
        filed, problems = proposals.file_tasks([item], dispatch)
        # A checkmark that produces silence is the original bug wearing a different hat: Brad acts,
        # nothing visible happens, and he has no way to tell "filed" from "went nowhere". Both
        # outcomes get a reply now.
        say = [f"✅ Filed to {t}" for t in filed] + [f"⚠️ {p}" for p in problems]
        if say:
            print(f"moses: reaction — {say[0][:70]}", flush=True)
            web.chat_postMessage(
                channel=(event.get("item") or {}).get("channel"),
                thread_ts=(event.get("item") or {}).get("ts"),
                text="\n".join(say),
                username=MOSES_NAME, icon_emoji=MOSES_EMOJI, unfurl_links=False)
        return

    if event.get("type") not in ("app_mention", "message"):
        return

    # One message can arrive twice: "@Moses status" fires app_mention AND message.channels. Slack
    # also redelivers an envelope it thinks went unacknowledged. Both would produce a duplicate
    # reply, so identity is (channel, ts) — the message itself, not the envelope that carried it.
    key = (event.get("channel"), event.get("ts"))
    with SEEN_LOCK:
        if key in SEEN:
            return
        SEEN.append(key)
        if len(SEEN) > 400:
            del SEEN[:200]

    channel = event.get("channel")
    # Reply WHERE THE CONVERSATION IS. `thread_ts` is on the event only when the message is itself
    # inside a thread, so it is exactly the right signal: present means answer in that thread, absent
    # means answer in the channel.
    #
    # The first version fell back to the message's own `ts`, which made every single reply start a
    # new thread — a channel of one-message threads, and a conversation nobody can follow.
    thread_ts = event.get("thread_ts") or ""
    raw = event.get("text") or ""
    machine = addressing.is_machine(event)

    def say(msg: str, threaded: bool = True):
        """Post as Moses. Returns Slack's response so a proposal can be tied to its message ts."""
        kw = {"thread_ts": thread_ts} if (threaded and thread_ts) else {}
        return web.chat_postMessage(
            channel=channel, text=msg,
            username=MOSES_NAME, icon_emoji=MOSES_EMOJI, unfurl_links=False, **kw,
        )

    # ── THE SAFEWORD, before everything ─────────────────────────────────────
    # Checked ahead of addressing, so stopping him never requires correct syntax, and only for
    # humans — the off switch belongs to people. See silence.py.
    if not machine and event.get("user") and event.get("user") != BOT_USER_ID:
        if silence.is_stop(raw):
            print(f"moses: SAFEWORD by {event.get('user')} in {channel}", flush=True)
            say(silence.engage(event.get("user"), channel), threaded=False)
            return
        if silence.is_resume(raw) and event.get("user") == OWNER_ID and silence.silenced():
            print("moses: released", flush=True)
            say(silence.release(), threaded=False)
            return

    # Silence means silence — the commands go quiet too, or he hasn't stopped, he's changed subject.
    if silence.silenced():
        return

    # ── Confirming a proposed task ──────────────────────────────────────────
    # Checked before addressing, because "yes" and "confirm all" are answers, not commands, and
    # nobody re-says a bot's name to agree with it. Matching is whole-message and only ever
    # against something already pending, so ordinary conversation never reaches it.
    if event.get("user") == OWNER_ID and not machine:
        # An explicit instruction naming ids, possibly several and possibly mixed. Tried first
        # because the whole-message matcher below refuses these, and refused them silently.
        acts = proposals.parse_actions(raw)
        if acts:
            lines = []
            if acts["confirm"]:
                items = [it for it in proposals.pending() if it["id"] in acts["confirm"]]
                filed, problems = proposals.file_tasks(items, dispatch)
                lines += [f"✅ Filed: _{t}_" for t in filed]
                lines += [f"⚠️ {p}" for p in problems]
            if acts["dismiss"]:
                dropped = [it for it in proposals.pending() if it["id"] in acts["dismiss"]]
                proposals.drop(acts["dismiss"])
                lines += [f"🗑️ Dropped: _{it['task'][:80]}_" for it in dropped]
            # NEVER stay quiet about an id that matched nothing. Silence is what made the original
            # failure invisible — Brad's instruction looked identical whether it worked or not.
            for bad in acts["unknown"]:
                lines.append(f"⚠️ `{bad}` isn't pending — nothing done with it.")
            if lines:
                print(f"moses: {len(acts['confirm'])} confirmed, {len(acts['dismiss'])} dropped, "
                      f"{len(acts['unknown'])} unknown", flush=True)
                say("\n".join(lines))
                return

        action, items, note = proposals.resolve(raw)
        if action == "confirm" and items:
            # A BARE "yes" IS ONLY MINE IF NOTHING ELSE SPOKE IN BETWEEN. Atlas now talks to Brad
            # directly in this channel; "Sounds good!" aimed at him once filed one of my proposals.
            newest_ts = max((it.get("msg_ts") or "") for it in items)
            if not proposals.confirmation_is_for_me(
                    recent(channel, thread_ts), newest_ts,
                    is_self=lambda m: addressing.is_self(m, BOT_USER_ID, BOT_ID),
                    is_machine=lambda m: addressing.is_machine(m)):
                print("moses: ambiguous confirmation — another agent spoke since I proposed", flush=True)
                say("Was that for me? Another agent has spoken since I proposed — reply "
                    + ", ".join(f"`confirm {it['id']}`" for it in items[:3])
                    + " and I'll file it.")
                return
            filed, problems = proposals.file_tasks(items, dispatch)
            print(f"moses: filed {len(filed)}, {len(problems)} problem(s) on Brad's confirmation",
                  flush=True)
            # This used to print the heading unconditionally and join an empty list under it, so a
            # task that could not be filed produced "✅ Filed:" followed by nothing — worse than
            # silence, because it reads as success.
            out = []
            if filed:
                out.append("✅ Filed:\n" + "\n".join(f"   • _{t}_" for t in filed))
            out += [f"⚠️ {p}" for p in problems]
            say("\n".join(out) if out else "Nothing to file — those were already dealt with.")
            return
        if action == "dismiss" and items:
            proposals.drop([i["id"] for i in items])
            print(f"moses: dropped {len(items)} proposal(s)", flush=True)
            say("Dropped. Not filing " + ("those." if len(items) > 1 else "that one."))
            return
        if action == "ambiguous":
            # Say which, rather than guessing. A wrong task on Brad's list is worse than one more
            # round trip — and "confirm all" is right there.
            say("Which one? " + proposals.summary() + "\n_Reply `confirm <id>`, or `confirm all`._")
            return

    # ── Pacing, for machine-authored messages in a CONVERSATIONAL channel ───
    #
    # The channel test is not an optimization — it is the fix for a real 8am misfire. Birdeye posted
    # his ops report to #ops, Moses ran the cap over that channel, decided he was over it, and
    # announced "I'll pick this up later" to a room he never talks in. Everywhere Moses is a
    # reporting surface rather than a participant, machine traffic must be ignored outright.
    history: list = []
    allow_bots = False
    conversational = channel in conversation.enabled_channels()
    if machine:
        # ── Phase 1: diagnose a persona's alarm. Read-only, never a fix. ─────
        # The one deliberate exception to "ignore machine traffic outside the chat channel". A
        # persona reporting trouble is the single case where another bot's message is addressed to
        # Moses in substance, and the answer belongs directly under the alarm.
        if (channel in alarm_channels()
                and not addressing.is_self(event, BOT_USER_ID, BOT_ID)
                and diagnose.is_alarm(raw)):
            who = speaker_name(event)
            print(f"moses: diagnosing an alarm from {who} in {channel}", flush=True)
            try:
                report, spent = diagnose.diagnose(raw, who)
            except Exception as e:
                print(f"moses: diagnosis failed ({type(e).__name__}: {e})", flush=True)
                return
            if report:
                verdict = diagnose.parse_verdict(report)
                diagnose.record(who, verdict, report[:300], channel, event.get("ts") or "")
                icon = {"real": "🔴", "false-alarm": "🟢", "unclear": "🟡"}.get(verdict, "🟡")
                web.chat_postMessage(
                    channel=channel, thread_ts=event.get("ts"),
                    text=f"{icon} *Diagnosis* — {report}",
                    username=MOSES_NAME, icon_emoji=MOSES_EMOJI, unfurl_links=False)
                print(f"moses: diagnosed {verdict} (${spent:.4f})", flush=True)
            return

        if not conversational:
            return
        history = recent(channel, thread_ts)
        allow_bots, why = pacing.may_reply_to_bot(history, BOT_USER_ID, BOT_ID)
        if not allow_bots:
            # Only bow out if he was actually being TALKED TO. Announcing "I'll pick this up later"
            # at a message that never involved him is worse than the silence it is announcing —
            # and it was how the #ops misfire became visible rather than merely wasteful.
            spoken_to = (addressing.addressed(event, BOT_USER_ID, BOT_ID, allow_bots=True) is not None
                         or addressing.in_conversation(history, BOT_USER_ID, BOT_ID))
            already_said = any(
                addressing.is_self(m, BOT_USER_ID, BOT_ID) and pacing.BOWING_OUT in (m.get("text") or "")
                for m in history[:6])
            # Bow out ONCE per stalled thread — repeating it is its own chatter, and the point of
            # the limit is to go quiet.
            #
            # "thread" ONLY, and the other two reasons are deliberate omissions. "cooldown" is a wait
            # of at most half a minute, so announcing a withdrawal for it would be a lie told 20
            # times an evening; "daily" is a global budget, so it would bow out once in every channel
            # he is addressed in — `already_said` is scoped to this thread and could not stop it.
            if why == "thread" and spoken_to and not already_said:
                say(pacing.BOWING_OUT)
            print(f"moses: bot reply withheld ({why}) in {channel}", flush=True)
            return

    text = addressing.addressed(event, BOT_USER_ID, BOT_ID, allow_bots=allow_bots)
    followup = False

    if text is None:
        # Not addressed by name — but he may already be IN the conversation, where nobody repeats a
        # name. Only in a channel where conversation is on, because the deterministic answer is a
        # roster dump and firing that at an unaddressed message would be pure noise.
        #
        # Never follow up on his own message: addressed() returns None for those too, so without
        # this check he would answer himself, which is a loop with nobody to notice it.
        speaker_is_self = addressing.is_self(event, BOT_USER_ID, BOT_ID)
        if not speaker_is_self and (allow_bots or not machine):
            if not history:
                history = recent(channel, thread_ts)
            # In the chat channel he stays in any conversation he is part of. Outside it he stays in
            # one only while a PERSON has recently used his name — so "Try again" lands in a working
            # channel, and an alarm he diagnosed in #ops still does not turn into small talk.
            # `in_conversation` reads Slack; `engaged_recently` remembers that he CONSIDERED
            # something here and chose silence. A pass leaves no message behind, so without the
            # second half one "nothing to add" ends his membership of the conversation for good —
            # measured 2026-09-08: five of Atlas's messages over two days after a single pass.
            follow = (addressing.in_conversation(history, BOT_USER_ID, BOT_ID)
                      or addressing.engaged_recently(channel)) and (
                channel in conversation.enabled_channels()
                or addressing.addressed_by_human_recently(history, BOT_USER_ID, BOT_ID))
            if follow:
                text, followup = raw, True

    # ── Named, but not in a shape the matchers call an address: let him REASON ─
    # Brad, 2026-09-02, after Moses sat out "Atlas and Moses, did you know…" twice in two days:
    # he should reason about whether a message is for him rather than pattern-match how his name
    # was used. Widening the grammar only moves the boundary; the shape nobody predicted is always
    # one message away.
    #
    # Cheapest possible filter — his name occurred — and the JUDGEMENT goes to the model, which can
    # answer PASS and say nothing. The strict matchers above run first, so ordinary addressing never
    # pays for this.
    #
    # HUMANS ONLY, deliberately. Atlas says "Moses, …" and the matchers already catch it; letting
    # every bot message that merely mentions him reach the model turns talk ABOUT him between two
    # agents into a turn each time. Narrow now, widen on evidence.
    if (text is None and not machine and not addressing.is_self(event, BOT_USER_ID, BOT_ID)
            and channel in conversation.enabled_channels()
            and addressing.mentions_name(raw)):
        print(f"moses: named but not addressed in {channel} — letting him decide", flush=True)
        text, followup = raw, True

    if text is None:
        return

    print(f"moses: {'following up on' if followup else 'addressed in'} {channel} by "
          f"{'a bot' if machine else 'a human'}", flush=True)

    # ── Capture stays deterministic ─────────────────────────────────────────
    # "add X to my list" is string handling with a correct answer; sending it to a model would make
    # a reliable thing unreliable and cost money to do it.
    # NOT on a follow-up: capture writes to the memory corpus, so it must be something he was
    # deliberately told to do. Mid-conversation prose that happens to look like "add X to my list"
    # would otherwise write a file nobody asked for.
    if not followup:
        try:
            intent = dispatch.classify(text)
            if intent.kind == "capture":
                say(dispatch.capture(intent))
                addressing.record_considered(channel, passed=False)
                pacing.record_reply(reactive=machine)
                return
        except Exception as e:
            say(f"Couldn't do that: `{type(e).__name__}: {e}`")
            return

    # ── One reply per burst, not one per message ────────────────────────────
    # Socket Mode dispatches on a thread pool, so three messages in the same second run three
    # handlers at once. On 2026-08-15 that produced three replies to one moment, carrying three
    # wordings of the same proposal. The first handler here takes the window; the rest hand their
    # message over and leave. NOTHING IS SUPPRESSED — the messages are answered together.
    #
    # Deliberately below the safeword, the confirm gate and diagnosis: those are deterministic,
    # cheap, and each one needs its own response. Only the model path coalesces.
    burst = (channel, thread_ts or "")
    if not coalesce.COALESCER.claim(burst, human=not machine):
        print(f"moses: folded into the reply in flight for {channel}", flush=True)
        return
    coalesce.COALESCER.wait(burst)
    folded, had_human = coalesce.COALESCER.release(burst)
    if folded:
        print(f"moses: answering {folded + 1} messages at once in {channel}", flush=True)
        machine = machine and not had_human   # a person in the burst makes this a reply to a person
        history = []                          # re-read, so the reply sees the whole burst

    # ── Images pasted into the channel ──────────────────────────────────────
    # Brad pasted a screenshot of a live bug and was told nobody could see it. Slack DOES deliver the
    # attachment metadata; nothing here had ever looked at it, and the bot could not have downloaded
    # one anyway until `files:read` was granted on 2026-08-23.
    #
    # Fetched here rather than inside conversation.py because this is the layer that holds the Slack
    # token, and handing a credential to the half that talks to the model is the wrong direction.
    att_images, att_notes = [], []
    try:
        if event.get("files"):
            att_images, att_notes = attachments.fetch(event.get("files") or [], BOT_TOKEN)
            if att_images:
                print(f"moses: {len(att_images)} attached image(s) included in the turn", flush=True)
            for n in att_notes:
                print(f"moses: attachment skipped — {n}", flush=True)
    except Exception as e:                                        # noqa: BLE001
        # An attachment is an enhancement on top of a message that still has words in it. It must
        # never be what stops Moses answering.
        print(f"moses: attachment handling failed ({type(e).__name__}: {e})", flush=True)
        att_images, att_notes = [], []

    # ── Conversation: say something, just react, or say nothing ─────────────
    kind, value, spent = "none", "", 0.0
    # Which Knight jobs exist BEFORE the model runs, so a job this turn starts reports back HERE when
    # it ends. Brad asked "ping me here when Knight is done", Moses promised it twice, and nothing
    # could deliver it. See knight_notify.
    jobs_before = knight_notify.snapshot()
    try:
        if not history:
            history = recent(channel, thread_ts)
        kind, value, spent = conversation.reply(history, BOT_USER_ID, channel,
                                                reactive=machine, directed=(text is not None),
                                                images=att_images, image_notes=att_notes,
                                                # So the transcript names its speakers instead of
                                                # showing raw ids for the model to guess from — see
                                                # agent/people.py and the 2026-09-17 "Jon" incident.
                                                web=web)
    except Exception as e:
        print(f"moses: conversation failed ({type(e).__name__}: {e})", flush=True)
        kind = "none"
    # An enhancement: failing to record where Knight reports back must never cost the reply itself.
    try:
        for jid in knight_notify.claim(jobs_before, channel=channel, thread_ts=thread_ts,
                                       user="" if machine else (event.get("user") or "")):
            print(f"moses: Knight job {jid} will report back to {channel}", flush=True)
    except Exception as e:                                        # noqa: BLE001
        print(f"moses: could not record where Knight reports back ({type(e).__name__}: {e})",
              flush=True)


    who = "bot" if machine else "human"

    if kind == "react":
        # A reaction instead of a sentence, when a whole message would be clutter. If Slack rejects
        # the emoji name, fall back to posting it rather than losing the reply entirely — a failed
        # decoration must not become silence.
        if react(channel, event.get("ts") or "", value):
            print(f"moses: reacted :{value}: ({who}, ${spent:.4f})", flush=True)
            addressing.record_considered(channel, passed=False)
            pacing.record_reply(reactive=machine)
            return
        print(f"moses: reaction :{value}: failed, saying it instead", flush=True)
        say(f":{value}:")
        addressing.record_considered(channel, passed=False)
        pacing.record_reply(reactive=machine)
        return

    if kind == "pass":
        # A DECISION to stay quiet, not a failure. Brad: "If Moses can't add anything meaningful to
        # a conversation, that's ok too." Logged so the choice is visible, and counted so a run of
        # silences still moves the cap.
        print(f"moses: passed — nothing to add ({who}, ${spent:.4f})", flush=True)
        # HE WAS HERE AND CHOSE SILENCE. Recorded so the next message still reaches him; a pass is
        # participation, not departure.
        addressing.record_considered(channel, passed=True)
        pacing.record_reply(reactive=machine)
        return

    if kind == "text":
        # Lift out anything he says he learned, file it against whoever said it, and tell Brad in
        # the same message rather than a second one he has to correlate.
        value, learned = conversation.extract_learning(value)
        value, proposed = conversation.extract_proposals(value)
        note = ""
        for item in learned:
            added, total = dispatch.file_learning(item, source=speaker_name(event),
                                                  link=permalink(channel, event.get("ts") or ""))
            if added:
                note += f"\n_📌 Filed to look into further: {item}_"
                print(f"moses: filed a learning ({total} open)", flush=True)

        # A proposed task is HELD, never filed. Brad confirms; code writes.
        results = [r for r in (proposals.add(t, channel, project=proj) for proj, t in proposed) if r]
        # A near-identical proposal is already pending. Drop it rather than showing the same task
        # twice under two ids — and do NOT attach this message to it, or a checkmark here would
        # confirm a proposal that was made in a different conversation.
        for r in results:
            if r.get("duplicate"):
                print(f"moses: skipped a duplicate of pending `{r['id']}`", flush=True)
        held = [h for h in results if not h.get("duplicate")]
        if held:
            note += "\n\n📋 *Proposed — say the word and I'll file " + \
                    ("them" if len(held) > 1 else "it") + ":*"
            for h in held:
                note += f"\n   • `{h['id']}`  {h['task']}"
                # Overlaps something already tracked. Shown, never suppressed — the call is Brad's.
                if h.get("related"):
                    note += f"\n     _↳ close to an open item: {h['related'][:110]}_"
            note += ("\n_React ✅, or reply `confirm`" +
                     (" / `confirm all`" if len(proposals.pending()) > 1 else "") + "._")
            print(f"moses: proposed {len(held)} task(s), awaiting Brad", flush=True)

        print(f"moses: replied ({who}, ${spent:.4f})", flush=True)
        posted = say((value + note).strip() or note.strip())
        # Remember which message carried them, so a checkmark on it confirms the right thing.
        for h in held:
            proposals.attach_message(h["id"], (posted or {}).get("ts", ""))
        addressing.record_considered(channel, passed=False)
        pacing.record_reply(reactive=machine)
        return

    # kind == "none": conversation never ran.
    #
    # On a FOLLOW-UP, stay silent. The deterministic answer is a roster-and-status dump; firing that
    # at a message that never named him would be worse than saying nothing — it is the "menu, not a
    # mind" behavior, aimed at someone who did not even ask.
    if followup:
        print(f"moses: follow-up with no conversation available — staying quiet", flush=True)
        return

    # Addressed by name and conversation is unavailable: answer deterministically rather than go
    # dark, because he WAS asked something directly.
    try:
        fallback = answer(text)
    except Exception as e:
        fallback = f"Couldn't do that: `{type(e).__name__}: {e}`"
    say(fallback)
    addressing.record_considered(channel, passed=False)
    pacing.record_reply(reactive=machine)


web = WebClient(token=BOT_TOKEN)

# Who we are, asked of Slack at startup rather than hardcoded — a pasted-in user id is one more
# thing that can be wrong after a reinstall, and it is the value the loop guard depends on.
BOT_USER_ID = ""
BOT_ID = ""

# Recently handled (channel, ts) pairs, bounded. A set would need its own eviction; a list this
# short is cheaper to reason about than to optimize.
SEEN: list = []
SEEN_LOCK = threading.Lock()


# ── Proof of life for the MAILBOX, not the model ────────────────────────────────────────────────
# The liveness probe asked the CLI "reply with the word alive" and reported healthy while Moses
# could not receive a single Slack message. It was answering a real question — is the model
# reachable, is the login valid — and not the one its own unit description claimed: "can he
# actually answer right now?"
#
# So the listener now says so itself. `connected` is the socket's own view; `last_event` is when
# anything last arrived. A wedged process stops updating the file at all, which is the case that
# actually happened and the one a stale-file check catches.
_BEAT = Path(os.environ.get("MOSES_STATE", "/var/lib/moses")) / "listener.json"
_LAST_EVENT: dict[str, str] = {}


def _beat(event: bool = False, connected: bool | None = None) -> None:
    try:
        now = _dt.datetime.now().replace(microsecond=0).isoformat()
        if event:
            _LAST_EVENT["at"] = now
        _BEAT.parent.mkdir(parents=True, exist_ok=True)
        _BEAT.write_text(json.dumps({
            "at": now,
            "connected": connected,
            "last_event": _LAST_EVENT.get("at"),
            "pid": os.getpid(),
        }), encoding="utf-8")
    except Exception:                                          # noqa: BLE001
        pass       # a heartbeat that can crash the listener is worse than no heartbeat


def _heartbeat_loop(sm) -> None:
    while True:
        try:
            _beat(connected=bool(sm.is_connected()))
        except Exception:                                      # noqa: BLE001
            _beat(connected=False)
        time.sleep(60)


def main() -> None:
    global BOT_USER_ID, BOT_ID
    try:
        me = web.auth_test()
        BOT_USER_ID, BOT_ID = me.get("user_id", ""), me.get("bot_id", "")
    except Exception as e:
        # Refuse to run half-identified. Without our own user id the loop guard is weaker, and a
        # listener that cannot recognize its own messages is exactly the thing not to leave running
        # unattended in a channel that contains another agent.
        print(f"moses: cannot identify self ({type(e).__name__}: {e}) — refusing to start",
              file=sys.stderr, flush=True)
        sys.exit(1)

    sm = SocketModeClient(app_token=APP_TOKEN, web_client=web)
    sm.socket_mode_request_listeners.append(lambda c, r: handle(r, c))
    files = len(list(MEMORY_DIR.glob("*.md"))) if MEMORY_DIR.is_dir() else 0
    print(f"moses: connecting as {BOT_USER_ID} (no model — deterministic only, {files} memory "
          f"files, answering to 'Moses …', @Moses and /birdeye)…", flush=True)
    sm.connect()
    threading.Thread(target=_heartbeat_loop, args=(sm,), daemon=True, name="heartbeat").start()
    threading.Event().wait()


if __name__ == "__main__":
    main()
