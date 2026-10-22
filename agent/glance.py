#!/usr/bin/env python3
"""glance — a project is DONE when it is live, its checks pass, and Brad has looked at it once (#5).

    glance.py request <target> <url> <run-dir>     knight-next, when a run reports the scope covered

Brad, 2026-10-17, on the dev system: there was no clear "done". A run of jobs ended with "Knight reports
the scope is covered", the project stayed `building` in the tracker, and nothing said whether anyone
had looked at the thing. Now the end of a run is three facts and one person:

  1. the scope is covered (Knight's SCOPE COMPLETE),
  2. the project's checks pass on its main branch (`knight provision`, run by knight-next), and
  3. it answers at its live address, when it has one,

and then Brad's one look. knight-next checks 1-3 and, only when all hold, asks for the look on the
run's status card and records the request here. Brad's "looks good" in that conversation (or a ✅ on
the card) is matched by the LISTENER's code, never the model, and moves the project to `shipped` —
which also stops every later run, since knight-next builds only a project whose stage is `building`.
Anything else he says is ordinary conversation: the request stays open until he says it looks good,
or a week passes.

WHERE A "LOOKS GOOD" COUNTS (review, 2026-10-18): only as a reply in the card's own thread (or the
thread the card was posted into) — never a top-level message, where "looks good" answers anything. And
only the words that mean it: not "done" or "ship it", which answer other questions. Anywhere in the
card's channel, `mark <project> done` names the project and so cannot close the wrong one.

The store is one JSON file in the shared state directory, like build_spec's.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import moses_env as _env  # noqa: E402

STORE = Path(_env.STATE) / "glances.json"
TTL = 7 * 24 * 3600
MCP_DIR = os.environ.get("MOSES_MCP_DIR", str(_env.ROOT / "mcp"))
LOOKS_GOOD = re.compile(r"^\s*(?:<@\w+>\s*)?(?:moses[\s,:]*)?(looks good|looks great|lgtm|"
                        r"it'?s good|all good|good to go|mark it done)\s*[.!]*\s*$", re.I)
MARK = re.compile(r"^\s*(?:<@\w+>\s*)?(?:moses[\s,:]*)?mark\s+`?([\w.-]+)`?\s+(?:as\s+)?done\s*[.!]*\s*$", re.I)
CARD = os.environ.get("KNIGHT_CARD", str(_env.ROOT / "knight" / "lib" / "card.py"))
REACTIONS = {"white_check_mark", "heavy_check_mark", "ballot_box_with_check"}


class _Locked:
    def __enter__(self):
        STORE.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(STORE.with_name(STORE.name + ".lock"), "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        self.f.close()


def _load() -> dict:
    try:
        return json.loads(STORE.read_text())
    except (OSError, ValueError):
        return {"glances": []}


def _save(d: dict) -> None:
    tmp = STORE.with_name(f"{STORE.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d, indent=1))
    os.replace(tmp, STORE)


def request(target: str, url: str, run_dir: str) -> str:
    """Ask for Brad's look where the run's card is. Returns what happened, for knight-next's log."""
    rd = Path(run_dir)
    try:
        card = json.loads((rd / "card.json").read_text())
    except (OSError, ValueError):
        card = {}
    try:
        note = json.loads((rd / "notify.json").read_text())
    except (OSError, ValueError):
        note = {}
    channel = card.get("channel") or note.get("channel") or ""
    if not channel:
        return "no card to ask on"
    with _Locked():
        d = _load()
        now = time.time()
        d["glances"] = [g for g in d["glances"] if now - g.get("at", 0) < TTL and g.get("target") != target]
        d["glances"].append({"target": target, "url": url, "channel": channel,
                             "thread_ts": card.get("thread") or note.get("thread_ts") or "",
                             "card_ts": card.get("ts", ""), "user": note.get("user", ""), "at": now,
                             "run_dir": str(rd)})
        _save(d)
    return f"asked in {channel}"


def pending(channel: str, thread_ts: str = "") -> dict | None:
    """The open request answered by a reply in this thread: the card's own thread, or the thread the
    card was posted into. A top-level message never matches."""
    if not thread_ts:
        return None
    now = time.time()
    for g in reversed(_load()["glances"]):
        if g.get("channel") == channel and now - g.get("at", 0) < TTL \
                and thread_ts in [t for t in (g.get("thread_ts"), g.get("card_ts")) if t]:
            return g
    return None


def by_mark(text: str, channel: str) -> dict | None:
    """ "mark gis done" anywhere in the card's channel: the project is named, so it cannot be the wrong one."""
    m = MARK.match(text or "")
    if not m:
        return None
    now = time.time()
    return next((g for g in _load()["glances"] if g.get("channel") == channel and g.get("target") == m.group(1)
                 and now - g.get("at", 0) < TTL), None)


def by_message(ts: str) -> dict | None:
    now = time.time()
    return next((g for g in _load()["glances"] if ts and g.get("card_ts") == ts and now - g.get("at", 0) < TTL), None)


def is_looks_good(text: str) -> bool:
    return bool(LOOKS_GOOD.match(text or ""))


def close(g: dict) -> str:
    """Brad looked. The project is shipped in the tracker; the request is gone."""
    if MCP_DIR not in sys.path:
        sys.path.insert(0, MCP_DIR)
    import projects
    with _Locked():
        d = _load()
        mine = next((x for x in d["glances"] if x.get("target") == g["target"]), None)
        if not mine:
            return ""
        try:
            # EXACT id, like knight-next's project_field: update()'s own lookup accepts a piece of the
            # name, and "done" must never land on a different project.
            if not any(p.get("id") == g["target"] for p in projects.load()["projects"]):
                raise ValueError(f"no project with the id '{g['target']}' in the tracker")
            projects.update(g["target"], "stage", "shipped")
            projects.update(g["target"], "next", "")
        except Exception as e:                                      # noqa: BLE001 — the request stays open
            return f"⚠️ I couldn't mark it done in the tracker, so it is still waiting: {str(e).splitlines()[0]}"
        d["glances"] = [x for x in d["glances"] if x is not mine]
        _save(d)
    # The run's card says it too, so the last thing on it is not "have a look".
    if mine.get("run_dir"):
        try:
            import subprocess
            subprocess.run([CARD, mine["run_dir"], "done", "Brad looked: marked shipped in the tracker."],
                           capture_output=True, timeout=30, env={**os.environ})
        except Exception:                                           # noqa: BLE001
            pass
    where = f" at {g['url']}" if g.get("url") else ""
    return (f"🏁 Marked *{g['target']}* done: live{where}, its checks pass, and you've looked. Knight stops "
            "building it; tell me when there's more to add.")


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "request":
        print(request(sys.argv[2], sys.argv[3], sys.argv[4]))
        sys.exit(0)
    print(__doc__.splitlines()[2].strip(), file=sys.stderr)
    sys.exit(64)
