"""project_status — one place to see the state of every project, and where the story disagrees.

Moses's goal: *central project status tracking — one place to see the state of every project.*
Brad's choice (2026-08-06): a declared registry, enriched with derived facts, **cross-checked**.

WHY CROSS-CHECKED IS THE WHOLE POINT
A registry alone goes stale the day someone stops updating it, and then it lies confidently — the
exact failure this system has hit repeatedly (a disk alarm that stopped checking, a launcher that
defined duty by its own hardcoded list). Derived-only avoids staleness but can't express intent:
git and transcripts show what happened, never what Brad *meant* to do.

So both, and the DISAGREEMENT is the signal. "The registry says Viatica is blocked on OAuth, but
the last three sessions were about iDrive" is more useful than either half alone, because it is the
question a person would actually ask.

Everything derived is read-only and cheap: git state, the transcript index built the same day, the
todo list, and the memory corpus.
"""

from __future__ import annotations

import sys as _sys, pathlib as _pl  # noqa: E401
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent / "lib"))
import moses_env as _env  # noqa: E402 — whose home, where Moses lives, the operator's settings
import json
import os
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REGISTRY = _env.MEMORY / "projects.json"
MEMORY = _env.MEMORY
# NEXT TO THE INDEXER, NOT NEXT TO THIS FILE. `__file__.parent` looked obviously right and was
# wrong from the day it was written: the index is built and read by the MCP tree, this module lives
# in the agent tree, and the database has never once been where this pointed. Every project reported
# "activity: no transcript index" — a declaration that reads exactly like a finding, on a database
# holding 75 sessions the whole time. The failure was silent because the miss returns a caption
# instead of raising.
DB = Path(os.environ.get("MOSES_TRANSCRIPT_DB") or _env.ROOT / "mcp/transcripts.db")
STALE_DAYS = 14          # focus unmentioned this long ⇒ the registry is probably out of date


def _sh(argv: list[str], cwd: str | None = None) -> str:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=20, cwd=cwd)
        return (p.stdout or "").strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, NotADirectoryError):
        return ""


def _git(repo: str) -> str:
    if not repo or not Path(repo, ".git").exists():
        return ""
    branch = _sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo) or "?"
    dirty = _sh(["git", "status", "--porcelain"], repo)
    ahead = _sh(["git", "rev-list", "--count", "@{u}..HEAD"], repo)
    n = len(dirty.splitlines()) if dirty else 0
    bits = [branch, "clean" if n == 0 else f"{n} uncommitted"]
    if ahead and ahead != "0":
        bits.append(f"{ahead} unpushed")
    return ", ".join(bits)


def _activity(keywords: list[str], repo: str) -> tuple[str, int, list[str]]:
    """Last time a session touched this project, and what it was about.

    Matched two ways because either alone misses: by the session's working directory (precise, but
    Moses work happens from the Viatica checkout too) and by keyword in the message text (catches
    the topic wherever it was discussed).
    """
    if not DB.exists():
        return ("no transcript index", 999, [])
    c = sqlite3.connect(DB)
    try:
        clauses, params = [], []
        if repo:
            clauses.append("s.cwd LIKE ?"); params.append(f"{repo}%")
        if keywords:
            q = " OR ".join(keywords)
            rows = c.execute(
                """SELECT m.ts, s.title FROM msg_fts JOIN messages m ON m.id = msg_fts.rowid
                   JOIN sessions s ON s.session_id = m.session_id
                   WHERE msg_fts MATCH ? ORDER BY m.ts DESC LIMIT 40""", (q,)).fetchall()
        else:
            rows = []
        if repo:
            rows += c.execute(
                f"""SELECT m.ts, s.title FROM messages m JOIN sessions s ON s.session_id=m.session_id
                    WHERE {clauses[0]} ORDER BY m.ts DESC LIMIT 20""", params).fetchall()
    except sqlite3.OperationalError:
        return ("index unreadable", 999, [])
    finally:
        c.close()
    rows = [r for r in rows if r[0]]
    if not rows:
        return ("never", 999, [])
    rows.sort(key=lambda r: r[0], reverse=True)
    last = rows[0][0]
    try:
        days = (datetime.now(timezone.utc) - datetime.fromisoformat(last.replace("Z", "+00:00"))).days
    except ValueError:
        days = 999
    titles = list(dict.fromkeys(t for _, t in rows[:12] if t))[:3]
    return (last[:10], days, titles)


def _focus_seen(focus: str, days_window: int = STALE_DAYS) -> bool:
    """Has anything in the declared focus actually come up lately?"""
    if not DB.exists() or not focus:
        return True
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9]{4,}", focus)
             if w.lower() not in {"phase", "final", "then", "finish", "their", "there"}]
    if not words:
        return True
    c = sqlite3.connect(DB)
    try:
        row = c.execute("""SELECT m.ts FROM msg_fts JOIN messages m ON m.id = msg_fts.rowid
                           WHERE msg_fts MATCH ? ORDER BY m.ts DESC LIMIT 1""",
                        (" OR ".join(words),)).fetchone()
    except sqlite3.OperationalError:
        return True
    finally:
        c.close()
    if not row or not row[0]:
        return False
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(row[0].replace("Z", "+00:00"))).days
    except ValueError:
        return True
    return age <= days_window


def _open_work(p: dict) -> list[str]:
    """A project's own unfinished work.

    This used to keyword-match a flat todo.md against the project's name — a heuristic that only
    existed BECAUSE tasks were not attached to anything. Brad retired that file on 2026-08-28 and
    every task now hangs off its project, so the answer is simply read rather than guessed at. It is
    also no longer possible for a task to be shown against the wrong project, or against none.
    """
    ms = [m.get("title", "") for m in (p.get("milestones") or []) if not m.get("done")]
    ideas = [f"(idea) {i.get('title','')}" for i in (p.get("ideas") or [])]
    return [t for t in ms + ideas if t]


def _memory_files(prefix: str) -> int:
    return len(list(MEMORY.glob(f"{prefix}*.md"))) if prefix else 0



def _target_line(target: str) -> str:
    """The target date, and how far away it is. A date with no distance attached gets skimmed."""
    if not target:
        return ""
    try:
        due = datetime.strptime(target, "%Y-%m-%d").date()
    except ValueError:
        return target
    left = (due - datetime.now().date()).days
    if left < 0:
        return f"{target}  ⚠️  {abs(left)} day(s) OVERDUE"
    if left == 0:
        return f"{target}  — today"
    return f"{target}  — {left} day(s)"


def _milestones(ms: list[dict]) -> list[str]:
    """Progress as a count and a list. A bare percentage hides WHICH thing is unfinished."""
    if not ms:
        return []
    done = sum(1 for m in ms if m.get("done"))
    out = [f"   progress   {done}/{len(ms)} milestone(s)"]
    for m in ms:
        out.append(f"              {'[x]' if m.get('done') else '[ ]'} {m.get('title','?')}")
    return out


def report() -> str:
    if not REGISTRY.is_file():
        return f"No project registry at {REGISTRY}."
    try:
        projects = json.loads(REGISTRY.read_text(encoding="utf-8"))["projects"]
    except (json.JSONDecodeError, KeyError) as e:
        return f"Registry unreadable: {e}"

    # Ordered by RANK — Brad's own order of focus — not by status. The question this answers is
    # "what am I meant to be doing", and that is a decision he makes, not one derived from activity.
    order = {"active": 0, "parked": 1, "dormant": 2, "scrapped": 3}
    projects.sort(key=lambda p: (p.get("rank", 99), order.get(p.get("status", ""), 4), p.get("name", "")))

    out, flags = [], []
    for p in projects:
        last, days, titles = _activity(p.get("keywords", []), p.get("repo", ""))
        rank = p.get("rank", 99)
        rank_s = f"#{rank}" if rank < 90 else "  "
        out.append(f"\n{rank_s} {p['name'].upper()}  —  {p.get('status','?')}"
                   + (f"  [{p['stage']}]" if p.get("stage") else ""))
        if p.get("scope"):
            out.append(f"   scope      {p['scope']}")
        else:
            out.append("   scope      — not defined yet (this is still an idea)")
        tgt = _target_line(p.get("target", ""))
        if tgt:
            out.append(f"   target     {tgt}")
        ms = _milestones(p.get("milestones", []) or [])
        for line in ms:
            out.append(line)
        out.append(f"   phase      {p.get('phase','—')}")
        out.append(f"   focus      {p.get('focus','—')}")
        if p.get("next"):
            out.append(f"   next       {p['next']}")
        for b in p.get("blockers", []) or []:
            out.append(f"   BLOCKED    {b}")
        git = _git(p.get("repo", ""))
        if git:
            out.append(f"   repo       {git}")
        if p.get("deploy"):
            out.append(f"   deploy     {p['deploy']}")
        out.append(f"   activity   last touched {last}" + (f" ({days}d ago)" if days < 999 else ""))
        if titles:
            out.append(f"              recent: {', '.join(titles)}")
        td = _open_work(p)
        for t in td[:4]:
            # Clipped: these run to whole paragraphs (the go-live steps carry their own research
            # inside them) and four of those turn a dashboard into a wall. The full text is on the
            # project itself — `project_status <id>` or the Projects page.
            out.append(f"   open       {t[:110] + '…' if len(t) > 110 else t}")
        if len(td) > 4:
            out.append(f"              (+{len(td) - 4} more on this project)")
        n = _memory_files(p.get("memory_prefix", ""))
        if n:
            out.append(f"   memory     {n} file(s) under {p['memory_prefix']}*")

        # ── the cross-check ──────────────────────────────────────────────────
        if p.get("status") == "active":
            if days > STALE_DAYS and days < 999:
                flags.append(f"{p['name']}: marked active but untouched for {days} days")
            if not _focus_seen(p.get("focus", "")):
                flags.append(f"{p['name']}: declared focus \"{p.get('focus','')}\" has not come up "
                             f"in ~{STALE_DAYS} days — the registry is probably stale")
        # PARKED means "not now, on purpose". Brad parked everything behind Viatica until it ships,
        # so a parked project being worked on is drift — the thing he actually asked to be told about,
        # and the opposite of the staleness check above.
        if p.get("status") == "parked" and days <= 1:
            # "came up", not "was worked on". The instrument counts transcript MENTIONS, and talking
            # about a parked project — deciding to park it, say — is not the same as working on it.
            # Claiming otherwise would make this an accusation the data cannot support, and a check
            # that cries wolf on correct behavior is one that gets switched off.
            flags.append(f"{p['name']}: parked, but came up in conversation "
                         f"{'today' if days == 0 else 'yesterday'} — working on it, or just talking about it?")
        if p.get("status") == "scrapped" and days <= 3:
            flags.append(f"{p['name']}: marked scrapped but was discussed {days} day(s) ago")

    head = [f"PROJECT STATUS — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"{len(projects)} project(s) in {REGISTRY.name}"]
    if flags:
        head.append("")
        head.append("⚠️  DECLARED vs ACTUAL — worth a look:")
        head += [f"   • {f}" for f in flags]
    else:
        head.append("Registry and activity agree.")
    return "\n".join(head + out)


if __name__ == "__main__":
    print(report())
