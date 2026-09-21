#!/usr/bin/env python3
"""Pin the burst coalescer. Run: python3 coalesce_test.py

THE BUG THIS EXISTS FOR (2026-08-15 12:51): three messages in one second, three Socket Mode handler
threads, three replies to one conversational moment — carrying three differently worded versions of
the same proposed task.

The tests use REAL THREADS, deliberately. A sequential test of claim/release would pass against a
plain timer too, and a plain timer does not fix this: three handlers that each sleep two seconds
sleep in parallel and all three still post. The property under test only exists under concurrency,
so the test has to be concurrent or it is testing something else.

The second half is the inverse, and it is the one that matters more. This project already removed a
minimum-gap rule because it silently muted him. A coalescer that drops a message is that same bug
wearing a different hat, so: every message either owns a window or is folded into one that is still
going to produce an answer, and the window is released before the slow part rather than after.
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coalesce  # noqa: E402

results = []


def check(label, cond):
    results.append((label, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + label)


CH = ("C_CHAT", "")

# ── THE INCIDENT: three handlers, one reply ────────────────────────────────────────────────────
c = coalesce.Coalescer(window=0.4)
owners, barrier = [], threading.Barrier(3)


def handler():
    barrier.wait()                       # make them genuinely simultaneous, not merely fast
    if c.claim(CH):
        c.wait(CH)
        owners.append(threading.current_thread().name)
        c.release(CH)


threads = [threading.Thread(target=handler, name=f"t{i}") for i in range(3)]
for t in threads:
    t.start()
for t in threads:
    t.join()

check("BURST three simultaneous messages produce exactly ONE reply", len(owners) == 1)

# ── Nothing is lost: the owner is told how many it is answering for ────────────────────────────
c = coalesce.Coalescer(window=0.4)
check("FOLD the first message owns the window", c.claim(CH) is True)
check("FOLD the second is folded, not answered separately", c.claim(CH) is False)
check("FOLD the third too", c.claim(CH) is False)
folded, _ = c.release(CH)
check("FOLD the owner knows it speaks for all three", folded == 2)

# ── NOT A MUTE. A message after the window gets its own answer. ────────────────────────────────
c = coalesce.Coalescer(window=0.15)
check("WINDOW the first owns it", c.claim(CH) is True)
c.wait(CH)
c.release(CH)
check("WINDOW a message arriving after it is answered, not swallowed", c.claim(CH) is True)

# The release happens BEFORE the model call, not after. If the window stayed open across those
# several seconds, a message arriving mid-thought would be folded into a reply already composed
# without it — dropped, with nothing to show it ever arrived.
c = coalesce.Coalescer(window=0.1)
c.claim(CH)
c.wait(CH)
c.release(CH)
time.sleep(0.05)                          # stand in for the model call
check("MUTE a message during the slow part still gets a reply", c.claim(CH) is True)

# ── Rooms are separate. A burst in one channel must not swallow another's message. ─────────────
c = coalesce.Coalescer(window=0.4)
check("ROOM a channel claims its own window", c.claim(("C_ONE", "")) is True)
check("ROOM a different channel is unaffected", c.claim(("C_TWO", "")) is True)
check("ROOM a thread is a different room from its parent channel",
      c.claim(("C_ONE", "1699.0001")) is True)

# ── The reply-cap budget: a person in the burst makes it a reply to a person ───────────────────
# The cap is asymmetric on purpose — a human always gets an answer, a bot's replies are rationed.
# Folding a human's message into a bot-triggered window must not quietly reclassify it.
c = coalesce.Coalescer(window=0.4)
c.claim(CH, human=False)                  # a bot spoke first
c.claim(CH, human=True)                   # a person spoke into the same window
_, had_human = c.release(CH)
check("BUDGET a person in the burst makes the reply count as human", had_human is True)

c = coalesce.Coalescer(window=0.4)
c.claim(CH, human=False)
c.claim(CH, human=False)
_, had_human = c.release(CH)
check("BUDGET an all-bot burst stays a bot reply", had_human is False)

# ── Degrade, don't crash (VI) ──────────────────────────────────────────────────────────────────
c = coalesce.Coalescer(window=0.1)
c.wait(("C_NEVER_CLAIMED", ""))           # must return immediately, not hang or raise
folded, had_human = c.release(("C_NEVER_CLAIMED", ""))
check("SAFE releasing an unclaimed window is harmless", folded == 0)
check("SAFE an unknown window defaults to the permissive answer", had_human is True)

passed = sum(1 for _, ok in results if ok)
failed = len(results) - passed
print(f"\n  {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
