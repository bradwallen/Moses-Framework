#!/usr/bin/env python3
"""The deliverability probe must never let "could not look" render as "all clear".

    python3 tests/deliverability_test.py

TESTS THE REAL FILE, NOT A COPY. The verdict parser is EXTRACTED from personas/health-expiry and
executed, so this cannot pass against logic that was proven in a scratch shell and then edited in
the script — the exact failure that told Brad his correct Slack config was wrong.

The cases that matter are the blind ones. A send-scoped API key cannot read metrics at all, and the
deps check already cried wolf on precisely that in August 2026; a monitor that raises false alarms
gets ignored, which costs more than the check was worth. So "the key cannot read metrics" must be
its own state — not an outage, and emphatically not silence.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "personas" / "health-expiry"
fails = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}{(' — ' + detail) if detail else ''}")
        fails.append(name)


def parser_source() -> str:
    src = SCRIPT.read_text()
    m = re.search(r"<<'PYEMAIL'[^\n]*\n(.*?)\nPYEMAIL\n", src, re.S)
    assert m, "the verdict parser is gone from health-expiry — this test is watching nothing"
    return m.group(1)


def _tmp(payload) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        return f.name


def verdict(week, day=None) -> str:
    """The parser takes BOTH windows: 7 days decides, 24 hours only colors the line."""
    if day is None:
        day = week
    p = subprocess.run([sys.executable, "-", _tmp(day), _tmp(week)], input=parser_source(),
                       capture_output=True, text=True, timeout=30)
    return (p.stdout or "").strip()


PARSER = parser_source()

# ── healthy ──────────────────────────────────────────────────────────────────
v = verdict({"ok": True, "sent": 1000, "delivered": 998, "bounced": 2, "complained": 0,
             "delivery_rate": 0.998, "bounce_rate": 0.002, "complaint_rate": 0.0,
             "bounce_warn": 0.05, "complaint_warn": 0.001})
check("healthy mail reports OK", v.startswith("OK|"), v)
check("healthy mail names the volume", "1000 sent in 7d" in v, v)

# THE REAL SHAPE OF THIS ACCOUNT, measured on Customs 2026-09-02: 16 in a week, 0 in the last day.
# This MUST NOT alarm. A guard that fires on correct work gets switched off, and at this volume a
# zero-day is the common case, not an outage.
v = verdict({"ok": True, "sent": 16, "delivered": 16, "bounced": 0, "complained": 0,
             "delivery_rate": 1.0, "bounce_rate": 0.0, "complaint_rate": 0.0,
             "bounce_warn": 0.05, "complaint_warn": 0.001},
            day={"ok": True, "sent": 0, "delivered": 0, "bounced": 0, "complained": 0,
                 "bounce_rate": None, "complaint_rate": None})
check("a quiet day against a live week does NOT alarm", v.startswith("OK|"), v)
check("and the quiet day is still stated", "none today" in v, v)

# One bounce out of 16 is a 6% rate — over the 5% line and meaningless. A percentage of a handful is
# noise wearing a decimal point.
v = verdict({"ok": True, "sent": 16, "delivered": 15, "bounced": 1, "complained": 0,
             "bounce_rate": 0.0625, "complaint_rate": 0.0,
             "bounce_warn": 0.05, "complaint_warn": 0.001})
check("a rate below the sample floor cannot alarm", v.startswith("OK|"), v)
check("and it says the sample was too small", "too few to rate" in v, v)

# ── the blind cases: NEVER all-clear ─────────────────────────────────────────
v = verdict({"ok": False, "reason": "forbidden",
             "detail": "the API key cannot read metrics (send-scoped) — deliverability NOT checked"})
check("a send-scoped key is BLIND, not OK", v.startswith("BLIND|"), v)
check("and it says it was not checked", "NOT checked" in v, v)

v = verdict({"ok": False, "reason": "unreachable", "detail": "ETIMEDOUT"})
check("an unreachable provider is BLIND", v.startswith("BLIND|"), v)

v = verdict({"ok": False, "reason": "unconfigured", "detail": "RESEND_API_KEY is not set"})
check("an unconfigured key is BLIND", v.startswith("BLIND|"), v)

# A missing or unparseable file must be blindness, not an exception and not silence. Written as a
# real two-argument call because the parser's contract is two windows — the single-argument version
# of this test passed for the wrong reason and then broke when the contract changed.
out = subprocess.run([sys.executable, "-", "/nonexistent-a.json", "/nonexistent-b.json"],
                     input=PARSER, capture_output=True, text=True, timeout=30).stdout.strip()
check("an unreadable response is BLIND", out.startswith("BLIND|"), out)

# ── zero sent: a finding, not silence ────────────────────────────────────────
v = verdict({"ok": True, "sent": 0, "delivered": 0, "bounced": 0, "complained": 0,
             "delivery_rate": None, "bounce_rate": None, "complaint_rate": None})
check("no mail in SEVEN DAYS is QUIET, and QUIET is a finding", v.startswith("QUIET|"), v)
check("the quiet alarm names the seven-day window", "7 days" in v, v)

# ── thresholds ───────────────────────────────────────────────────────────────
v = verdict({"ok": True, "sent": 500, "delivered": 400, "bounced": 100, "complained": 0,
             "bounce_rate": 0.2, "complaint_rate": 0.0, "bounce_warn": 0.05, "complaint_warn": 0.001})
check("a high bounce rate is BAD once the sample is real", v.startswith("BAD|"), v)

v = verdict({"ok": True, "sent": 1000, "delivered": 1000, "bounced": 0, "complained": 5,
             "bounce_rate": 0.0, "complaint_rate": 0.005, "bounce_warn": 0.05, "complaint_warn": 0.001})
check("a complaint spike is BAD", v.startswith("BAD|"), v)
check("and the complaint case is named, not just flagged", "complaint" in v.lower(), v)

# A rate exactly AT the warning line must not fire. A guard that fires on correct work gets switched
# off, and "at the threshold" is the value somebody deliberately tuned to.
v = verdict({"ok": True, "sent": 1000, "delivered": 950, "bounced": 50, "complained": 1,
             "bounce_rate": 0.05, "complaint_rate": 0.001, "bounce_warn": 0.05, "complaint_warn": 0.001})
check("exactly at the threshold does NOT alarm", v.startswith("OK|"), v)

# ── the seam: every verdict the parser emits must be handled by the shell ────
emitted = set(re.findall(r'print\(f?"([A-Z]+)\|', PARSER))
handled = set(re.findall(r"^\s+(OK|QUIET|BAD)\)", SCRIPT.read_text(), re.M))
has_wildcard = re.search(r"^\s+\*\)\s", SCRIPT.read_text(), re.M) is not None
unhandled = emitted - handled
check("every verdict is either handled by name or by the wildcard",
      not unhandled or has_wildcard, f"unhandled and no wildcard: {unhandled}")
check("the parser can emit the blind verdict at all", "BLIND" in emitted, str(emitted))
# COUNTING OCCURRENCES MEASURED NOTHING. The first version asserted "$MAIL_STATE" appeared at
# least twice anywhere in the file — and it appears in the declaration and the assignments, so
# deleting it from the all-clear line left this green. Proven: the sabotage passed. Assert it
# reaches each SUMMARY, which is the only place Brad ever reads it.
src = SCRIPT.read_text()
oneline = re.search(r'if \[ "\$ONELINE" -eq 1 \]; then(.*?)\nfi\n', src, re.S)
allclear = re.search(r'if \[ "\$ISSUES" -eq 0 \]; then(.*?)\nfi\n', src, re.S)
check("the oneline summary carries the mail state", bool(oneline) and "$MAIL_STATE" in oneline.group(1))
check("the all-clear summary carries the mail state", bool(allclear) and "$MAIL_STATE" in allclear.group(1))

print()
if fails:
    print(f"{len(fails)} FAILED: " + ", ".join(fails))
    sys.exit(1)
print("deliverability_test: all checks pass")
