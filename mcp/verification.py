"""The verification ledger — what a finished job still owes before it counts as done.

A deploy that landed is not a request that is delivered. Zryachiy judges the acceptance criteria
against the DIFF, before anything ships; this is the other half, judged against the RUNNING product.

Brad, 2026-09-04: *"make sure Moses tells me what to verify and a request isn't marked as closed
until everything is verified, even if I'm one of the holdups."*

WHO CAN CHECK WHAT. Moses reaches the public site and the secret-authed API. He cannot see anything
behind a user session — an admin page answers him with a 307 to /login. Giving him an admin session
was considered and refused on 2026-09-04: the line in this estate is that the agent never holds the
credential (Knight has no push rights; nothing here reads the Railway token). So an admin-gated
criterion is Brad's to confirm, it is NAMED as his, and the request stays open until he says he
looked.
"""
from __future__ import annotations

import sys as _sys, pathlib as _pl  # noqa: E401
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent / "agent" / "lib"))
import moses_env as _env  # noqa: E402 — whose home, where Moses lives, the operator's settings
import datetime
import json
from pathlib import Path

JOBS = _env.ROOT / "knight/jobs"


def _ledgers(job_id: str | None):
    dirs = [JOBS / job_id] if job_id else sorted(JOBS.glob("*"), reverse=True)
    for d in dirs:
        f = d / "verification.json"
        if f.is_file():
            try:
                yield d.name, json.loads(f.read_text(encoding="utf-8")), f
            except (OSError, json.JSONDecodeError):
                continue


def report(job_id: str | None = None) -> str:
    out, any_open = [], False
    for jid, data, _ in _ledgers(job_id):
        rows = data.get("criteria", [])
        open_rows = [(i, c) for i, c in enumerate(rows, 1) if c.get("status") == "unverified"]
        if not open_rows and not job_id:
            continue                       # the sweep shows only what is still owed
        any_open = any_open or bool(open_rows)
        task = (JOBS / jid / "task.txt")
        head = task.read_text(encoding="utf-8").splitlines()[0][:70] if task.is_file() else jid
        out.append(f"*{jid}* — {head}")
        out.append(f"   deployed: {data.get('deploy', '?')} · "
                   f"{len(rows) - len(open_rows)}/{len(rows)} verified")
        for i, c in enumerate(rows, 1):
            if c.get("status") == "verified":
                out.append(f"   [{i}] ✓ {c['criterion'][:88]}")
                if c.get("note"):
                    out.append(f"        {c['by']}: {c['note'][:100]}")
            else:
                who = "BRAD — admin-gated" if c.get("checkable_by") == "brad" else "Moses can check this"
                out.append(f"   [{i}] ☐ {c['criterion'][:88]}   ← {who}")
        out.append("")
    if not out:
        return "Nothing is waiting on verification — every finished job's criteria are confirmed."
    if any_open:
        out.append("_A request is not done until every row is ticked. Check the ones marked for you, "
                   "and ask Brad about his rather than assuming._")
    return "\n".join(out)


def record(job_id: str, n: int, by: str, note: str) -> str:
    if by not in ("moses", "brad"):
        return "`by` must be 'moses' (you checked it) or 'brad' (he told you he did)."
    if len(note.strip()) < 8:
        return ("Say what was actually seen — that note is the only evidence anyone will have later. "
                "'the marker line is present in the /admin header', not 'confirmed'.")
    for jid, data, f in _ledgers(job_id):
        rows = data.get("criteria", [])
        if not 1 <= n <= len(rows):
            return f"{jid} has {len(rows)} criteria; there is no [{n}]."
        row = rows[n - 1]
        if row.get("status") == "verified":
            return f"[{n}] was already verified by {row.get('by')} — {row.get('note')}"
        # A criterion only Brad can see must not be closed by Moses claiming to have looked.
        if row.get("checkable_by") == "brad" and by != "brad":
            return (f"[{n}] is admin-gated — only Brad can see it. Ask him to look, then record it "
                    f"with by='brad' once he says he has.")
        row.update(status="verified", by=by, note=note,
                   at=datetime.datetime.now().replace(microsecond=0).isoformat())
        f.write_text(json.dumps(data, indent=2), encoding="utf-8")
        left = [c for c in rows if c.get("status") == "unverified"]
        if left:
            owed = ", ".join(f"[{i}] {c['checkable_by']}" for i, c in enumerate(rows, 1)
                             if c.get("status") == "unverified")
            return f"Recorded [{n}]. Still open on {jid}: {owed}"
        (JOBS / jid / "status").write_text("live\n", encoding="utf-8")
        return f"Recorded [{n}]. Every criterion on {jid} is verified — the request is done."
    return f"No verification ledger for {job_id!r}."
