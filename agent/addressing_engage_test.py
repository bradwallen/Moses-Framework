#!/usr/bin/env python3
"""A considered PASS is participation, not departure — and it still ends eventually.

    python3 agent/addressing_engage_test.py

THE FAILURE THIS EXISTS FOR, measured 2026-09-08. Moses evaluated one of Atlas's messages on the 7th
at 11:01, decided he had nothing to add, and passed. A pass leaves NO message in Slack, so his own
last turn stayed where it was, slid out of the 3-message window, and he never engaged again — five
of Atlas's messages over two days. Only his NAME could let him back in, and nobody repeats a name
mid-conversation, which is the exact problem `in_conversation` was written to solve.

The cases that matter are the two ENDINGS: it must still stale out, and passing forever must still
drop him. A membership that never lapses is just the old "reply to everything" with extra steps.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ["MOSES_STATE"] = TMP
sys.path.insert(0, str(Path(__file__).resolve().parent))
import addressing as A  # noqa: E402

fails: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✓' if cond else '✗'} {label}" + (f" — {detail}" if not cond and detail else ""))
    if not cond:
        fails.append(label)


CH = "C0BQ4PTUJV8"
import time  # noqa: E402

now = time.time()

check("with no record at all, he is not in a conversation", not A.engaged_recently(CH, now))

# The real sequence: he considers, and passes.
A.record_considered(CH, passed=True, now=now - 300)
check("after a PASS he is still in the room", A.engaged_recently(CH, now),
      "one pass ejected him — this is the bug")

# The history Slack shows at that moment says otherwise, and that is the point.
hist = [{"user": "U0BPPAZKBEK", "ts": str(now - i)} for i in range(6)]
check("...even though Slack history alone says he is not",
      not A.in_conversation(hist, "U0BMTTVT648", ""))

# It must still stale out on its own.
A.record_considered(CH, passed=True, now=now - (13 * 3600))
check("but a 13-hour-old consideration has lapsed", not A.engaged_recently(CH, now))

# And passing forever is not participation either.
for i in range(A.MAX_CONSECUTIVE_PASSES):
    A.record_considered(CH, passed=True, now=now - 60)
check(f"after {A.MAX_CONSECUTIVE_PASSES} passes in a row he drops out for real",
      not A.engaged_recently(CH, now))

# Actually saying something clears the streak — he is contributing again.
A.record_considered(CH, passed=False, now=now - 60)
check("and speaking resets it", A.engaged_recently(CH, now))

# Per channel, so #ops going quiet cannot pull him out of the chat channel.
A.record_considered("C0BM0AG70AV", passed=True, now=now - (20 * 3600))
check("the record is per channel", A.engaged_recently(CH, now)
      and not A.engaged_recently("C0BM0AG70AV", now))

# Bookkeeping must never be able to break the reply path.
A._ENGAGED.write_text("{ not json", encoding="utf-8")
check("a corrupt record reads as 'not engaged' rather than throwing",
      A.engaged_recently(CH, now) is False)

print(f"\n  {len(fails)} failed" if fails else "\n  all good")
sys.exit(1 if fails else 0)
