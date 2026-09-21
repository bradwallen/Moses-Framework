"""knight_notify — the conversation that starts a Knight job hears how it ended.

Brad, 2026-09-11, in #the_4_horsemen: "Ping me here when Knight is done." Moses answered "I'll ping
you here when it lands" — twice — and never did, because nothing could. Knight reports to
#viatica-dev and nowhere else, and Moses only speaks when spoken to. The promise was prose with no
mechanism behind it, so the build finished (and was wrongly blocked) with nobody in that channel told.

WHY THE LISTENER RECORDS IT, NOT THE MODEL OR THE TOOL
The model reaches `knight_start` through the long-running MCP server over HTTP, which never learns
what channel the request came from — and asking the model to pass the channel along would be one
more instruction it can forget. The listener is the only layer that knows both halves: which
conversation this turn belongs to, and (by looking at the job record before and after the turn)
whether the turn started a job. So it writes `notify.json` into the job, and bin/knight-run posts
the finished headline there, beside the full report in #viatica-dev.

A job that has already ENDED when the turn finishes is left alone: its report has been posted and
nothing would read the file.
"""

import sys as _sys, pathlib as _pl  # noqa: E401
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent / "lib"))
import moses_env as _env  # noqa: E402 — whose home, where Moses lives, the operator's settings
import json
import os
from pathlib import Path

JOBS = Path(os.environ.get("KNIGHT_JOBS", str(_env.ROOT / "knight/jobs")))
# Zryachiy's explorations report back the same way (knight/bin/zryachiy). They have their own
# directory, so they never count against Knight's daily ceiling or appear in his history.
ZRYACHIY_JOBS = Path(os.environ.get("ZRYACHIY_JOBS", str(_env.ROOT / "zryachiy/jobs")))


def _dirs() -> list[Path]:
    # Read at call time, so a test can point either at a temporary directory.
    return [JOBS, ZRYACHIY_JOBS]


def snapshot() -> set[str]:
    """Every job directory that exists right now, as a full path. Unreadable means none, never an exception."""
    found: set[str] = set()
    for base in _dirs():
        try:
            found |= {str(p) for p in base.iterdir() if p.is_dir()}
        except OSError:
            pass
    return found


def claim(before: set[str], *, channel: str, thread_ts: str = "", user: str = "") -> list[str]:
    """Tie every job created since `before` to this conversation. Returns the ids claimed."""
    claimed = []
    for path in sorted(snapshot() - before):
        d = Path(path)
        jid = d.name
        if (d / "ended").exists() or (d / "notify.json").exists():
            continue
        tmp = d / ".notify.json.tmp"
        try:
            tmp.write_text(json.dumps({"channel": channel, "thread_ts": thread_ts or "",
                                       "user": user or ""}), encoding="utf-8")
            # Renamed into place so the runner can never read half a file.
            tmp.replace(d / "notify.json")
        except OSError:
            continue
        claimed.append(jid)
    return claimed
