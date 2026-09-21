#!/usr/bin/env python3
"""Pin who gets told when a Knight job or a Zryachiy exploration ends. Run: python3 knight_notify_test.py

The failure this guards (2026-09-11): Brad asked to be pinged in the channel when Knight finished,
Moses promised it twice, and nothing existed that could. The listener now ties a job started during
a turn to that turn's conversation; the runners read the tie. These pin the tie. Knight's half is
pinned in knight/test-notify.sh.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(tempfile.mkdtemp(prefix="knight-notify-"))
os.environ["KNIGHT_JOBS"] = str(_ROOT / "knight")
os.environ["ZRYACHIY_JOBS"] = str(_ROOT / "zryachiy")
(_ROOT / "knight").mkdir()
(_ROOT / "zryachiy").mkdir()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import knight_notify as K  # noqa: E402

fails = 0


def check(label, cond):
    global fails
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        fails += 1


jobs = _ROOT / "knight"
explore = _ROOT / "zryachiy"
(jobs / "old-job").mkdir()
before = K.snapshot()
check("the snapshot sees jobs that exist", before == {str(jobs / "old-job")})

(jobs / "new-job").mkdir()
got = K.claim(before, channel="C_H", thread_ts="", user="U_BRAD")
check("a job started during the turn is claimed", got == ["new-job"])
n = json.loads((jobs / "new-job" / "notify.json").read_text())
check("it records the channel, thread and who asked",
      n == {"channel": "C_H", "thread_ts": "", "user": "U_BRAD"})
check("a job that existed before the turn is NOT claimed",
      not (jobs / "old-job" / "notify.json").exists())
check("no half-written temp file is left behind", not (jobs / "new-job" / ".notify.json.tmp").exists())

before = K.snapshot()
(explore / "zry-1").mkdir()
got = K.claim(before, channel="C_H", thread_ts="9.9", user="U_BRAD")
check("a Zryachiy exploration started during the turn is claimed too", got == ["zry-1"])
check("and records its thread",
      json.loads((explore / "zry-1" / "notify.json").read_text())["thread_ts"] == "9.9")

before = K.snapshot()
(jobs / "done-job").mkdir()
(jobs / "done-job" / "ended").write_text("x")
check("a job that already ended is not claimed; nothing would read it",
      K.claim(before, channel="C_H") == [])

before = K.snapshot()
(jobs / "twice").mkdir()
K.claim(before, channel="C_FIRST")
K.claim(before, channel="C_SECOND")
check("a claim is never overwritten by a later turn",
      json.loads((jobs / "twice" / "notify.json").read_text())["channel"] == "C_FIRST")

check("nothing new means nothing claimed", K.claim(K.snapshot(), channel="C_H") == [])

K.JOBS = _ROOT / "does-not-exist"
K.ZRYACHIY_JOBS = _ROOT / "nor-this"
check("a missing job record is an empty snapshot, not a crash", K.snapshot() == set())
check("and claims nothing", K.claim(set(), channel="C_H") == [])

print(f"\n  {'all passed' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
