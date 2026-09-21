"""discovery — go looking for connections nobody asked about.

THE DISTINCTION THIS MODULE EXISTS FOR
[[semantic_index]] built *retrieval*: Brad asks, it finds. Brad's actual requirement (2026-08-06) is
*discovery*: **"see where those ideas may either overlap or come together as a new idea"** — and he
named the reason it has to be built early: the convergence may not happen for *"months or even a
year down the road, and there'll be no way to catch something like that unless I build that system
now."*

A question you never think to ask never gets answered. That is the whole gap. Retrieval cannot
close it, no matter how good the embeddings are, because retrieval waits to be queried. So this
module runs unprompted and reports what it noticed.

THREE MODES, DELIBERATELY DIFFERENT IN WHAT THEY NEED TO WORK
  link()        — one new idea against the whole corpus. Works TODAY, with one project.
  connections() — a standing scan for pairs that converge across distant origins. Thin today by
                  construction; this is the one built for a year from now.
  themes()      — cluster the corpus so Brad can see what he actually thinks about, unprompted.

WHY NAIVE NEAREST-NEIGHBOUR WOULD BE USELESS
Three failure modes swamp the signal, and each needs its own defense:
  1. Adjacent chunks of one conversation are trivially similar and mean nothing → require pairs to
     come from different sessions, or be far apart in time.
  2. Boilerplate ("Pushed. Branch master…", "Let me check that") is close to EVERYTHING → the
     genericness filter below removes chunks whose average similarity to the corpus is high. A
     chunk near everything is near nothing in particular.
  3. Re-reporting the same pair every week trains Brad to ignore the report → a seen-ledger, so a
     connection is announced once.

HONESTY REQUIREMENT
82% of today's corpus is a single recovered session. A tool that printed "here are your converging
ideas" from within-project pairs would be manufacturing insight, which is worse than silence — it
is the same failure as a monitor that reports success while checking nothing. Every report states
the corpus composition and says plainly when there is not enough spread to draw from.
"""

from __future__ import annotations

import sys as _sys, pathlib as _pl  # noqa: E401
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent / "agent" / "lib"))
import moses_env as _env  # noqa: E402 — whose home, where Moses lives, the operator's settings
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from semantic_index import DB, DIM, EmbeddingUnavailable, _get_model

MEMORY = _env.MEMORY
DISCOVERIES = MEMORY / "discoveries.md"

# A pair must come from genuinely different context to count as a connection rather than a
# restatement. Different session is the primary test; the day gap catches the case where one long
# session spans weeks (the recovered 7,555-turn one covers 06-12 to 06-27).
MIN_DAYS_APART = 14

# Similarity floor. Below ~0.55 with these embeddings the pairs are topical coincidence rather
# than shared substance; above ~0.92 they are usually the same text restated.
SIM_FLOOR = 0.58
SIM_CEILING = 0.93

# Fraction of the corpus discarded as boilerplate before pairing, ranked by mean similarity to
# everything else. Tuned by inspection: at 0.25 the surviving pairs were still mostly deploy
# chatter; at 0.40 real design discussion started surviving intact.
GENERIC_CUT = 0.40

LEDGER = """
CREATE TABLE IF NOT EXISTS connections_seen (
    pair_hash   TEXT PRIMARY KEY,
    first_seen  TEXT NOT NULL,
    score       REAL
);
"""


def _load(db: Path = DB):
    """Every embedded chunk with the provenance needed to judge whether a pair is interesting."""
    c = sqlite3.connect(db)
    c.executescript(LEDGER)
    rows = c.execute(
        "SELECT ch.id, ch.session_id, ch.ts, ch.text, ch.vec, s.title, s.project"
        " FROM chunks ch LEFT JOIN sessions s ON s.session_id = ch.session_id"
        " WHERE ch.vec IS NOT NULL ORDER BY ch.id"
    ).fetchall()
    c.close()
    if not rows:
        return None, None
    mat = np.frombuffer(b"".join(r[4] for r in rows), dtype="<f4").reshape(len(rows), DIM)
    mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    return rows, mat.astype(np.float32)


def _genericness(mat: np.ndarray) -> np.ndarray:
    """Mean similarity of each chunk to the whole corpus.

    This is the single most useful filter in the module. Boilerplate embeds near everything —
    "Pushed. Branch master." is vaguely close to every engineering sentence Brad has ever written,
    so it wins pairings on volume rather than meaning. Ranking by this and cutting the top of the
    distribution removes it without a keyword blocklist that would need endless maintenance.
    """
    n = len(mat)
    means = np.empty(n, dtype=np.float32)
    for i in range(0, n, 512):                       # blocked: never materialize n×n
        block = mat[i:i + 512] @ mat.T
        means[i:i + 512] = (block.sum(axis=1) - 1.0) / max(n - 1, 1)
    return means


def _day(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _excerpt(text: str, n: int = 260) -> str:
    return " ".join(text.split())[:n]


def _composition(rows) -> tuple[str, int]:
    """How concentrated the corpus is — the caveat every report has to carry."""
    from collections import Counter
    per = Counter(r[1] for r in rows)
    top = per.most_common(1)[0][1] if per else 0
    pct = round(100 * top / max(len(rows), 1))
    return (f"{len(rows)} chunks across {len(per)} session(s); "
            f"largest single session is {pct}% of the corpus"), pct


def connections(limit: int = 12, db: Path = DB, record: bool = False, repeat: bool = False) -> str:
    """Pairs of distant-origin chunks that converge in meaning. The standing scan."""
    rows, mat = _load(db)
    if rows is None:
        return "Nothing embedded yet — run `semantic_index.py build`."

    comp, top_pct = _composition(rows)
    gen = _genericness(mat)
    keep = np.argsort(gen)[: int(len(rows) * (1 - GENERIC_CUT))]
    keep_set = set(keep.tolist())

    days = [_day(r[2]) for r in rows]
    found: list[tuple[float, int, int]] = []

    # Top-k per row rather than the full pair matrix: this stays linear in memory and keeps
    # working when the corpus is 40k chunks instead of 1.5k.
    for i in range(0, len(mat), 512):
        block = mat[i:i + 512] @ mat.T
        for bi, row in enumerate(block):
            gi = i + bi
            if gi not in keep_set:
                continue
            row[gi] = -1.0
            for gj in np.argpartition(-row, min(25, len(row) - 1))[:25]:
                gj = int(gj)
                if gj <= gi or gj not in keep_set:
                    continue                          # gj<=gi dedupes the symmetric pair
                s = float(row[gj])
                if not (SIM_FLOOR <= s <= SIM_CEILING):
                    continue
                if rows[gi][1] == rows[gj][1]:
                    di, dj = days[gi], days[gj]
                    if not (di and dj) or abs((di - dj).days) < MIN_DAYS_APART:
                        continue                      # same conversation, same week — restatement
                found.append((s, gi, gj))

    found.sort(reverse=True)

    c = sqlite3.connect(db)
    c.executescript(LEDGER)
    seen = {h for (h,) in c.execute("SELECT pair_hash FROM connections_seen")}

    out, shown, used = [], 0, set()
    for s, i, j in found:
        h = hashlib.sha256(f"{rows[i][0]}:{rows[j][0]}".encode()).hexdigest()[:24]
        if h in seen and not repeat:
            continue
        # One chunk, one pair. Without this a single hub chunk pairs with four neighbors and eats
        # the whole report — the first run showed the same excerpt three times, which reads as
        # three findings but is one. Diversity matters more than squeezing out the last 0.01.
        if i in used or j in used:
            continue
        used.add(i); used.add(j)
        shown += 1
        if shown > limit:
            break
        a, b = rows[i], rows[j]
        out.append(f"\n[{s:.2f}]  {a[5] or '?'} {(a[2] or '')[:10]}  ↔  {b[5] or '?'} {(b[2] or '')[:10]}")
        out.append(f"   A: {_excerpt(a[3])}")
        out.append(f"   B: {_excerpt(b[3])}")
        if record:
            c.execute("INSERT OR IGNORE INTO connections_seen VALUES (?,?,?)",
                      (h, datetime.now(timezone.utc).isoformat(), s))
    c.commit()
    c.close()

    head = [f"CONNECTIONS — {datetime.now().strftime('%Y-%m-%d %H:%M')}", comp]
    if top_pct >= 70:
        head.append("⚠️  The corpus is dominated by one session, so cross-PROJECT convergence has "
                    "little to draw on yet. What follows is real but mostly within-project; this "
                    "gets sharper as more projects accumulate.")
    if not out:
        head.append("")
        head.append("No new connections above threshold." if seen else "No connections above threshold.")
        head.append("That is a finding, not a failure — nothing is being withheld.")
        return "\n".join(head)
    head.append("")
    head.append("Pairs that converge in meaning despite coming from different places. "
                "Nobody asked for these:")
    return "\n".join(head + out)


def link(text: str, limit: int = 6, db: Path = DB) -> str:
    """What in the corpus does this new thing touch? The mode that works today.

    Highest-value path right now: Brad captures idea #4, and Moses immediately says which problem
    he already solved in June is relevant — without him knowing to go looking.
    """
    rows, mat = _load(db)
    if rows is None:
        return "Nothing embedded yet."
    try:
        model = _get_model()
    except EmbeddingUnavailable as e:
        return f"Cannot link — {e}"
    q = np.array(list(model.query_embed([text]))[0], dtype=np.float32)
    q /= np.linalg.norm(q) + 1e-9

    gen = _genericness(mat)
    keep = set(np.argsort(gen)[: int(len(rows) * (1 - GENERIC_CUT))].tolist())
    scores = mat @ q
    order = [i for i in np.argsort(-scores) if i in keep][:limit]

    hits = [(float(scores[i]), rows[i]) for i in order if scores[i] >= SIM_FLOOR]
    if not hits:
        return ("Nothing in the corpus connects to this yet — it is genuinely new ground.\n"
                "Worth recording precisely because there is no prior art to lean on.")
    out = ["This connects to work you have already done:"]
    for s, r in hits:
        out.append(f"\n[{s:.2f}] {r[5] or '?'} — {(r[2] or '')[:10]}")
        out.append(f"   {_excerpt(r[3])}")
    return "\n".join(out)


def _kmeans(mat: np.ndarray, k: int, iters: int = 40, seed: int = 0):
    """k-means++ on unit vectors; cosine distance is Euclidean here, so plain k-means is correct.

    Written out rather than pulling in scikit-learn: that is ~100 MB of dependency to run an
    algorithm that is fifteen lines, on a box where the whole point was staying light.
    """
    rng = np.random.default_rng(seed)
    cent = mat[rng.integers(len(mat))][None, :]
    for _ in range(k - 1):
        d = 1.0 - (mat @ cent.T).max(axis=1)
        d = np.clip(d, 0, None) ** 2
        if d.sum() <= 0:
            break
        cent = np.vstack([cent, mat[rng.choice(len(mat), p=d / d.sum())]])
    for _ in range(iters):
        lab = (mat @ cent.T).argmax(axis=1)
        new = np.vstack([
            mat[lab == i].mean(axis=0) if np.any(lab == i) else cent[i]
            for i in range(len(cent))
        ])
        new /= np.linalg.norm(new, axis=1, keepdims=True) + 1e-9
        if np.allclose(new, cent, atol=1e-5):
            cent = new
            break
        cent = new
    return (mat @ cent.T).argmax(axis=1), cent


def themes(k: int = 10, per: int = 3, db: Path = DB) -> str:
    """Cluster the corpus into what Brad actually spends thought on.

    Deliberately returns representative excerpts and NO generated labels. Naming a cluster is a
    language-model job, and the model is Brad's own Claude app reading this output — which costs
    nothing, where an API call to label ten clusters would violate the standing constraint. The
    tool does the arithmetic; the model does the naming.
    """
    rows, mat = _load(db)
    if rows is None:
        return "Nothing embedded yet."
    if len(rows) < k * 3:
        k = max(2, len(rows) // 3)

    gen = _genericness(mat)
    keep = np.argsort(gen)[: int(len(rows) * (1 - GENERIC_CUT))]
    sub = mat[keep]
    lab, cent = _kmeans(sub, k)

    out = [f"THEMES — {len(keep)} substantive chunks in {k} clusters",
           "Excerpts only, no labels: name these yourself from what you read.", ""]
    for ci in range(k):
        idx = np.where(lab == ci)[0]
        if len(idx) == 0:
            continue
        sims = sub[idx] @ cent[ci]
        best = idx[np.argsort(-sims)][:per]
        spanned = sorted({(rows[keep[i]][5] or "?") for i in idx})
        dates = sorted(d for d in ((rows[keep[i]][2] or "")[:10] for i in idx) if d)
        span = f"{dates[0]} → {dates[-1]}" if dates else "?"
        out.append(f"── cluster {ci + 1}  ·  {len(idx)} chunks  ·  {span}  ·  {', '.join(spanned)}")
        for i in best:
            out.append(f"   • {_excerpt(rows[keep[i]][3], 200)}")
        out.append("")
    return "\n".join(out)


def sweep(db: Path = DB) -> str:
    """The unprompted run. Records what it reports, and appends anything new to the corpus."""
    text = connections(limit=8, db=db, record=True)
    if "No new connections" in text or "No connections above" in text:
        return text
    MEMORY.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    header = "" if DISCOVERIES.exists() else (
        "# Discoveries\n\nConnections Moses found on his own, unprompted. Written by "
        "`discovery.py sweep`. Each pair is reported once.\n")
    with DISCOVERIES.open("a", encoding="utf-8") as f:
        f.write(f"{header}\n## {stamp}\n\n{text}\n")
    return text + f"\n\nRecorded to {DISCOVERIES}."


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "connections"
    if cmd == "connections":
        print(connections(repeat="--repeat" in sys.argv))
    elif cmd == "link":
        print(link(" ".join(a for a in sys.argv[2:] if not a.startswith("--"))))
    elif cmd == "themes":
        print(themes())
    elif cmd == "sweep":
        print(sweep())
    else:
        print("usage: discovery.py [connections [--repeat] | link TEXT | themes | sweep]")
