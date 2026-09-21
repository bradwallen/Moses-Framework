"""semantic_index — find ideas in the transcripts by meaning, not by wording.

Brad's reasoning for wanting a vector store (2026-08-06): it *"most closely matches how people
think and it would allow you to help me find ideas buried in what we talked about."* That is the
requirement, and FTS5 genuinely cannot meet it. Keyword search answers "where did I say this
word"; it scores "make backups safer" against a transcript that says "guard against unmounted
drives" at exactly zero, because they share no terms. Those are the same idea. Finding them is the
whole point of the feature.

WHY THIS SITS BESIDE FTS5 RATHER THAN REPLACING IT
They fail in opposite directions and neither is the better tool. Exact search is the right answer
for an error string, a session id, a port number, `sk-ant-` — things where a near-miss is a wrong
answer. Semantic search is the right answer for a half-remembered idea. So both indexes live in the
same database over the same messages, and `search()` fuses their rankings.

WHY NO VECTOR DATABASE
The corpus is a few thousand chunks. A brute-force matmul over that is sub-millisecond and exact —
an ANN index would add a service, a second copy of the data and approximation error to solve a
problem that does not exist yet. See CEILING below for where that stops being true.

WHY fastembed
It runs onnxruntime, not torch: ~130 MB of model, no CUDA stack, and critically **no API calls**.
Brad's constraint is standing and mechanical — *"I don't want Moses to chew my API credits"* —
so embedding had to be local or not exist. It is local. This module never opens a network socket
after the one-time model download.

REDACTION IS INHERITED, NOT REDONE
Embeddings are built from `messages.text`, which `transcript_index.redact()` already scrubbed on
the way in. Secrets never reach this file. Do not add a path that reads raw .jsonl.
"""

from __future__ import annotations

import hashlib
import sqlite3
import struct
from pathlib import Path

DB = Path(__file__).resolve().parent / "transcripts.db"
MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384

# Chunking. A single message is the wrong unit in both directions: "Yes, go ahead and build it"
# carries no meaning alone, and a 6 KB design message holds five separate ideas that average into
# mush. So group consecutive messages to roughly a paragraph's worth of context, and overlap by one
# message so an idea that straddles a boundary is still wholly inside some chunk.
TARGET_CHARS = 900
MAX_CHARS = 2000
MIN_CHARS = 120

# Where brute force stops being the right call. At ~40k chunks the scan is still only ~60 ms, but
# the vectors no longer fit comfortably in memory alongside everything else on Reserve. Past this,
# reach for sqlite-vec — do not quietly let it degrade.
CEILING = 40_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY,
    session_id  TEXT NOT NULL,
    start_seq   INTEGER NOT NULL,
    end_seq     INTEGER NOT NULL,
    ts          TEXT,
    text        TEXT NOT NULL,
    hash        TEXT NOT NULL UNIQUE,
    vec         BLOB
);
CREATE INDEX IF NOT EXISTS chunks_session ON chunks(session_id);
"""


class EmbeddingUnavailable(RuntimeError):
    """fastembed is missing or the model will not load — callers fall back to exact search."""


_model = None


def _get_model():
    """Loaded lazily and once. Importing fastembed costs ~1.5s, which should not be paid by an
    MCP server start that may never run a semantic query."""
    global _model
    if _model is None:
        try:
            from fastembed import TextEmbedding
        except ImportError as e:
            raise EmbeddingUnavailable(f"fastembed not installed: {e}") from e
        try:
            _model = TextEmbedding(MODEL)
        except Exception as e:                     # noqa: BLE001 — model download/onnx failures vary
            raise EmbeddingUnavailable(f"could not load {MODEL}: {e}") from e
    return _model


def _pack(vec) -> bytes:
    return struct.pack(f"<{DIM}f", *vec)


# Markers of harness-injected content. Prefixes are checked at the start; tags anywhere, since the
# CLI wraps them around real text. Kept as data so a new Claude Code release that adds a wrapper is
# a one-line change rather than a re-think.
_INJECTED_PREFIXES = (
    "Base directory for this skill:",
    "This session is being continued from a previous",
    "Caveat: The messages below were generated",
)
_INJECTED_TAGS = (
    "<system-reminder>", "<local-command-caveat>", "<local-command-stdout>",
    "<command-name>", "<ide_selection>", "A plan file exists from plan mode",
)


def _is_injected(text: str) -> bool:
    return text.startswith(_INJECTED_PREFIXES) or any(t in text for t in _INJECTED_TAGS)


def _chunks_for_session(rows: list[tuple]) -> list[tuple[int, int, str, str]]:
    """(start_seq, end_seq, ts, text) windows over one session's messages."""
    out, buf, size = [], [], 0
    for seq, ts, role, text in rows:
        text = (text or "").strip()
        if not text:
            continue
        # Tool invocations are 59% of the corpus and actively harm this index. A bash line is
        # mostly paths and punctuation, so its embedding lands weakly near everything and it
        # crowds out real discussion — the first hybrid query returned an `ls -la` as the top
        # answer to "why did we decide not to pay for something". They stay in FTS5, where
        # "did we ever run this command" is a fair question; they do not belong in a search for
        # ideas. This is the ONE place the two indexes deliberately cover different material.
        if text.startswith("[tool:"):
            continue
        # Harness-injected text is not Brad thinking — it is the CLI talking to itself: skill
        # preambles, session-continuation notices, /model command echoes, system reminders. There
        # are only ~48 of them, but they repeat VERBATIM across sessions, so they pair with each
        # other at 0.92 and swamped the first connections report. The genericness filter cannot
        # catch these: it finds text close to everything, and these are close to their own copies.
        # Exact search keeps them; nothing here is a thought worth rediscovering.
        if _is_injected(text):
            continue
        # A single message larger than the cap becomes its own chunk rather than swallowing
        # its neighbors; truncation keeps one runaway paste from dominating the vector space.
        piece = f"{role}: {text[:MAX_CHARS]}"
        buf.append((seq, ts, piece))
        size += len(piece)
        if size >= TARGET_CHARS:
            out.append(buf)
            buf, size = buf[-1:], len(buf[-1][2])   # one-message overlap
    if buf and (not out or buf is not out[-1]):
        joined = sum(len(p[2]) for p in buf)
        if joined >= MIN_CHARS or not out:
            out.append(buf)

    packed = []
    for group in out:
        body = "\n".join(p[2] for p in group)
        if len(body) < MIN_CHARS:
            continue
        packed.append((group[0][0], group[-1][0], group[0][1] or "", body))
    return packed


def build(db: Path = DB, batch: int = 64) -> str:
    """Chunk and embed anything not already embedded. Safe to re-run; it is incremental by hash."""
    if not db.exists():
        return "No transcript database yet — run transcript_index.py first."
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)

    have = {h for (h,) in c.execute("SELECT hash FROM chunks")}
    pending: list[tuple] = []
    for (sid,) in c.execute("SELECT session_id FROM sessions ORDER BY started"):
        rows = c.execute(
            "SELECT seq, ts, role, text FROM messages WHERE session_id=? ORDER BY seq", (sid,)
        ).fetchall()
        for start, end, ts, body in _chunks_for_session(rows):
            h = hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]
            if h not in have:
                have.add(h)
                pending.append((sid, start, end, ts, body, h))

    if not pending:
        n = c.execute("SELECT COUNT(*) FROM chunks WHERE vec IS NOT NULL").fetchone()[0]
        c.close()
        return f"Semantic index up to date — {n} chunk(s), nothing new."

    try:
        model = _get_model()
    except EmbeddingUnavailable as e:
        c.close()
        # Loud, not silent: exact search still works, but nobody should believe semantic is running.
        return f"Semantic index NOT built — {e}\nExact search is unaffected."

    written = 0
    for i in range(0, len(pending), batch):
        block = pending[i:i + batch]
        vecs = list(model.embed([b[4] for b in block]))
        c.executemany(
            "INSERT OR IGNORE INTO chunks(session_id,start_seq,end_seq,ts,text,hash,vec)"
            " VALUES (?,?,?,?,?,?,?)",
            [(b[0], b[1], b[2], b[3], b[4], b[5], _pack(v)) for b, v in zip(block, vecs)],
        )
        c.commit()
        written += len(block)

    total = c.execute("SELECT COUNT(*) FROM chunks WHERE vec IS NOT NULL").fetchone()[0]
    c.close()
    warn = ""
    if total > CEILING:
        warn = (f"\n⚠️  {total} chunks is past the {CEILING} brute-force ceiling — "
                f"time to move to sqlite-vec.")
    return f"Embedded {written} new chunk(s); {total} total.{warn}"


def _scan(qvec, db: Path, limit: int) -> tuple[list, list]:
    """Brute-force cosine over every stored vector. Exact, and fast at this corpus size."""
    import numpy as np
    c = sqlite3.connect(db)
    try:
        rows = c.execute(
            "SELECT c.id, c.session_id, c.ts, c.text, c.vec, s.title FROM chunks c"
            " LEFT JOIN sessions s ON s.session_id = c.session_id WHERE c.vec IS NOT NULL"
        ).fetchall()
    except sqlite3.OperationalError:
        return [], []
    finally:
        c.close()
    if not rows:
        return [], []
    mat = np.frombuffer(b"".join(r[4] for r in rows), dtype="<f4").reshape(len(rows), DIM)
    mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    q = qvec / (np.linalg.norm(qvec) + 1e-9)
    scores = mat @ q
    top = np.argsort(-scores)[:limit]
    return [(float(scores[i]), rows[i][0], rows[i][1], rows[i][2], rows[i][3]) for i in top], rows


def semantic(query: str, limit: int = 8, db: Path = DB) -> str:
    try:
        model = _get_model()
    except EmbeddingUnavailable as e:
        return f"Semantic search unavailable — {e}\nTry exact search instead."
    import numpy as np
    qvec = np.array(list(model.query_embed([query]))[0])
    hits, rows = _scan(qvec, db, limit)
    if not hits:
        return "Nothing embedded yet — run `semantic_index.py build`."
    by_id = {r[0]: r for r in rows}
    out = [f"Semantic matches for {query!r}:"]
    for score, cid, sid, ts, text in hits:
        title = by_id[cid][5] or sid[:8]
        out.append(f"\n[{score:.2f}] {title} — {(ts or '')[:10]}  (session {sid[:8]})")
        out.append("   " + " ".join(text.split())[:400])
    out.append("\nRanked by meaning, not wording — a high score with no shared words is expected.")
    return "\n".join(out)


def search(query: str, limit: int = 8, db: Path = DB) -> str:
    """Hybrid: fuse exact and semantic rankings.

    Reciprocal-rank fusion rather than blending scores, because BM25 and cosine are not on a
    comparable scale and any weighting between them would be a number I made up. RRF only needs
    each engine's ORDER, which both produce honestly. A result both engines like outranks one that
    only a single engine loves — which is the behavior you want when you cannot say in advance
    whether a query is a phrase you remember or an idea you half-remember.
    """
    import numpy as np
    K = 60          # RRF damping; the standard value — keeps rank 1 from dwarfing ranks 2-5

    ranks: dict[int, float] = {}
    meta: dict[int, tuple] = {}

    c = sqlite3.connect(db)
    try:
        fts = c.execute(
            """SELECT c.id, c.session_id, c.ts, c.text, s.title
               FROM msg_fts JOIN messages m ON m.id = msg_fts.rowid
               JOIN chunks c ON c.session_id = m.session_id
                            AND m.seq BETWEEN c.start_seq AND c.end_seq
               LEFT JOIN sessions s ON s.session_id = c.session_id
               WHERE msg_fts MATCH ? GROUP BY c.id ORDER BY MIN(rank) LIMIT ?""",
            (query, limit * 3),
        ).fetchall()
    except sqlite3.OperationalError:
        fts = []          # an unparseable FTS query is not a reason to lose semantic results
    finally:
        c.close()

    for i, row in enumerate(fts):
        ranks[row[0]] = ranks.get(row[0], 0) + 1.0 / (K + i + 1)
        meta[row[0]] = row

    sem_note = ""
    try:
        model = _get_model()
        qvec = np.array(list(model.query_embed([query]))[0])
        hits, rows = _scan(qvec, db, limit * 3)
        by_id = {r[0]: r for r in rows}
        for i, (_score, cid, sid, ts, text) in enumerate(hits):
            ranks[cid] = ranks.get(cid, 0) + 1.0 / (K + i + 1)
            meta.setdefault(cid, (cid, sid, ts, text, by_id[cid][5]))
    except EmbeddingUnavailable as e:
        # Commandment #13: degrade, don't crash — but say so, so nobody reads a keyword-only
        # result set as though meaning had been searched.
        sem_note = f"\n⚠️  Semantic half unavailable ({e}) — these are keyword matches only."

    if not ranks:
        return f"No matches for {query!r}." + sem_note

    out = [f"Matches for {query!r} (exact + semantic, rank-fused):"]
    for cid, _s in sorted(ranks.items(), key=lambda kv: -kv[1])[:limit]:
        _id, sid, ts, text, title = meta[cid]
        out.append(f"\n{title or sid[:8]} — {(ts or '')[:10]}  (session {sid[:8]})")
        out.append("   " + " ".join(text.split())[:400])
    return "\n".join(out) + sem_note


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        print(build())
    elif cmd == "semantic":
        print(semantic(" ".join(sys.argv[2:])))
    elif cmd == "search":
        print(search(" ".join(sys.argv[2:])))
    else:
        print("usage: semantic_index.py [build | semantic QUERY | search QUERY]")
