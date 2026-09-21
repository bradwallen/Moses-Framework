#!/usr/bin/env python3
"""Pin what counts as an alarm. Run: python3 diagnose_test.py

THE FAILURE THIS EXISTS FOR (2026-08-21): Brad asked Birdeye for a status. Birdeye returned a
perfectly healthy report. Moses diagnosed it anyway and posted "VERDICT: false-alarm — nothing in the
Birdeye report indicates a fault."

The detector had matched the word "failed" inside the healthiest line a backup can produce:

    2 of 56241 files · 147.9 MB transferred · 0 failed · took 0m

A detector that fires on good news is worse than no detector. It costs a model call every time, it
posts noise into #ops, and it teaches the reader to scroll past diagnoses — which is exactly when a
real one gets missed.

BOTH DIRECTIONS MATTER. Suppressing too much is the other way to lose an alarm, so the real Birdeye
and Therapist alarm texts are pinned here too.
"""
import os
import sys
# THIS directory, not /home/brad/Projects/moses/agent. The hardcoded path meant a clone's gate tested
# the live checkout instead of the change under review — a check pointed at the wrong target.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diagnose

passed = failed = 0

def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL {name}: got {got}, want {want}")

# ── The exact message that caused this, verbatim from Slack ──────────────────
REAL_HEALTHY = """:airplane_arriving: *Viatica*
:tada: *Signups* — 2 new users in the last 24h (12 total)
:house: *Reserve — ops*
:white_check_mark: *Backup* — Success, finished Fri Aug 21, 3:00 AM
   2 of 56241 files · 147.9 MB transferred · 0 failed · took 0m
   7 exclude rule(s) applied
:cloud: *iDrive* — 2.4 TB of 4.9 TB (49%)"""

check("the real healthy report is NOT an alarm", diagnose.is_alarm(REAL_HEALTHY), False)

# ── Negated counts say the opposite of the keyword ───────────────────────────
for text in ("0 failed", "no errors", "zero failures", "completed with no issues",
             "0 failed · took 0m", "No problems found"):
    check(f"negated: {text!r}", diagnose.is_alarm(text), False)

# ── Real alarms must still fire ──────────────────────────────────────────────
REAL_ALARMS = [
    (":adhesive_bandage: *Therapist — something needs attention*\n:red_circle: *Viatica returned HTTP 404*", "therapist 404"),
    (":warning: *Needs a look*  _(1 new signup)_\n:red_circle: *Service down*\n   • `moses` — activating", "birdeye service down"),
    ("*Backup* — FAILED, 12 failed files", "backup failed"),
    ("cert expiring in 3 days", "cert expiry"),
    ("*Didn't report:*\n • *Big Pipe* — HTTP 404", "standup overdue"),
    ("iDrive backup is stalled", "stalled"),
    ("Anthropic out of credit", "out of credit"),
    ("Customs unreachable", "unreachable"),
]
for text, name in REAL_ALARMS:
    check(f"alarm: {name}", diagnose.is_alarm(text), True)

# ── A count that is NOT zero is still an alarm ───────────────────────────────
check("12 failed IS an alarm", diagnose.is_alarm("12 failed files"), True)
check("1 error IS an alarm", diagnose.is_alarm("1 error during sync"), True)

# ── Clean reports from every persona ─────────────────────────────────────────
CLEAN = [
    ":white_check_mark: *Reserve* — all clear (backup ok, iDrive 49%, disks ok, services up)",
    ":white_check_mark: *Therapist* — Viatica healthy (reachable, deps ok, certs 64d, domain 304d)",
    ":clipboard: *Standup* — everyone is inside their cadence",
]
for text in CLEAN:
    check(f"clean: {text[:34]!r}", diagnose.is_alarm(text), False)

# ── Every probe says which check it is and what it ran against ───────────────
# Coverage alone said "it looked" and not "at what": a check pointed at staging answers 200 exactly
# like one pointed at production. These run a real process, and the scope comes from what each was
# invoked with — never from what it printed, which a failing check may not.
V = diagnose.VIATICA

out, cov = diagnose._run(["echo", V], label="direct HTTP check", target=V)
check("right target: names the check", "check **direct HTTP check**" in cov, True)
check("right target: names the scope it ran against", f"scope **{V}**" in cov, True)
check("right target: passed", "exit **0**" in cov and "(failed)" not in cov, True)
check("right target: not flagged", "⚠️" in cov, False)
check("right target: the original fields still lead the line",
      cov.startswith("_coverage: ran `echo https://") and "actually looked: **yes**" in cov, True)

# The case scraping lost: birdeye-customs erroring out without printing its URL read as "scope unknown",
# so a real site-down was reported as "couldn't tell".
out, cov = diagnose._run(["sh", "-c", "echo 'connection refused' >&2; exit 7"], label="birdeye-customs", target=V)
check("silent failure: still shows its registered target", f"scope **{V}**" in cov, True)
check("silent failure: counts as a failure", "exit **7** (failed)" in cov, True)
check("silent failure: not flagged away as unknown", "⚠️" in cov, False)

# Invoked against staging — and printing nothing, so only the invocation can tell.
out, cov = diagnose._run(["true", "https://staging.viatica.travel/"], label="direct HTTP check", target=V)
check("wrong target: flagged as a mismatch from the invocation", "scope MISMATCH: registered for" in cov, True)
check("wrong target: says where it was invoked", "invoked against **https://staging.viatica.travel/**" in cov, True)

# Output is a cross-check: a URL configured inside the check that points elsewhere is still caught.
cov = diagnose.coverage(["/usr/local/bin/birdeye-customs"], "UP https://staging.viatica.travel/", 0,
                        "birdeye-customs", V)
check("cross-check: output naming only another site is flagged", "but its output names only" in cov, True)
cov = diagnose.coverage(["/usr/local/bin/birdeye-customs"], f"UP {V}api/health", 0, "birdeye-customs", V)
check("cross-check: a page on the right site is not", "⚠️" in cov, False)

out, cov = diagnose._run(["/nonexistent/probe"], label="gone", target="/nonexistent/probe")
check("never ran: keeps its target, says it did not look",
      "scope **/nonexistent/probe**" in cov and "actually looked: **no**" in cov, True)

cov = diagnose.coverage(["uptime"], "up 3 days", 0)
check("nothing names a target: scope unknown and flagged", "scope **unknown**" in cov and "⚠️ scope unknown" in cov, True)
cov = diagnose.coverage(["uptime"], "", 2)
check("unknown scope: a failure still counts", "exit **2** (failed)" in cov and "a failure is still a failure" in cov, True)
cov = diagnose.coverage(["/usr/bin/uptime"], "up 3 days", 0)
check("unregistered check: flagged, not passed", "registered for no target" in cov, True)

check("a trailing slash is the same site",
      "⚠️" in diagnose.coverage(["x", "https://viatica.travel"], "", 0, "c", V), False)

# The registry agrees with itself: every probe, fed a healthy run, reads back its own target. A probe
# whose argv and target drift apart would be flagged on every real alarm, so it fails here first.
for keys, label, argv, note, target in diagnose.PROBES:
    said = f"HTTP 200 at {target}" if target.startswith("http") else "ok"
    check(f"registered: {label}", "⚠️" in diagnose.coverage(argv, said, 0, label, target), False)
for label, argv, target in diagnose.ALWAYS:
    check(f"registered: {label}", "⚠️" in diagnose.coverage(argv, "ok", 0, label, target), False)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
