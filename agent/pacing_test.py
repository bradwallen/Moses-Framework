#!/usr/bin/env python3
"""Pin the three bot-to-bot limits. Run: python3 pacing_test.py

They are Atlas's live values (2026-09-11): 12 turns in a thread, 20 reactive replies a day, 30
seconds between his own bot-prompted posts. The exact numbers are asserted, not just the behavior,
because "match Atlas" is the requirement and a silently drifting constant would still go green.

TERMINATION is the load-bearing group: the turn cap is the only thing standing between Moses and
Atlas talking forever. ASYMMETRY is its twin — a limit that reaches a human is a bug, not a safety
feature, and that includes a HUMAN reply arming the cooldown against the next bot one, which is how
the gap was broken the first time it shipped.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["MOSES_STATE"] = tempfile.mkdtemp(prefix="moses-pacing-")

import pacing  # noqa: E402


def own(history, bot_user_id=None):
    return pacing.consecutive_own_replies(history, BOT, MOSES_BOT_ID)


def may(history, bot_user_id=None, now=None):
    return pacing.may_reply_to_bot(history, BOT, MOSES_BOT_ID, now)

BOT = "U0BMTTVT648"
MOSES_BOT_ID = "B0BLZHWR0HY"
results = []


def check(label, cond):
    results.append((label, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + label)


def human(text="hi"):
    return {"user": "U1", "text": text}


def atlas(text="hi"):
    return {"user": "U9", "bot_id": "B0BPYF1T0E7", "text": text}


def moses(text="hi"):
    """The REAL shape, from the live channel: no `user` field at all. Posting under a custom display
    name makes Slack record it as a bare bot_message. The old fixture invented a `user` key, which is
    why the cap tested green while counting zero in production."""
    return {"bot_id": MOSES_BOT_ID, "app_id": "A0BLTAD2Y67", "username": "Moses",
            "subtype": "bot_message", "text": text}


def reset():
    pacing.PACING_FILE.unlink(missing_ok=True)


# ── Counting HIS OWN replies since a human last spoke ───────────────────────
reset()
check("no history counts as zero", own([], BOT) == 0)
check("a human at the top resets to zero",
      own([human(), moses(), moses()], BOT) == 0)
check("counts only his own messages, not Atlas's",
      own([moses(), atlas(), moses(), atlas(), human()], BOT) == 2)
check("Atlas talking to himself does not count against Moses",
      own([atlas(), atlas(), atlas()], BOT) == 0)
check("stops counting at the first human",
      own([moses(), human(), moses(), moses()], BOT) == 1)
check("joins and leaves are skipped, not read as humans",
      own(
          [moses(), {"user": "U1", "subtype": "channel_join", "text": "joined"}, moses()], BOT) == 2)

# ── TERMINATION: the only thing that stops a runaway ────────────────────────
reset()
ping_pong = [moses(), atlas()] * 20
ok, why = may(ping_pong, BOT)
check("TERMINATION a runaway exchange is refused", not ok and why == "thread")

reset()
ok, _ = may([moses(), atlas(), moses(), atlas()], BOT)
check("TERMINATION two replies in is still allowed", ok)

reset()
exactly = []
for _ in range(pacing.MAX_OWN_REPLIES):
    exactly += [moses(), atlas()]
ok, why = may(exactly, BOT)
check("TERMINATION the cap is inclusive (>= not >)", not ok and why == "thread")

reset()
revived = [human("what do you two think?")] + [moses(), atlas()] * 20
ok, _ = may(revived, BOT)
check("TERMINATION a human speaking revives a capped thread", ok)

check("TERMINATION the cap is 12, Atlas's per-thread value", pacing.MAX_OWN_REPLIES == 12)
check("TERMINATION eleven replies in is still allowed",
      may([moses(), atlas()] * 11, BOT, now=99_000)[0])

# ── DAILY: 20 reactive replies, one budget for every channel ───────────────
# The turn cap bounds one thread. Without this, twelve turns in each of six threads is a day nobody
# watched, and each thread is individually within its limit the whole time.
check("DAILY the cap is 20", pacing.DAILY_REACTIVE_CAP == 20)
reset()
for i in range(pacing.DAILY_REACTIVE_CAP - 1):
    pacing.record_reply(now=10_000 + i, reactive=True)
check("DAILY one short of the cap still answers", may([atlas()], BOT, now=99_000)[0])
pacing.record_reply(now=10_100, reactive=True)
ok, why = may([atlas()], BOT, now=99_000)
check("DAILY the cap is inclusive, and says which limit refused", not ok and why == "daily")
check("DAILY a fresh thread does not buy more budget",
      not may([human(), atlas()], BOT, now=99_000)[0])

# ── GAP: 30s between his own bot-prompted posts ────────────────────────────
# It shipped at 45s on 2026-08-13 and was pulled the same day for muting him. Back at Atlas's value,
# and these pin the half that was a genuine BUG rather than a value: it is armed by his reactive
# posts only, so answering Brad never delays answering Atlas.
check("GAP the gap is 30s", pacing.MIN_GAP_SECONDS == 30)
reset()
pacing.record_reply(now=10_000, reactive=True)
ok, why = may([atlas()], BOT, now=10_010)
check("GAP ten seconds after his own bot reply is refused", not ok and why == "cooldown")
check("GAP one second short of the gap is still refused",
      not may([atlas()], BOT, now=10_029)[0])
check("GAP at exactly 30s he may speak again", may([atlas()], BOT, now=10_030)[0])
reset()
pacing.record_reply(now=10_000, reactive=False)
ok, _ = may([atlas()], BOT, now=10_000.1)
check("GAP answering a human never delays answering a bot", ok)
reset()
check("GAP no state file means no cooldown — a lost counter must not mute him",
      may([atlas()], BOT, now=10_000)[0])

# ── ASYMMETRY: none of this may ever reach a human ─────────────────────────
reset()
for i in range(200):
    pacing.record_reply(now=10_000 + i, reactive=False)
check("ASYMMETRY human replies never consume anything",
      may([atlas()], BOT, now=99_000)[0])
check("ASYMMETRY the bowing-out line is what Atlas said his would be",
      pacing.BOWING_OUT == "I'll pick this up later.")

# ── State handling ──────────────────────────────────────────────────────────
reset()
pacing.PACING_FILE.write_text('{"day":"1999-01-01","reactive":9999}')
ok, _ = may([atlas()], BOT)
check("STATE yesterday's counters do not carry over", ok)
pacing.PACING_FILE.write_text("not json at all")
ok, _ = may([atlas()], BOT)
check("STATE a corrupt file degrades, it does not crash", ok)
check("STATE the cap is derived from the channel, not a counter file",
      own([moses()] * 9, BOT) == 9)

# ── THE CAP MUST EXPIRE, OR IT IS A MUTE ───────────────────────────────────────
# Measured on the live channel 2026-08-26: the last human message in #the_4_horsemen was 08-23
# 22:18, Moses had posted five times since, and `may_reply_to_bot` returned False. Atlas said good
# morning on the 24th, the 25th and the 26th and was met with silence every time. The cap is meant
# to end a runaway loop in 24 messages, not to mute one agent to another for three days.
print("\nthe cap ends a loop, it does not mute the channel forever")

NOW = 1_800_000_000.0


def at(m, when):
    """Same fixture, with a Slack timestamp — which the older fixtures above deliberately omit."""
    return {**m, "ts": f"{when:.6f}"}


reset()
# A full cap's worth of ping-pong, every message inside the hour window.
burst = []
for i in range(pacing.MAX_OWN_REPLIES):          # newest first, 20s apart
    burst += [at(moses(), NOW - 20 * i - 10), at(atlas(), NOW - 20 * i - 20)]
burst.append(at(human(), NOW - 20 * pacing.MAX_OWN_REPLIES - 30))
check("a capped burst inside the window still stops the loop",
      pacing.consecutive_own_replies(burst, "", MOSES_BOT_ID, now=NOW) >= pacing.MAX_OWN_REPLIES)
check("and the refusal is what the listener sees",
      pacing.may_reply_to_bot(burst, "", MOSES_BOT_ID, now=NOW)[0] is False)

# The same five messages, two days old — a finished conversation, not a runaway one.
stale = [at(atlas(), NOW - 60), at(moses(), NOW - 172800), at(moses(), NOW - 172900),
         at(moses(), NOW - 173000), at(moses(), NOW - 173100), at(human(), NOW - 173200)]
check("the same five replies, two days old, do not count",
      pacing.consecutive_own_replies(stale, "", MOSES_BOT_ID, now=NOW) == 0)
check("so a morning greeting gets answered",
      pacing.may_reply_to_bot(stale, "", MOSES_BOT_ID, now=NOW)[0] is True)

# The guard must not deepen itself: bowing out was one of the five replies keeping him quiet.
# A thread exactly ON the edge — a cap's worth of replies, one of which is the announcement.
bow = [at(moses(pacing.BOWING_OUT), NOW - 10)]
bow += [at(moses(), NOW - 20 * (i + 1)) for i in range(pacing.MAX_OWN_REPLIES - 1)]
bow.append(at(human(), NOW - 20 * pacing.MAX_OWN_REPLIES))
check("the bow-out message does not count against the cap",
      pacing.consecutive_own_replies(bow, "", MOSES_BOT_ID, now=NOW) == pacing.MAX_OWN_REPLIES - 1)
check("so a thread at the edge is not pushed over it by the announcement",
      pacing.may_reply_to_bot(bow, "", MOSES_BOT_ID, now=NOW)[0] is True)

# A fixture with no timestamp must still be capped. Unknown age counts, because the dangerous
# direction is losing the cap — every test above this line relies on it.
check("a message with no timestamp is still counted",
      pacing.consecutive_own_replies([moses(), moses(), moses(), human()],
                                     "", MOSES_BOT_ID, now=NOW) == 3)

passed = sum(1 for _, ok in results if ok)
failed = len(results) - passed
print(f"\n  {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
