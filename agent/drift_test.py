#!/usr/bin/env python3
"""The drift check must not depend on WHO runs it.

    python3 agent/drift_test.py

WHY THIS EXISTS: `check_tracked_units` compared the repo's units against
`Path.home()/.config/systemd/user`. Correct when Brad runs it by hand, wrong every single morning —
the 08:00 standup is a SYSTEM unit running as root, so that resolved to /root and six user units
that were running perfectly well were reported daily as "the repo describes a unit systemd does not
have". It went to the ops channel from 24 August until 26 August.

The cost is not the wrong line, it is the credibility: drift is the only thing watching for real
drift, and a report that cries wolf every morning is one people stop reading. On the morning it was
finally investigated it was also, correctly, reporting undeployed code — buried under six lies.
"""
import importlib.machinery
import importlib.util
import os
import pwd
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_loader(
    "moses_drift", importlib.machinery.SourceFileLoader("moses_drift", str(HERE / "moses-drift")))
drift = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drift)

fails = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  — {detail}"))
    if not cond:
        fails.append(label)


owner_home = Path(pwd.getpwuid(drift.SRC.stat().st_uid).pw_dir)
expected = owner_home / ".config/systemd/user"

print("the user-unit directory belongs to the repo owner, not the caller")
check("it resolves to the owner's home", drift.user_unit_dir() == expected,
      f"{drift.user_unit_dir()} != {expected}")

# The actual failure, reproduced: root ran it and every user unit vanished.
_real_home = os.environ.get("HOME")
try:
    for fake in ("/root", "/nonexistent", "/tmp"):
        os.environ["HOME"] = fake
        check(f"HOME={fake} does not move it", drift.user_unit_dir() == expected,
              f"resolved to {drift.user_unit_dir()}")
finally:
    if _real_home is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = _real_home

# A guard that cannot be wrong is not a guard. If the units ever genuinely stop being where this
# says they are, that is real drift and the check above must not paper over it.
check("and that directory is real", expected.is_dir(), f"{expected} does not exist")

# ── EVERY CHECK MUST BE ADDRESSABLE BY NAME ──────────────────────────────────
# The root cause on 2026-09-02 was not that a check was wrong — `check_deployed_matches_source` was
# exactly right. It was that the only way to run it was the daily sweep, so a true finding could not
# be re-tested when it mattered. A check nobody can re-run on demand has to be believed or ignored.
#
# This asserts the registry stays complete, so the next check added does not quietly inherit the
# same defect. Discovered from the module rather than a hand-kept list — a checker that scans a list
# somebody remembered to update reports clean about the files they remembered.
registered = set(drift.CHECKS.values())
defined = {fn for name, fn in vars(drift).items()
           if name.startswith("check_") and callable(fn)}
missing = sorted(fn.__name__ for fn in defined - registered)
check("every check is addressable with --only", not missing,
      f"not in CHECKS: {', '.join(missing)}")
check("CHECKS names are unique", len(drift.CHECKS) == len(set(drift.CHECKS)))

# ── /opt is only worth comparing while something runs from it ────────────────
# After the listener moves to a user unit running its source directly, /opt/moses is a leftover.
# Comparing it then would report drift every morning about a directory nothing executes — and this
# check is the only thing watching for REAL deploy drift, so a daily false alarm switches off the
# one instrument that matters.
_real_sh = drift.sh
try:
    drift.DRIFT.clear(); drift.BLIND.clear(); drift.AGREED.clear()
    drift.sh = lambda argv, env=None: (0, "/home/brad/moses-venv/bin/python /home/brad/Projects/moses/agent/listener.py")
    drift.check_deployed_matches_source()
    check("a /opt nothing runs from is not drift",
          not drift.DRIFT and any("nothing runs from" in a for a in drift.AGREED),
          f"drift={drift.DRIFT} agreed={drift.AGREED[-1:] }")

    drift.DRIFT.clear(); drift.BLIND.clear(); drift.AGREED.clear()
    drift.sh = lambda argv, env=None: (0, "/opt/moses/venv/bin/python /opt/moses/listener.py")
    drift.check_deployed_matches_source()
    check("but a /opt that IS running is still compared",
          not any("nothing runs from" in a for a in drift.AGREED),
          "the check skipped a live deployment")

    # BOTH sources have to be unavailable before this is genuinely unanswerable. Since 2026-09-09
    # an unreachable systemctl falls back to the unit FILE, because the standup runs as root and
    # `systemctl --user` cannot answer for the operator — that is what made this check report
    # eleven modules missing from a deleted directory every morning. Being unable to reach systemd
    # is no longer the same as being unable to answer, so this fixture hides the file too.
    drift.DRIFT.clear(); drift.BLIND.clear(); drift.AGREED.clear()
    _uud = drift.user_unit_dir
    try:
        drift.sh = lambda argv, env=None: (1, "")
        drift.user_unit_dir = lambda: Path("/nonexistent/systemd/user")
        drift.check_deployed_matches_source()
        check("and when NEITHER systemd nor the unit file can answer, the comparison is not skipped",
              not any("nothing runs from" in a for a in drift.AGREED),
              "could-not-ask was treated as not-running")
    finally:
        drift.user_unit_dir = _uud
finally:
    drift.sh = _real_sh
    drift.DRIFT.clear(); drift.BLIND.clear(); drift.AGREED.clear()


# ── check_work_is_recorded ──────────────────────────────────────────────────────────────────────
# The registry is the estate's memory of what is happening; commits are what actually happened.
# This check exists because the two had silently parted, so the tests below are all about the
# BOUNDARY: newer, older, inside the grace window, and unreadable. A check that says "agreed" when
# it could not look is the failure mode this whole tool was written against.
import datetime as _dt
import json as _json
import subprocess as _sp
import tempfile as _tf

def _repo(tmp, when=None):
    """A real git checkout with one commit, optionally backdated."""
    r = Path(tmp) / "r"
    r.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when.isoformat()
    _sp.run(["git", "init", "-q", str(r)], check=True, env=env)
    (r / "f").write_text("x")
    _sp.run(["git", "-C", str(r), "add", "f"], check=True, env=env)
    _sp.run(["git", "-C", str(r), "commit", "-qm", "a commit"], check=True, env=env)
    return r

def _run(tmp, repo, last_change, status="parked"):
    reg = Path(tmp) / "projects.json"
    proj = {"id": "t", "name": "T", "repo": str(repo), "status": status,
            "stage": "building", "milestones": [], "ideas": []}
    if last_change:
        proj["last_change"] = {"at": last_change.replace(microsecond=0).isoformat(), "by": "test"}
    reg.write_text(_json.dumps({"projects": [proj]}))
    drift.DRIFT.clear(); drift.BLIND.clear(); drift.AGREED.clear()
    _was = drift.REGISTRY
    drift.REGISTRY = reg
    try:
        drift.check_work_is_recorded()
        return list(drift.DRIFT), list(drift.BLIND), list(drift.AGREED)
    finally:
        drift.REGISTRY = _was
        drift.DRIFT.clear(); drift.BLIND.clear(); drift.AGREED.clear()

now = _dt.datetime.now()
print()
with _tf.TemporaryDirectory() as tmp:
    r = _repo(tmp, now - _dt.timedelta(days=3))
    d, b, a = _run(tmp, r, now - _dt.timedelta(days=10))
    check("a commit newer than the registry entry is drift",
          any("code moved" in w for w, _ in d), f"got {d}")

    d, b, a = _run(tmp, r, now - _dt.timedelta(hours=1))
    check("a registry entry newer than the commit is not",
          not d and any("no older than" in x for x in a), f"got drift={d} agreed={a}")

    d, b, a = _run(tmp, r, None)
    check("an entry that was never stamped at all is drift",
          any("never been updated" in w for w, _ in d), f"got {d}")

with _tf.TemporaryDirectory() as tmp:
    # INSIDE THE GRACE WINDOW. Work that landed an hour ago is not yet a failure to record it, and
    # firing here is what would get this switched off.
    r = _repo(tmp, now - _dt.timedelta(hours=1))
    d, b, a = _run(tmp, r, now - _dt.timedelta(days=10))
    check("a commit inside the grace window is NOT reported",
          not d, f"fired too early: {d}")

with _tf.TemporaryDirectory() as tmp:
    r = _repo(tmp, now - _dt.timedelta(days=40))
    d, b, a = _run(tmp, r, now - _dt.timedelta(days=40), status="active")
    check("declared active with nothing moving either side is drift",
          any('declared "active"' in w for w, _ in d), f"got {d}")
    d, b, a = _run(tmp, r, now - _dt.timedelta(days=40), status="parked")
    check("the same thing PARKED is not drift",
          not any('declared "active"' in w for w, _ in d), f"parked should be quiet: {d}")

with _tf.TemporaryDirectory() as tmp:
    # An ACTIVE project with no repo must be BLIND, never agreement — work could be happening
    # where this cannot see it. A PARKED one owes no checkout and must stay silent, or nine
    # standing lines bury the one that means something.
    def _norepo(status):
        reg = Path(tmp) / f"projects-{status}.json"
        reg.write_text(_json.dumps({"projects": [{"id": "n", "name": "N", "status": status}]}))
        drift.DRIFT.clear(); drift.BLIND.clear(); drift.AGREED.clear()
        _was = drift.REGISTRY; drift.REGISTRY = reg
        try:
            drift.check_work_is_recorded()
            return list(drift.BLIND), list(drift.AGREED)
        finally:
            drift.REGISTRY = _was
            drift.DRIFT.clear(); drift.BLIND.clear(); drift.AGREED.clear()

    b, a = _norepo("active")
    check("an ACTIVE project with no repo is could-not-check, not agreement",
          any("declare no repo" in why for _, why in b) and not a, f"blind={b} agreed={a}")
    b, a = _norepo("parked")
    check("a PARKED project with no repo says nothing at all",
          not b and not a, f"blind={b} agreed={a}")

    # And an unreadable registry must not read as a clean bill of health.
    drift.DRIFT.clear(); drift.BLIND.clear(); drift.AGREED.clear()
    _was = drift.REGISTRY; drift.REGISTRY = Path(tmp) / "does-not-exist.json"
    try:
        drift.check_work_is_recorded()
        check("an unreadable registry is could-not-check, not agreement",
              drift.BLIND and not drift.AGREED and not drift.DRIFT,
              f"blind={drift.BLIND} agreed={drift.AGREED}")
    finally:
        drift.REGISTRY = _was
        drift.DRIFT.clear(); drift.BLIND.clear(); drift.AGREED.clear()


# ── The answer must not depend on WHO asks ──────────────────────────────────
#
# THE SAME BUG AS THIS FILE'S HEADER, one layer down. /opt/moses was deleted on 2026-09-02 when the
# listener became a user unit running its source. `deployment_is_live()` was written to notice that
# and stop comparing — but it asked ONLY `systemctl --user`, and the standup runs as ROOT, where
# that talks to root's own manager and cannot answer for the operator. It returned None, callers
# correctly refused to read could-not-ask as not-running, and the 08:00 report named eleven modules
# as missing from a directory deleted on purpose. Every morning for a week, in the report Brad reads.
#
# The unit file says the same thing and any user can read it. What must NOT happen is the opposite
# over-correction: a silent all-clear when nothing could actually be consulted.
_sh, _uud = drift.sh, drift.user_unit_dir
try:
    drift.sh = lambda *a, **k: (1, "")          # systemd unreachable — exactly what root gets
    check("with systemd unreachable, the unit file answers instead of giving up",
          drift.deployment_is_live() is False,
          f"got {drift.deployment_is_live()!r} — the retired /opt/moses reports as drift again")

    drift.user_unit_dir = lambda: Path("/nonexistent/systemd/user")
    check("but with NEITHER source available it says so, rather than assuming",
          drift.deployment_is_live() is None,
          f"got {drift.deployment_is_live()!r} — could-not-ask must never mean not-running")

    drift.user_unit_dir = _uud
    drift.sh = lambda *a, **k: (0, f"/usr/bin/python {drift.DEPLOYED}/listener.py")
    check("and when systemd CAN be asked, its answer decides",
          drift.deployment_is_live() is True,
          f"got {drift.deployment_is_live()!r}")
finally:
    drift.sh, drift.user_unit_dir = _sh, _uud


# ── A milestone that contradicts its own title ───────────────────────────────
#
# The recency check compares TIMESTAMPS — was the registry edited after the last commit — and never
# reads what it says. So the Projects page showed "Delivery chain … (SHIPPED 2026-09-04)" sitting
# unticked, and travel-day mode filed as an idea the day after it shipped, while drift reported
# "declared matches actual". Brad, 2026-09-09: "the Projects page is way off as usual."
#
# The narrowness is the point. A guard that fires on correct work gets switched off, and these
# titles talk about shipping constantly — so only an uppercase SHIPPED/CLOSED with an ISO date
# beside it counts as the entry contradicting itself.
def _reg(tmp, milestones):
    f = Path(tmp) / "projects.json"
    f.write_text(_json.dumps({"projects": [{"id": "p", "milestones": milestones}]}))
    return f

_was_reg = drift.REGISTRY
try:
    with _tf.TemporaryDirectory() as tmp:
        drift.DRIFT.clear(); drift.BLIND.clear(); drift.AGREED.clear()
        drift.REGISTRY = _reg(tmp, [{"title": "Delivery chain (SHIPPED 2026-09-04)", "done": False}])
        drift.check_milestone_says_shipped()
        check("an unticked milestone that names a ship date is drift",
              any("contradicts itself" in w for w, _ in drift.DRIFT),
              f"drift={drift.DRIFT}")

        drift.DRIFT.clear(); drift.BLIND.clear(); drift.AGREED.clear()
        drift.REGISTRY = _reg(tmp, [{"title": "Delivery chain (SHIPPED 2026-09-04)", "done": True}])
        drift.check_milestone_says_shipped()
        check("and the same milestone ticked is not",
              not drift.DRIFT, f"drift={drift.DRIFT}")

        # The false positive that would get this switched off.
        drift.DRIFT.clear(); drift.BLIND.clear(); drift.AGREED.clear()
        drift.REGISTRY = _reg(tmp, [
            {"title": "Nothing is shipped without its help content", "done": False},
            {"title": "Decide what SHIPPED should mean for a flagged feature", "done": False}])
        drift.check_milestone_says_shipped()
        check("prose about shipping, with no date, is left alone",
              not drift.DRIFT, f"drift={drift.DRIFT}")

        drift.DRIFT.clear(); drift.BLIND.clear(); drift.AGREED.clear()
        drift.REGISTRY = Path(tmp) / "does-not-exist.json"
        drift.check_milestone_says_shipped()
        check("an unreadable registry is could-not-check, not agreement",
              drift.BLIND and not drift.AGREED, f"blind={drift.BLIND} agreed={drift.AGREED}")
finally:
    drift.REGISTRY = _was_reg
    drift.DRIFT.clear(); drift.BLIND.clear(); drift.AGREED.clear()


print(f"\n  {len(fails)} failed" if fails else "\n  all good")
sys.exit(1 if fails else 0)
