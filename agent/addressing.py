"""Decide whether an inbound Slack message is addressed to Moses.

Split out of listener.py because this is the one piece of logic that MUST NOT silently break, and a
pure function is the only version that can be pinned by a test (see addressing_test.py).

WHY IT EXISTS (Brad, 2026-08-13): he wants to say "Moses, ..." in a channel rather than "@Moses ...".
That means subscribing to message.channels / message.groups, which delivers EVERY message in every
channel Moses is in — not just the ones aimed at him. So the filter, not the subscription, is what
makes this safe.

THE LOOP HAZARD, which is the reason this file is careful:
The channel that prompted this also contains Atlas, another agent. Two agents that answer each other
have no natural stopping point — one message naming both could bounce forever, unattended and
metered.

Bot messages were originally ignored unconditionally. Atlas then published his own bot-to-bot limits
and Brad asked to match them, so the guard changed shape rather than disappearing: `allow_bots` is
opt-in, and the CALLER must pass it only after pacing.py has approved the reply. See pacing.py —
limit 1, the consecutive-turn cap, is what actually terminates the loop.

Two rules stayed absolute and are not subject to `allow_bots`:
**Moses never answers himself**, and a message with no human or bot author at all is never answered.
"""

import sys as _sys, pathlib as _pl  # noqa: E401
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent / "lib"))
import moses_env as _env  # noqa: E402 — whose home, where Moses lives, the operator's settings
import os
import re

# Subtypes that are still a person typing. Everything else — joins, leaves, edits, deletions,
# pinned items, channel topics — is noise that must never be read as an instruction. An allowlist
# is used rather than a denylist because Slack adds new subtypes over time, and the failure
# direction matters: an unknown subtype should be ignored, not obeyed.
HUMAN_SUBTYPES = {None, "file_share", "thread_broadcast"}

# Addressing him the way you address a person: his name in the VOCATIVE, not as the object of a
# sentence. Three shapes, because that is how people actually type.
#
# The first version required the name at the very start and nothing else. It shipped, and the first
# real message in the channel was Brad's "Hey Moses, meet Atlas" — which it ignored, in front of
# Jon and Atlas. Over-correcting against "I'll ask Moses about it" made him look broken at the one
# moment he was being introduced.
#
# The distinction that matters is grammatical: a vocative is set off by position or a comma, while
# an object is embedded in the clause. "Hey Moses, ..." and "..., Moses?" are addressed;
# "I'll ask Moses", "that's Moses's job" and "tell Moses later" are talk ABOUT him.
FILLER = (r"(?:hey|hi|hello|yo|ok|okay|so|alright|right|well|oh|um|uh|morning|afternoon|evening|"
          r"thanks|thank\s+you|cheers|please|and|but|also|actually|anyway)")

# 1. Leading: optional greetings, then his name. "Hey Moses, ...", "ok Moses ...", "Moses: ..."
ADDRESS = re.compile(rf"^\s*(?:{FILLER}[\s,]+)*moses\b[\s,:.\-–—>!?]*", re.I)

# 2. Trailing vocative — his name at the end of the message.
#
#    This first required a comma, on the theory that "…, Moses?" is a vocative and "I'll ask Moses"
#    is an object. The theory is right; the test was too strict, and it missed twice in live use —
#    "Are you ready to rock Moses?" and "welcome back to the conversation Moses". People do not type
#    the comma.
#
#    So the comma is now sufficient but not necessary, and what actually disqualifies a trailing
#    name is the word IN FRONT of it. "talking to Moses", "tell Moses", "Brad and Moses" — those
#    make him the object of something. "the conversation Moses" and "ready to rock Moses" do not.
#    Checking the preceding word is the cheap approximation of the grammar that matters.
OBJECT_MARKER = (r"(?:to|with|about|for|from|and|or|than|like|ask|asks|asked|asking|tell|tells|told|"
                 r"telling|ping|pinged|email|emailed|dm|dmed|message|messaged|notify|notified|"
                 r"contact|contacted|mention|mentioned|is|was|are|were|be|been|call|called)")
TRAILING = re.compile(r"[,;]\s*moses\s*[?!.…]*\s*$", re.I)
TRAILING_LOOSE = re.compile(rf"(?<!\w)(?!{OBJECT_MARKER}\s+moses\b)\w+\s+moses\s*[?!.…]*\s*$", re.I)

# 3. Just his name, with or without a question mark. "Moses?" / "Moses!"
BARE = re.compile(r"^\s*moses\s*[?!.]*\s*$", re.I)


def strip_mention(text: str, bot_user_id: str) -> tuple[str, bool]:
    """Remove a leading <@BOT> mention. Returns (text, was_mentioned_first)."""
    if not bot_user_id:
        return text, False
    m = re.match(rf"^\s*<@{re.escape(bot_user_id)}>[\s,:.\-–—>!?]*", text or "")
    if not m:
        return text, False
    return (text or "")[m.end():], True


SELF_NAME = _env.setting("MOSES_NAME", "Moses")


def is_self(m: dict, bot_user_id: str = "", bot_id: str = "") -> bool:
    """Is this message one of MOSES'S OWN? Not Birdeye's, not Therapist's — HIS.

    Two fields are needed, and each one alone is wrong:

    **`user` is absent.** Moses posts with `chat:write.customize` to wear his own name, and Slack
    records those as a pure `bot_message` with no `user` field at all. Matching on `user` identified
    none of his messages, which silently broke follow-up mode and made the reply cap count zero.

    **`bot_id` is not unique to him.** EVERY persona posts through the same Slack app — Birdeye,
    Therapist, Big Pipe, Tagilla and Moses all carry `bot_id=B0BLZHWR0HY`. Observed in #ops on
    2026-08-14, where all three morning reports were indistinguishable:

        [08:00:01] username=Birdeye    bot_id=B0BLZHWR0HY
        [08:00:04] username=Moses      bot_id=B0BLZHWR0HY
        [08:00:17] username=Therapist  bot_id=B0BLZHWR0HY

    So bot_id alone made Moses count *Birdeye's* morning report as his own reply, trip the
    consecutive-reply cap, and post "I'll pick this up later" into #ops at 8am — in a channel he is
    not even conversational in. The display name is what separates the personas, so it is required.
    """
    if bot_id and m.get("bot_id") == bot_id:
        name = m.get("username") or (m.get("bot_profile") or {}).get("name") or ""
        # No name at all means the app posted as itself, which is Moses's default identity.
        return not name or name == SELF_NAME
    return bool(bot_user_id and m.get("user") == bot_user_id)


def in_conversation(history: list[dict], bot_user_id: str = "", bot_id: str = "",
                    window: int = 3) -> bool:
    """Has Moses spoken within the last `window` messages? Then he is mid-conversation.

    WHY THIS EXISTS. Requiring his name on every message means he can be pinged but never talked
    with. It showed up the first time he actually worked: Brad said "welcome back … Moses", Moses
    answered, and Atlas replied *"what are you for? … I'll trade you the same about me"* — a direct
    question containing no "Moses" anywhere, because nobody repeats a name mid-conversation. He sat
    silent on a question aimed squarely at him.

    So being addressed by name STARTS a conversation, and this keeps him in it. It is deliberately
    permissive, because the model now has a way to decline: it can answer PASS and say nothing. The
    judgment about whether a message is for him moves to the place that can actually judge it,
    instead of a regex guessing from the absence of a proper noun.

    SELF-LIMITING: the window counts messages, not time. Every message he stays quiet for pushes his
    last one further back, so three messages after he stops contributing he drops out on his own —
    no timer, no state to go stale.

    AND THAT SELF-LIMIT IS ALSO A ONE-WAY DOOR, which is why `engaged_recently` exists beside it.
    A considered PASS leaves no message in Slack, so deciding "nothing to add" once pushes his own
    last turn back and quietly ends his membership of the conversation. Measured 2026-09-08: he
    evaluated Atlas at 11:01 on the 7th, passed, and never engaged again — five messages over two
    days, because only his NAME could let him back in, and nobody repeats a name mid-conversation.
    That is the exact failure this function was written to fix, reintroduced by its own escape hatch.

    This function stays a pure read of history — the caller ORs it with a record of whether he
    actually considered something recently.
    """
    seen = 0
    for m in history:                       # Slack returns newest-first
        if m.get("subtype") in ("channel_join", "channel_leave"):
            continue
        if is_self(m, bot_user_id, bot_id):
            return True
        seen += 1
        if seen >= window:
            break
    return False


def is_machine(event: dict) -> bool:
    """Was this posted by a bot or app rather than a person?

    Slack marks integration posts with a bot_id and usually the bot_message subtype. A message sent
    with chat:write.customize — which is how every persona here posts — carries bot_id but can look
    otherwise human, so bot_id is the reliable test rather than the subtype alone.
    """
    if not isinstance(event, dict):
        return False
    return bool(event.get("bot_id") or event.get("app_id") or event.get("subtype") == "bot_message")


def addressed_by_human_recently(history: list[dict], bot_user_id: str = "", bot_id: str = "",
                                window: int = 4) -> bool:
    """Did a PERSON say his name in the last few messages?

    `in_conversation` asks whether Moses spoke recently, which is the right question in the chat
    channel and the wrong one everywhere else: in #ops he speaks by diagnosing alarms, so keying
    follow-ups off his own voice would make him conversational in a room that exists for reports.

    This asks the narrower thing — whether a human STARTED something with him — so he can be talked
    with in a working channel without ever volunteering an opinion in one. Measured 2026-08-25 in
    #viatica-dev: "Tooling update for you Moses…", "Try again" and "Status?" were all ignored, and
    Brad had to re-send each one opening with the name. Every reply that day followed a message
    beginning "Moses". He was not slow; he could not hear anything that did not start with his name.

    Machines cannot start one: `addressed` refuses them unless allow_bots, which is not passed here.
    """
    seen = 0
    for m in history:                       # Slack returns newest-first
        if m.get("subtype") in ("channel_join", "channel_leave"):
            continue
        if is_self(m, bot_user_id, bot_id):
            continue                        # his own turns do not consume the window
        if addressed(m, bot_user_id, bot_id) is not None:
            return True
        seen += 1
        if seen >= window:
            break
    return False


# ── Named at all, anywhere ───────────────────────────────────────────────────
# Brad, 2026-09-02: *"the rules of engagement for Moses is pretty strict and because Jon and I talk
# like normal humans, Moses needs to REASON when to jump in vs some pattern matching of how his name
# might have been used."*
#
# The matchers above answer a GRAMMATICAL question — is this a vocative — and they answer it well.
# What they cannot answer is the actual question: is this for me. Jon wrote "Atlas and Moses, did
# you know about workflows" and got silence, because another name came first; twice in two days.
# Widening the grammar would just move the boundary, since the next shape nobody predicted is always
# one message away.
#
# So this deliberately does NOT decide. It only says his name occurred, which is the cheapest
# possible filter, and hands the judgement to the model — which can answer PASS and say nothing.
# The strict matchers still short-circuit first, so the common cases never pay for a model turn.
NAMED = re.compile(r"\bmoses(?:'s)?\b", re.I)


def mentions_name(text: str) -> bool:
    """Does his name appear at all? Not 'is this addressed to him' — that judgement is the model's."""
    return bool(NAMED.search(text or ""))


def addressed(event: dict, bot_user_id: str = "", bot_id: str = "",
              allow_bots: bool = False) -> str | None:
    """Return the command text if this message event is addressed to Moses, else None.

    Ignores, in order: machines (unless `allow_bots`), Moses's own messages, non-human subtypes, and
    finally anything that does not open with his name.

    `allow_bots` is for the caller that has already asked pacing.py whether a bot reply is within
    the three limits. Passing it unconditionally would restore the unbounded loop.
    """
    if not isinstance(event, dict):
        return None

    # 1. Machines: opt-in only, and never by accident.
    if is_machine(event) and not allow_bots:
        return None

    # 2. Never respond to ourselves. This one is absolute — `allow_bots` does not reach it, because
    #    a Moses who answers Moses is a loop with only one participant and no one to notice.
    user = event.get("user") or ""
    if bot_user_id and user == bot_user_id:
        return None
    if bot_id and event.get("bot_id") == bot_id:
        return None
    if not user and not is_machine(event):
        return None

    if event.get("subtype") not in HUMAN_SUBTYPES:
        return None

    text = event.get("text") or ""
    if not text.strip():
        return None

    # 3. A leading @mention counts as addressing him too, so the two paths behave identically.
    #    (The app_mention event also fires for these — listener.py de-duplicates by channel+ts.)
    text, mentioned = strip_mention(text, bot_user_id)

    if BARE.match(text):
        return ""
    m = ADDRESS.match(text)
    if m:
        return text[m.end():].strip()
    t = TRAILING.search(text)
    if t:
        # Hand back the request without the vocative: "what do you think, Moses?" -> "what do you think"
        return text[:t.start()].strip()
    if TRAILING_LOOSE.search(text):
        # No comma, but the preceding word does not make him an object — so it is a vocative.
        # Strip only the name, keeping the word before it: "welcome back to the conversation Moses"
        # -> "welcome back to the conversation".
        return re.sub(r"\s*\bmoses\b\s*[?!.…]*\s*$", "", text, flags=re.I).strip()
    if mentioned:
        return text.strip()
    return None


# ── Did he CONSIDER this channel recently, whether or not he spoke? ─────────────────────────────
# `in_conversation` can only see what Slack shows, and a PASS is invisible there. So participation
# is recorded here: he was present, he read it, he chose silence. That still counts as being in the
# room, and it is the difference between going quiet and leaving.
#
# TIME-BOUNDED AND SELF-CORRECTING, because the objection in the docstring above is a fair one:
# state goes stale. This stales itself out after ENGAGED_WINDOW_S with nothing further.
#
# AND IT STILL ENDS. Passing forever is not participation either, so consecutive passes with no
# human speaking are counted and capped — he drops out for real after MAX_CONSECUTIVE_PASSES,
# exactly as the message-window rule intended, but for the stated reason rather than by accident.
import json as _json
import time as _time
from pathlib import Path as _Path

_ENGAGED = _Path(os.environ.get("MOSES_STATE", "/var/lib/moses")) / "engaged.json"
ENGAGED_WINDOW_S = float(os.environ.get("MOSES_ENGAGED_WINDOW_S", str(12 * 3600)))
MAX_CONSECUTIVE_PASSES = int(os.environ.get("MOSES_MAX_CONSECUTIVE_PASSES", "4"))


def _engaged_load() -> dict:
    try:
        return _json.loads(_ENGAGED.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {}


def record_considered(channel: str, *, passed: bool, now: float | None = None) -> None:
    """He read something in this channel and decided. Spoke or passed, both are participation."""
    d = _engaged_load()
    row = d.get(channel) or {}
    row["at"] = _time.time() if now is None else now
    row["passes"] = (row.get("passes", 0) + 1) if passed else 0
    d[channel] = row
    try:
        _ENGAGED.parent.mkdir(parents=True, exist_ok=True)
        _ENGAGED.write_text(_json.dumps(d), encoding="utf-8")
    except Exception:                                          # noqa: BLE001
        pass          # never let bookkeeping break the reply path


def engaged_recently(channel: str, now: float | None = None) -> bool:
    row = _engaged_load().get(channel)
    if not row:
        return False
    if row.get("passes", 0) >= MAX_CONSECUTIVE_PASSES:
        return False                      # genuinely nothing to add, repeatedly — let him go
    age = (_time.time() if now is None else now) - float(row.get("at", 0))
    return age <= ENGAGED_WINDOW_S
