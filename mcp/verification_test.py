#!/usr/bin/env python3
"""A request is not done until every criterion is verified — including the ones only Brad can see.

    python3 mcp/verification_test.py

Brad, 2026-09-04: "a request isn't marked as closed until everything is verified, even if I'm one of
the holdups." So the interesting cases are the ones that must NOT close a job: Moses claiming an
admin-gated row, a note with no evidence in it, and a job with anything still open reading as done.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verification as V  # noqa: E402

fails: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✓' if cond else '✗'} {label}" + (f" — {detail}" if not cond and detail else ""))
    if not cond:
        fails.append(label)


def make(tmp: Path, rows):
    job = tmp / "20260904-000000-1"
    job.mkdir(parents=True, exist_ok=True)
    (job / "task.txt").write_text("a task\n", encoding="utf-8")
    (job / "status").write_text("awaiting-verification\n", encoding="utf-8")
    (job / "verification.json").write_text(json.dumps(
        {"deploy": "live", "opened": "2026-09-04T00:00:00", "criteria": rows}, indent=2),
        encoding="utf-8")
    V.JOBS = tmp
    return job.name


MOSES = {"criterion": "the /pricing page shows the new plan", "checkable_by": "moses",
         "status": "unverified", "by": None, "at": None, "note": None}
ADMIN = {"criterion": "[admin] the ops header shows the marker", "checkable_by": "brad",
         "status": "unverified", "by": None, "at": None, "note": None}

with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)

    jid = make(tmp, [dict(MOSES), dict(ADMIN)])
    r = V.report()
    check("the sweep names what is open", "[1]" in r and "[2]" in r)
    check("and says which rows are Brad's", "BRAD — admin-gated" in r, r[:200])

    out = V.record(jid, 2, "moses", "I looked at the admin page and saw it")
    check("MOSES CANNOT close an admin-gated row", "only Brad can see it" in out, out)
    check("and it stays unverified after that refusal",
          json.loads((tmp / jid / "verification.json").read_text())["criteria"][1]["status"] == "unverified")

    out = V.record(jid, 1, "moses", "ok")
    check("a note with no evidence in it is refused", "only evidence" in out, out)

    out = V.record(jid, 1, "moses", "fetched /pricing and the new plan row is present")
    check("a real check is recorded", "Recorded [1]" in out, out)
    check("but the job is NOT done while Brad's row is open", "Still open" in out, out)
    check("and its status is still awaiting-verification",
          (tmp / jid / "status").read_text().strip() == "awaiting-verification")

    out = V.record(jid, 2, "brad", "Brad confirmed the marker is in the ops header")
    check("Brad's confirmation closes the last row", "request is done" in out, out)
    check("and only THEN does the job read live",
          (tmp / jid / "status").read_text().strip() == "live")

    out = V.record(jid, 1, "moses", "checking it a second time for no reason")
    check("re-recording a verified row is refused", "already verified" in out, out)

    check("a fully verified job drops out of the open sweep", "20260904" not in V.report())

print(f"\n  {len(fails)} failed" if fails else "\n  all good")
sys.exit(1 if fails else 0)
