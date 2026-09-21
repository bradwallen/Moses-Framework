"""moses_pages — the content of Moses's own site, read from the live sources.

NOTHING HERE IS A COPY. The commandments come from the same memory file the prompt hook injects; the
architecture diagrams come from the same document the architecture check validates; the roster comes
from the file that decides who owes a report; the projects come from the registry Moses edits. A page
that restates any of them would be a second copy, and this system has been bitten by a second copy
three times in one day — a status module that had drifted, a CLI installed from a stale source, and
an inert cross-check nobody could see was inert.

So the rule is: if it is shown here, it is read at request time from the file that owns it.
"""

from __future__ import annotations

import sys as _sys, pathlib as _pl  # noqa: E401
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent / "agent" / "lib"))
import moses_env as _env  # noqa: E402 — whose home, where Moses lives, the operator's settings

import os
import html
import json
import re
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

MEMORY = _env.MEMORY
COMMANDMENTS_SRC = MEMORY / "project_moses.md"
ARCHITECTURE_SRC = _env.ROOT / "agent/docs/ARCHITECTURE.md"
ROSTER_SRC = Path(os.environ.get("MOSES_ROSTER", str(Path.home() / ".config" / "moses" / "roster.json")))
ROSTER_FALLBACK = _env.ROOT / "agent/roster.json"
TRANSCRIPTS_DB = _env.ROOT / "mcp/transcripts.db"


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def inline(md: str) -> str:
    """The little markdown that actually appears in these files: bold, code, and [[memory links]]."""
    s = esc(md)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    # [[wiki-links]] point at memory files, which are not served — shown as what they are.
    s = re.sub(r"\[\[([^\]]+)\]\]", r'<span class="ref">\1</span>', s)
    return s


# ── The commandments ─────────────────────────────────────────────────────────

def commandments() -> list[dict]:
    """The ten, parsed from the SAME section the prompt hook injects on every turn."""
    if not COMMANDMENTS_SRC.is_file():
        return []
    grabbing, lines = False, []
    for ln in COMMANDMENTS_SRC.read_text(encoding="utf-8").splitlines():
        if ln.startswith("## THE COMMANDMENTS"):
            grabbing = True
            continue
        if grabbing and ln.startswith("## "):
            break
        if grabbing:
            lines.append(ln)

    # Bullets are kept as bullets. Joining them into the prose turned commandments 3, 7, 9 and 10
    # into run-on paragraphs — "Four faces of one rule: - Debugging: ... - Code: ..." — which is
    # exactly the structure that makes them readable in the first place.
    out, cur = [], None
    for ln in lines:
        m = re.match(r"^(\d+)\.\s+\*\*(.+?)\*\*\s*(.*)$", ln)
        if m:
            if cur:
                out.append(cur)
            cur = {"n": int(m.group(1)), "title": m.group(2), "body": [m.group(3).strip()], "bullets": []}
            continue
        if cur is None:
            continue
        stripped = ln.strip()
        if stripped.startswith("- "):
            cur["bullets"].append([stripped[2:]])
        elif cur["bullets"] and ln.startswith("     "):
            # A continuation line of the bullet above — indented further than the bullet marker.
            cur["bullets"][-1].append(stripped)
        elif stripped:
            cur["body"].append(stripped)
    if cur:
        out.append(cur)
    for c in out:
        c["body"] = " ".join(x for x in c["body"] if x)
        c["bullets"] = [" ".join(b) for b in c["bullets"]]
    return out


# ── The architecture document ────────────────────────────────────────────────

def architecture_sections() -> list[dict]:
    """Headings and fenced ASCII blocks, in order. The diagrams are already text — that is the point
    of commandment 5 — so they need rendering, not redrawing."""
    if not ARCHITECTURE_SRC.is_file():
        return []
    secs, cur, fence, buf = [], None, False, []
    for ln in ARCHITECTURE_SRC.read_text(encoding="utf-8").splitlines():
        if ln.startswith("```"):
            if fence:
                cur and cur["blocks"].append("\n".join(buf))
                buf, fence = [], False
            else:
                fence = True
            continue
        if fence:
            buf.append(ln)
            continue
        if re.match(r"^#{1,3} ", ln):
            if cur:
                secs.append(cur)
            cur = {"title": re.sub(r"^#+\s*", "", ln), "level": len(ln) - len(ln.lstrip("#")),
                   "prose": [], "blocks": [], "tables": []}
        elif cur is not None and ln.strip():
            row = ln.strip()
            # Markdown tables carry real content here — which Access app answers which question, what
            # each persona kind means — but joined into a paragraph they read as a row of pipes. Kept
            # apart and rendered as tables instead of dumped into the prose.
            if row.startswith("|") and row.endswith("|"):
                cells = [c.strip() for c in row.strip("|").split("|")]
                if not all(re.fullmatch(r"[-—:\s]*", c) for c in cells):   # skip the ---|--- rule
                    cur["tables"].append(cells)
            else:
                cur["prose"].append(row)
    if cur:
        secs.append(cur)
    return secs


# ── Who is accountable for what ──────────────────────────────────────────────

def roster() -> list[dict]:
    src = ROSTER_SRC if ROSTER_SRC.is_file() else ROSTER_FALLBACK
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for p in data.get("personas", []):
        out.append({
            "name": p.get("name", p.get("id", "?")),
            "emoji": p.get("emoji", "").strip(":"),
            "kind": p.get("kind", ""),
            "title": p.get("title", ""),
            "aoe": (p.get("aoe") or {}).get("owns") if isinstance(p.get("aoe"), dict) else p.get("aoe", ""),
            "cadence": p.get("cadence_hours"),
        })
    return out


# ── Live numbers, gathered defensively ───────────────────────────────────────

def vitals() -> list[tuple[str, str]]:
    """A few facts about the running system. Every one is wrapped: this is decoration on a page, and
    a page that 500s because a count failed is worse than a page with a blank in it (commandment 6).
    """
    out: list[tuple[str, str]] = []

    try:
        out.append(("memory files", str(len(list(MEMORY.glob("*.md"))))))
    except OSError:
        pass

    try:
        if TRANSCRIPTS_DB.is_file():
            c = sqlite3.connect(f"file:{TRANSCRIPTS_DB}?mode=ro", uri=True)
            try:
                msgs = c.execute("SELECT count(*) FROM messages").fetchone()[0]
                out.append(("messages indexed", f"{msgs:,}"))
                last = c.execute("SELECT max(ts) FROM messages").fetchone()[0]
                if last:
                    out.append(("last session", str(last)[:10]))
            finally:
                c.close()
    except sqlite3.Error:
        pass

    try:
        import projects
        ps = projects.load()["projects"]
        live = [p for p in ps if p.get("rank", 99) < projects.ARCHIVE_RANK]
        out.append(("projects tracked", str(len(live))))
    except Exception:
        pass

    try:
        v = subprocess.run(["cloudflared", "--version"], capture_output=True, text=True, timeout=5)
        if v.returncode == 0:
            out.append(("cloudflared", v.stdout.split()[2]))
    except (OSError, subprocess.SubprocessError, IndexError):
        pass

    try:
        up = subprocess.run(["uptime", "-p"], capture_output=True, text=True, timeout=5)
        if up.returncode == 0:
            out.append(("reserve up", up.stdout.strip().replace("up ", "")))
    except (OSError, subprocess.SubprocessError):
        pass

    return out
