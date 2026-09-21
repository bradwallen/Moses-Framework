"""pacing — the limits that keep two agents from talking forever.

Three limits, set to Atlas's LIVE values so both agents bound the same conversation the same way
(Brad, 2026-09-11):

  * MAX_OWN_REPLIES  — 12 replies from Moses in a row in one thread with no human speaking.
  * DAILY_REACTIVE_CAP — 20 bot-prompted replies a day, counted across every channel at once.
  * MIN_GAP_SECONDS  — 30 seconds between two of his own bot-prompted posts.

Moses runs in ONE Slack workspace (one SLACK_BOT_TOKEN, one Socket Mode process — see listener.py),
so Atlas's per-workspace/shared-budget wording maps cleanly: the turn cap is read from the thread
itself, which is per-channel and therefore per-workspace by construction, and the daily count is a
single number in one state file, which is exactly what "one budget shared across workspaces" means.
If Moses is ever given a second workspace, the turn cap needs nothing and the daily count is already
shared.

ONLY THE TURN CAP TERMINATES. Counting his OWN replies — rather than all machine traffic — is the
honest measure of "am I the one filling this channel", and it is the limit that ends a runaway:
whatever Atlas does, Moses stops after twelve and the exchange is over. A human speaking resets the
count, so a capped thread revives the moment a person joins in. The other two are budget: they make
a bounded conversation cheaper and slower, they do not make it shorter.

THE GAP IS THE ONE THAT HAS DRAWN BLOOD. It shipped at 45s on 2026-08-13 and was removed the same
day, for two separate reasons:

  * Atlas answers in 1–9 seconds, so a gap longer than his turn refuses replies inside a live
    exchange and Moses looks like he has ghosted. 30s is shorter than 45s but still longer than
    Atlas's turn, so this failure mode is REDUCED, NOT GONE — it is the cost of matching his value.
  * It was armed by ANY post, so answering Brad locked Moses out of the exchange that followed.
    That half is fixed rather than accepted: the clock is armed by his BOT-PROMPTED posts only
    (see record_reply), because a human message is exempt from all three limits, and a reply to a
    human that silences the next reply to a bot is that exemption leaking.

THE ASYMMETRY IS THE DESIGN. None of this touches a human message. Brad or Jon typing always gets a
reply — no turn cap, no daily budget, no cooldown, and answering them consumes none of the three. A
limiter that makes Moses ignore Brad is a bug wearing a safety feature's clothes.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path

import addressing

STATE = Path(os.environ.get("MOSES_STATE", "/var/lib/moses"))
PACING_FILE = STATE / "pacing.json"

# Twelve consecutive replies with no human input — Atlas's live per-thread value. Long enough for a
# real exchange to finish on its own, and still bounded: an unattended loop is over in 24 messages.
MAX_OWN_REPLIES = int(os.environ.get("MOSES_BOT_TURN_CAP", "12"))

# Twenty bot-prompted replies a day, across every channel. The turn cap bounds one thread; this
# bounds the DAY, which is what stops twelve turns in each of six threads from adding up to a
# runaway nobody watched. Counted in the state file rather than read back from Slack because there
# is no single history to derive "today" from once more than one channel is involved.
DAILY_REACTIVE_CAP = int(os.environ.get("MOSES_BOT_DAILY_CAP", "20"))

# Thirty seconds between two bot-prompted posts. See the module docstring for why this constant was
# once deleted outright; it is back at Atlas's value, armed only by reactive posts.
MIN_GAP_SECONDS = float(os.environ.get("MOSES_BOT_MIN_GAP_S", "30"))

BOWING_OUT = "I'll pick this up later."

# A RUNAWAY LOOP IS FAST. Two agents talking past each other post seconds apart, so a cap's worth of
# replies inside an hour is a loop and the same replies spread over three days is a conversation.
#
# Without this the cap was not a terminator, it was a LATCH. Measured 2026-08-26: the last human
# message in #the_4_horsemen was 08-23 22:18, Moses had posted five times since, and every one of
# Atlas's morning greetings on the 24th, 25th and 26th was refused with `may_reply_to_bot -> False`.
# He was not slow or broken, he was permanently muted to the other agent and nothing said so out
# loud. The docstring's promise that "a capped thread revives the moment a person joins in" is true
# and was the whole problem: in a channel where people speak every few days, nothing revived it.
#
# The count is now bounded by this window, so the limit still ends any loop in 24 messages and then
# lets the room go back to normal on its own.
TURN_WINDOW_S = float(os.environ.get("MOSES_BOT_TURN_WINDOW_S", "3600"))


def _load() -> dict:
    try:
        d = json.loads(PACING_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if d.get("day") != date.today().isoformat():
        return {}
    return d


def _save(d: dict) -> None:
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        d["day"] = date.today().isoformat()
        tmp = PACING_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        tmp.replace(PACING_FILE)
    except OSError:
        pass          # pacing state is a brake, not a ledger; losing it must not break the listener


def _is_machine(m: dict) -> bool:
    return bool(m.get("bot_id") or m.get("app_id") or m.get("subtype") == "bot_message")


def consecutive_own_replies(history: list[dict], bot_user_id: str = "", bot_id: str = "",
                            now: float | None = None) -> int:
    """How many messages in a row Moses has posted without a human speaking.

    Counts backward from the newest message. Another agent's messages neither count nor stop the
    count — only a HUMAN resets it, because the thing being limited is Moses talking into a room
    with no people in it.

    **Identity comes from `bot_id`, not `user`.** Messages Moses posts under his own display name
    have NO `user` field — Slack records them as a bare `bot_message`. Matching on `user` meant this
    returned 0 forever and the cap never fired. See addressing.is_self for the observed shapes.
    """
    cutoff = (time.time() if now is None else now) - TURN_WINDOW_S
    n = 0
    for m in history:                      # Slack returns newest-first
        if m.get("subtype") in ("channel_join", "channel_leave"):
            continue
        # A message only leaves the window if it can be shown to be old. Missing or unparseable
        # timestamps COUNT — the dangerous direction is losing the cap, not applying it once too
        # often, so anything unknown is treated as part of the current conversation.
        raw = m.get("ts")
        if raw is not None:
            try:
                if float(raw) < cutoff:
                    break                  # older than the window — a different conversation
            except (TypeError, ValueError):
                pass
        if addressing.is_self(m, bot_user_id, bot_id):
            # The bow-out is the LIMIT ANNOUNCING ITSELF, not Moses filling the room. Counting it
            # let the guard deepen its own refusal: on 2026-08-24 the message saying he would pick
            # it up later became the fourth of the five replies that kept him quiet.
            if BOWING_OUT in (m.get("text") or ""):
                continue
            n += 1
            continue
        if not _is_machine(m):
            break                          # a human spoke — the count resets here
    return n


def may_reply_to_bot(history: list[dict], bot_user_id: str = "", bot_id: str = "",
                     now: float | None = None) -> tuple[bool, str]:
    """Decide whether Moses may answer a machine. Returns (allowed, reason_if_not).

    Never call this for a human message — the asymmetry is the design, and routing a human through
    here would be the bug this file warns about.

    The reasons are ordered worst-first, and the caller reads them: "thread" is a finished
    conversation and earns the bow-out, "daily" is a spent budget, "cooldown" is a wait of at most
    MIN_GAP_SECONDS and must never announce itself (see listener.py).
    """
    now = time.time() if now is None else now
    if consecutive_own_replies(history, bot_user_id, bot_id, now) >= MAX_OWN_REPLIES:
        return False, "thread"
    state = _load()
    if int(state.get("reactive", 0)) >= DAILY_REACTIVE_CAP:
        return False, "daily"
    # Missing or unparseable state means no known post, so no cooldown. Failing OPEN here is
    # deliberate and is the opposite of the turn cap's rule: a lost counter file must not be able to
    # mute Moses, because the limit that actually terminates a loop is derived from the channel and
    # is still in force above.
    try:
        since = now - float(state["last_reactive_post"])
    except (KeyError, TypeError, ValueError):
        return True, ""
    if 0 <= since < MIN_GAP_SECONDS:
        return False, "cooldown"
    return True, ""


def record_reply(now: float | None = None, *, reactive: bool) -> None:
    """Note that Moses posted.

    `reactive` is the whole asymmetry in one flag. A reply to a human touches `last_post` only — it
    feeds the morning standup and gates nothing — while a reply to a MACHINE spends a slot of the
    day's budget and arms the cooldown. Counting a human reply either way would make answering Brad
    the reason Moses went quiet to Atlas, which is the 2026-08-13 failure the docstring describes.
    """
    now = time.time() if now is None else now
    state = _load()
    state["last_post"] = now
    if reactive:
        state["reactive"] = int(state.get("reactive", 0)) + 1
        state["last_reactive_post"] = now
    _save(state)


def status() -> str:
    state = _load()
    return (f"bot-to-bot: {int(state.get('reactive', 0))} of {DAILY_REACTIVE_CAP} reactive replies "
            f"today · cap {MAX_OWN_REPLIES} in a row without a human · "
            f"{MIN_GAP_SECONDS:.0f}s between his own bot replies")
