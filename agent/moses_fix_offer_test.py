#!/usr/bin/env python3
"""moses_fix_offer_test — a drift line only offers a fix moses-fix can actually run.

    ~/moses-venv/bin/python agent/moses_fix_offer_test.py

WHY (2026-11-03): the standup now says "I can fix this" under the drift lines Moses can fix, and Brad
answers in the thread. An offer naming an op moses-fix does not have would be a promise nothing can
keep — the same disease as the capability sentence that denied a tool he had (2026-08-22). So the two
lists are compared here, and the framework check is run against stand-ins to see each offer appear.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
fails = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  —  {detail}" if detail and not cond else ""))
    if not cond:
        fails += 1


def load(name: str, path: Path):
    spec = importlib.util.spec_from_loader(name, importlib.machinery.SourceFileLoader(name, str(path)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


drift = load("moses_drift", HERE / "moses-drift")
fix = load("moses_fix", HERE / "moses-fix")

print("the offers and the ops agree")
check("every op the standup offers is one moses-fix has", set(drift.FIXES) <= set(fix.OPS),
      f"offered but missing: {set(drift.FIXES) - set(fix.OPS)}")
listed = subprocess.run([str(HERE / "moses-fix"), "ops"], capture_output=True, text=True).stdout
check("… and moses-fix lists each of them when asked", all(op in listed for op in drift.FIXES), listed)
for op in drift.FIXES:
    line = drift.fix_line(op)
    check(f"the {op} offer names the op, the safety checks and how to accept",
          f"`{op}`" in line and "private topics" in line and "Reply in this thread" in line, line)

print("the framework check offers the right fix")


def run_check(behind_rc: int, unpushed: str) -> list[tuple[str, str, str]]:
    """Run check_framework_in_step with the sync and git answers stubbed."""
    drift.DRIFT.clear(); drift.BLIND.clear(); drift.AGREED.clear(); drift.OFFERS.clear()
    real_sh = drift.sh

    def fake_sh(argv, env=None):
        if "--check" in argv:
            return behind_rc, "framework is behind: 3 file(s) to copy" if behind_rc == 1 else "in step: 0"
        if "rev-list" in argv:
            return 0, unpushed
        return real_sh(argv, env)

    drift.sh = fake_sh
    try:
        drift.check_framework_in_step()
    finally:
        drift.sh = real_sh
    return [(w, d, drift.OFFERS.get(w, "")) for w, d in drift.DRIFT]


d = run_check(1, "0")
check("behind only: one line, offering the sync", len(d) == 1 and d[0][2] == "framework-sync", str(d))
d = run_check(0, "2")
check("unpushed only: one line, offering the push", len(d) == 1 and d[0][2] == "framework-push", str(d))
d = run_check(1, "1")
check("both: the sync is offered once, and the push line points at it",
      len(d) == 2 and d[0][2] == "framework-sync" and d[1][2] == "" and "sync above" in d[1][1], str(d))
d = run_check(0, "0")
check("in step and pushed: nothing to offer", d == [], str(d))

print(f"\n{'FAILED' if fails else 'OK'}{f' — {fails} failure(s)' if fails else ''}")
raise SystemExit(1 if fails else 0)
