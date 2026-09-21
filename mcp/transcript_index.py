"""transcript_index — make every Claude session searchable, forever, for free.

Moses's flagship goal: *capture EVERY transcript, in every context, into an easily indexable store
… it must power status tracking of all projects and make standing up / resuming projects
frictionless.* The nightly tar already captures them; this is the indexed half.

THREE DESIGN DECISIONS, AND WHY

1. **FTS5, not embeddings.** The vision says "vector DB", but a vector DB needs an embedding model:
   the hosted route is Anthropic API spend, which Brad ruled out, and a local model means ~2 GB of
   torch on Reserve for a marginal gain. SQLite FTS5 ships inside Python, indexes 22 MB in under a
   second, and — with the porter tokenizer — matches "backup" to "backups", which is exactly the
   phrasing-mismatch failure that made Moses claim he'd never heard of Viatica. The semantic work
   happens in Brad's Claude app, which reads the results and reasons over them. Embeddings can be
   added later as a second index; nothing here forecloses it.

2. **Secrets are redacted ON INGEST, never at query time.** Transcripts contain pasted tokens, .env
   contents and API keys — Brad pasted a Cloudflare service-token id into a session on 2026-08-06.
   The index is queryable from his phone, so anything stored is reachable. Redacting on the way in
   means the database never holds the secret at all; a query-time filter would leave it on disk and
   one bug away from exposure. Same reasoning as building the agent env from nothing rather than
   filtering it.

3. **Thinking blocks and tool results are skipped.** Not squeamishness — volume and risk. Tool
   results are where whole .env files and command output land, and they dwarf the actual
   conversation. What Brad searches for is what was said and decided, which lives in text blocks.
   Tool *invocations* are indexed (short, and "when did we run X" is a real question); their output
   is not.
"""

from __future__ import annotations

import sys as _sys, pathlib as _pl  # noqa: E401
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent / "agent" / "lib"))
import moses_env as _env  # noqa: E402 — whose home, where Moses lives, the operator's settings
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

# Live sessions plus anything recovered from the laptop archives by backfill-transcripts.sh.
# Both are scanned; the staging dir holds history from before Reserve became the dev box.
PROJECTS = _env.HOME / ".claude/projects"
ARCHIVE = _env.HOME / ".claude/transcripts-archive"
SOURCES = [PROJECTS, ARCHIVE]
DB = Path(__file__).resolve().parent / "transcripts.db"

# ── Redaction ────────────────────────────────────────────────────────────────
# Ordered most-specific first. Each pattern keeps a short prefix so a human can still tell WHICH
# kind of credential appeared without the value being recoverable.
_SECRETS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}"),            "sk-ant-«REDACTED»"),
    (re.compile(r"\bxoxb-[A-Za-z0-9\-]{8,}"),               "xoxb-«REDACTED»"),
    (re.compile(r"\bxapp-[A-Za-z0-9\-]{8,}"),               "xapp-«REDACTED»"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),           "ghp_«REDACTED»"),
    (re.compile(r"\bwhsec_[A-Za-z0-9]{16,}"),               "whsec_«REDACTED»"),
    (re.compile(r"\b[a-f0-9]{32}\.access\b"),               "«REDACTED».access"),   # CF service token id
    (re.compile(r"(?i)\b(client[_-]?secret|api[_-]?key|password|passwd|secret|token)"
                r"\s*[:=]\s*[\"']?([A-Za-z0-9_\-./+=]{12,})"), r"\1=«REDACTED»"),
    (re.compile(r"\bpostgres(?:ql)?://[^\s\"']+"),          "postgres://«REDACTED»"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
                                                            "«REDACTED PRIVATE KEY»"),
]


def redact(text: str) -> str:
    for pat, repl in _SECRETS:
        text = pat.sub(repl, text)
    return text


# ── Extraction ───────────────────────────────────────────────────────────────
@dataclass
class Msg:
    seq: int
    ts: str
    role: str
    text: str


def _blocks_text(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    out = []
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and b.get("text"):
            out.append(b["text"])
        elif b.get("type") == "tool_use":
            # The invocation, not the output: short, and "when did we run X" is a real question.
            inp = b.get("input") or {}
            bits = [str(inp.get(k)) for k in ("command", "file_path", "path", "query", "url", "pattern")
                    if inp.get(k)]
            out.append(f"[tool:{b.get('name','?')}] " + " ".join(bits)[:400])
    return out


def parse(path: Path) -> tuple[dict, list[Msg]]:
    meta = {"session_id": path.stem, "project": path.parent.name, "title": "",
            "started": "", "ended": "", "cwd": "", "branch": ""}
    msgs: list[Msg] = []
    seq = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = d.get("type")
        # A custom title beats an AI-generated one; both beat nothing.
        if t == "custom-title" and d.get("customTitle"):
            meta["title"] = d["customTitle"]
        elif t == "ai-title" and d.get("aiTitle") and not meta["title"]:
            meta["title"] = d["aiTitle"]
        elif t in ("user", "assistant"):
            m = d.get("message") or {}
            texts = _blocks_text(m.get("content"))
            if not texts:
                continue
            ts = d.get("timestamp", "")
            meta["cwd"] = d.get("cwd", meta["cwd"])
            meta["branch"] = d.get("gitBranch", meta["branch"])
            if ts:
                meta["started"] = meta["started"] or ts
                meta["ended"] = ts
            seq += 1
            msgs.append(Msg(seq, ts, t, redact("\n".join(texts))))
    return meta, msgs


# ── Index ────────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  session_id TEXT PRIMARY KEY, project TEXT, title TEXT, started TEXT, ended TEXT,
  turns INTEGER, cwd TEXT, branch TEXT, path TEXT, mtime REAL, indexed_at TEXT);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, session_id TEXT, seq INTEGER, ts TEXT, role TEXT, text TEXT);
CREATE INDEX IF NOT EXISTS ix_msg_session ON messages(session_id, seq);
-- porter stemming so "backup" finds "backups" — the phrasing mismatch that made an earlier
-- substring search report nothing while the answer sat in the corpus.
CREATE VIRTUAL TABLE IF NOT EXISTS msg_fts USING fts5(
  text, content='messages', content_rowid='id', tokenize='porter unicode61');
"""


def connect(db: Path = DB) -> sqlite3.Connection:
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    return c


def build(db: Path = DB, sources: list[Path] | None = None, force: bool = False) -> str:
    c = connect(db)
    seen, added, skipped = 0, 0, 0
    files = sorted({f for s in (sources or SOURCES) if s.is_dir() for f in s.rglob("*.jsonl")})
    for f in files:
        seen += 1
        mtime = f.stat().st_mtime
        row = c.execute("SELECT mtime FROM sessions WHERE session_id=?", (f.stem,)).fetchone()
        if row and not force and abs(row[0] - mtime) < 1:
            skipped += 1
            continue
        meta, msgs = parse(f)
        # Re-index by replacement, so a session that grew mid-write can never end up duplicated.
        ids = [r[0] for r in c.execute("SELECT id FROM messages WHERE session_id=?", (f.stem,))]
        if ids:
            c.executemany("INSERT INTO msg_fts(msg_fts, rowid, text) VALUES('delete', ?, "
                          "(SELECT text FROM messages WHERE id=?))", [(i, i) for i in ids])
            c.execute("DELETE FROM messages WHERE session_id=?", (f.stem,))
        for m in msgs:
            cur = c.execute("INSERT INTO messages(session_id, seq, ts, role, text) VALUES(?,?,?,?,?)",
                            (f.stem, m.seq, m.ts, m.role, m.text))
            c.execute("INSERT INTO msg_fts(rowid, text) VALUES(?,?)", (cur.lastrowid, m.text))
        c.execute("""INSERT INTO sessions(session_id, project, title, started, ended, turns, cwd,
                     branch, path, mtime, indexed_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'))
                     ON CONFLICT(session_id) DO UPDATE SET title=excluded.title, started=excluded.started,
                     ended=excluded.ended, turns=excluded.turns, cwd=excluded.cwd, branch=excluded.branch,
                     mtime=excluded.mtime, indexed_at=excluded.indexed_at""",
                  (f.stem, meta["project"], meta["title"], meta["started"], meta["ended"],
                   len(msgs), meta["cwd"], meta["branch"], str(f), mtime))
        added += 1
    c.commit()
    n = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    s = c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    c.close()
    return (f"Indexed {added} session(s), skipped {skipped} unchanged, {seen} file(s) seen. "
            f"Index now holds {s} session(s) / {n} message(s).")


# ── Query ────────────────────────────────────────────────────────────────────
def search(query: str, limit: int = 12, db: Path = DB) -> str:
    if not db.exists():
        return "No transcript index yet — run the indexer."
    c = connect(db)
    try:
        rows = c.execute("""
            SELECT s.title, s.project, m.ts, m.role, snippet(msg_fts, 0, '«', '»', ' … ', 18), m.session_id, m.seq
            FROM msg_fts JOIN messages m ON m.id = msg_fts.rowid
            JOIN sessions s ON s.session_id = m.session_id
            WHERE msg_fts MATCH ? ORDER BY rank LIMIT ?""", (query, limit)).fetchall()
    except sqlite3.OperationalError as e:
        return (f"Bad search syntax ({e}). FTS5 accepts words, \"quoted phrases\", AND/OR/NOT, "
                f"and prefix* — but bare punctuation confuses it.")
    finally:
        c.close()
    if not rows:
        return (f"No transcript matches {query!r}. Try fewer or shorter words — the index stems, so "
                f"'backup' finds 'backups', but it cannot guess synonyms.")
    out = [f"{len(rows)} match(es) for {query!r}:"]
    for title, project, ts, role, snip, sid, seq in rows:
        out.append(f"\n[{(ts or '')[:16]}] {role} — {title or '(untitled)'}  ({project})")
        out.append(f"   {' '.join(snip.split())}")
        out.append(f"   session {sid[:8]} #{seq}")
    return "\n".join(out)


def sessions(db: Path = DB) -> str:
    if not db.exists():
        return "No transcript index yet — run the indexer."
    c = connect(db)
    rows = c.execute("""SELECT started, title, project, turns, branch, session_id
                        FROM sessions ORDER BY started DESC""").fetchall()
    c.close()
    if not rows:
        return "Index is empty."
    out = [f"{len(rows)} session(s), newest first:"]
    for started, title, project, turns, branch, sid in rows:
        out.append(f"  {(started or '?')[:10]}  {turns:>4} turns  {title or '(untitled)'}")
        out.append(f"        {project}{('  branch ' + branch) if branch else ''}  · {sid[:8]}")
    return "\n".join(out)


def read_session(session_id: str, start: int = 1, count: int = 30, db: Path = DB) -> str:
    if not db.exists():
        return "No transcript index yet — run the indexer."
    c = connect(db)
    meta = c.execute("SELECT title, project, started FROM sessions WHERE session_id LIKE ?",
                     (session_id + "%",)).fetchone()
    if not meta:
        c.close()
        return f"No session starting {session_id!r}. Use list_sessions."
    rows = c.execute("""SELECT m.seq, m.role, m.text FROM messages m JOIN sessions s
                        ON s.session_id=m.session_id WHERE s.session_id LIKE ?
                        AND m.seq >= ? ORDER BY m.seq LIMIT ?""",
                     (session_id + "%", start, count)).fetchall()
    c.close()
    out = [f"{meta[0] or '(untitled)'} — {meta[1]}, started {(meta[2] or '')[:16]}"]
    for seq, role, text in rows:
        body = text if len(text) < 1200 else text[:1200] + " …[truncated]"
        out.append(f"\n#{seq} {role}:\n{body}")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "search":
        print(search(" ".join(sys.argv[2:])))
    elif len(sys.argv) > 1 and sys.argv[1] == "sessions":
        print(sessions())
    else:
        print(build(force="--force" in sys.argv))
