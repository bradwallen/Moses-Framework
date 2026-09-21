#!/usr/bin/env python3
"""relevant-notes.py — put the memory notes and doc sections that match a prompt in front of Claude.

UserPromptSubmit hook (via inject-relevant-notes.sh). Brad, 2026-09-13: *"I tend to re-use a session
for a long time and from time to time we have an issue that could have been avoided by always checking
the commandments and/or existing documentation."*

The Commandments already arrive on every prompt (inject-commandments.sh), and the memory INDEX arrives
at session start. What never arrived was the specific note: in a session days old, the one-line index
is compacted away, and a prompt about the edit page does not remind anyone that
gotcha-btp-edit-page-streamed-reply exists. This matches the prompt's words against every note's name
and description (weighted) and body, plus the project's docs by section, and lists the few that clear
a relevance bar — names and descriptions only, so the cost is a few hundred characters, not the notes.

WHY KEYWORDS, NOT EMBEDDINGS: Moses has a local embedding model (mcp/semantic_index.py), but loading it
costs ~1.5s before a single vector, on every prompt, inside a 5s hook budget. BM25 over 150 short files
runs in well under that, needs nothing installed, and a prompt about "the edit page" shares words with
the note about the edit page. Revisit if the log shows misses that only meaning would catch.

WHY THE BAR IS HIGH: a hook that lists five loosely related notes on every "yes please" is noise, and
noise gets ignored — the same death as a guard that fires on correct work. A result needs two distinct
prompt words, not one, and a score over the cutoff. Short replies inject nothing.

WHAT IT CANNOT DO: make anyone read the note. It guarantees the pointer is present at the moment it
matters; like the Commandments hook, presence is not compliance.

Every run is counted in ~/.local/state/moses/relevant-notes.log (note names only, never prompt text), so
the hit rate is visible rather than assumed. relevant-notes-selftest.sh proves it discriminates.

CLI (for the self-test and calibration):  relevant-notes.py --query "text" [--cwd DIR] [--json]
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from pathlib import Path

MEMORY = Path(os.environ.get("RELEVANT_NOTES_MEMORY") or os.environ.get("MOSES_MEMORY_DIR") or Path.home() / ".claude" / "memory")
LOG = Path(os.environ.get("RELEVANT_NOTES_LOG", Path.home() / ".local/state/moses/relevant-notes.log"))

MAX_NOTES = 3
MAX_DOCS = 2
MIN_TERMS = 2          # distinct prompt words a result must contain
CUTOFF = float(os.environ.get("RELEVANT_NOTES_CUTOFF", "7.0"))
RELATIVE = 0.6         # and within this fraction of the best result, so a long prompt's tail drops off
MIN_PROMPT_TERMS = 2   # "yes please", "continue", "Problems?" carry no topic
DESC_WEIGHT = 3        # a word in a note's name or description counts three times one in its body
# Copies of other notes: the pre-trim index repeats every description, so it matched everything.
EXCLUDE = {"index-detail-archive"}

STOP = set("""
a about above after again against all also am an and any are aren't as at be because been before being
below between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down
during each few for from further had hadn't has hasn't have haven't having he her here hers herself him
himself his how i i'd i'll i'm i've if in into is isn't it it's its itself let's me more most mustn't
my myself no nor not of off on once only or other ought our ours ourselves out over own same shan't she
should shouldn't so some such than that that's the their theirs them themselves then there there's these
they they'd they'll they're they've this those through to too under until up very was wasn't we we'd
we'll we're we've were weren't what what's when when's where where's which while who who's whom why
why's with won't would wouldn't you you'd you'll you're you've your yours yourself yourselves
yes yeah yup yep ok okay please thanks thank sure go ahead just still really actually maybe probably
thing things something anything everything stuff lot lots bit little kind sort way ways
get got gets getting make makes made making need needs want wants wanted see seen look looks looking
let lets know think thought say said tell told ask asked use used using try tried keep going went come
one two three first last next new old good great fine right wrong well much many more less
now today yesterday tomorrow time times day days week earlier later again already ever never always
also even back still quick real real-time like just can will shall may might must done do
continue resume problems problem issue issues work working worked question questions
hey hi claude you're i'd we'll
""".split())

WORD = re.compile(r"[a-z0-9][a-z0-9'+.]*[a-z0-9]|[a-z0-9]")


def terms(text: str) -> list[str]:
    out = []
    for raw in WORD.findall(text.lower()):
        for w in re.split(r"[.'+]", raw):
            if len(w) < 2 or w in STOP or w.isdigit():
                continue
            if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
                w = w[:-1]
            out.append(w)
    return out


def _frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^(name|description):\s*(.*)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip().strip('"')
    return meta, text[end + 4:]


def load_notes() -> list[dict]:
    docs = []
    for p in sorted(MEMORY.glob("*.md")):
        try:
            meta, body = _frontmatter(p.read_text(errors="ignore"))
        except OSError:
            continue
        if not meta.get("description"):
            continue          # the index, generated inventories and scratch files have no frontmatter
        name = meta.get("name") or p.stem
        if name in EXCLUDE or p.stem in EXCLUDE:
            continue
        head = terms(name.replace("-", " ").replace("_", " ") + " " + meta["description"])
        docs.append({"kind": "note", "label": name, "desc": meta["description"], "path": str(p),
                     "head": head, "body": terms(body)})
    return docs


def load_doc_sections(cwd: str | None) -> list[dict]:
    """The project's docs, one entry per ## section, so a hit names the section rather than a
    40 KB file. Found from the git toplevel of the session's working directory."""
    if not cwd:
        return []
    root = Path(cwd)
    for parent in [root, *root.parents]:
        if (parent / ".git").exists():
            root = parent
            break
    else:
        return []
    files = sorted(set(root.glob("docs/*.md")) | set(root.glob("*/docs/*.md")))
    out = []
    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        rel = str(f.relative_to(root))
        for sec in re.split(r"(?m)^(?=## )", text):
            title = sec.splitlines()[0].lstrip("# ").strip() if sec.strip() else ""
            if not title or not sec.startswith("## "):
                title = "(intro)"
            out.append({"kind": "doc", "label": f"{rel} § {title}"[:120], "desc": "", "path": str(f),
                        "head": terms(title), "body": terms(sec)})
    return out


def score(query: str, corpus: list[dict]) -> list[tuple[float, int, dict]]:
    q = list(dict.fromkeys(terms(query)))
    if len(q) < MIN_PROMPT_TERMS or not corpus:
        return []
    n = len(corpus)
    tfs, lens = [], []
    df: dict[str, int] = {}
    for d in corpus:
        tf: dict[str, int] = {}
        for w in d["head"]:
            tf[w] = tf.get(w, 0) + DESC_WEIGHT
        for w in d["body"]:
            tf[w] = tf.get(w, 0) + 1
        tfs.append(tf)
        lens.append(len(d["head"]) * DESC_WEIGHT + len(d["body"]))
        for w in tf:
            df[w] = df.get(w, 0) + 1
    avg = sum(lens) / n or 1
    k1, b = 1.2, 0.75
    ranked = []
    for d, tf, ln in zip(corpus, tfs, lens):
        s, hit = 0.0, 0
        for w in q:
            f = tf.get(w)
            if not f:
                continue
            hit += 1
            idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
            s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * ln / avg))
        # The name and description are the note's own summary of what it is about. A note that
        # shares words with the prompt only in its body is usually a long note mentioning everything
        # in passing — the Railway note matched a profile-picture bug that way.
        # A doc section's "head" is only its heading — a few words — so one shared word is chance
        # ("clone", "verified"). Two is a topic. A note's description is a sentence; one will do.
        heads = set(d["head"])
        head_hits = sum(1 for w in q if w in heads)
        if hit >= MIN_TERMS and s >= CUTOFF and head_hits >= (2 if d["kind"] == "doc" else 1):
            ranked.append((s, hit, d))
    ranked.sort(key=lambda r: -r[0])
    if ranked:
        top = ranked[0][0]
        ranked = [r for r in ranked if r[0] >= top * RELATIVE]
    return ranked


def pick(query: str, cwd: str | None) -> list[tuple[float, int, dict]]:
    notes = score(query, load_notes())[:MAX_NOTES]
    docs = score(query, load_doc_sections(cwd))[:MAX_DOCS]
    return notes + docs


def render(picked) -> str:
    lines = [
        "NOTES AND DOCS MATCHING THIS PROMPT (found by a hook matching its words, so judge relevance "
        "yourself — but if one covers what you are about to do, read it before acting, and say so if "
        "you depart from it):"
    ]
    for _, _, d in picked:
        if d["kind"] == "note":
            lines.append(f"- {d['label']} — {d['desc'][:220]}  [{d['path']}]")
        else:
            lines.append(f"- {d['label']}  [{d['path']}]")
    return "\n".join(lines)


def log(picked, n_terms: int) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            names = ",".join(d["label"].split(" § ")[0] if d["kind"] == "doc" else d["label"]
                             for _, _, d in picked)
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\tterms={n_terms}\thits={len(picked)}\t{names}\n")
    except OSError:
        pass


def main() -> int:
    if "--query" in sys.argv:
        i = sys.argv.index("--query")
        query = sys.argv[i + 1]
        cwd = sys.argv[sys.argv.index("--cwd") + 1] if "--cwd" in sys.argv else None
        picked = pick(query, cwd)
        if "--json" in sys.argv:
            print(json.dumps([{"label": d["label"], "score": round(s, 2), "terms": h} for s, h, d in picked]))
        else:
            print(render(picked) if picked else "(nothing injected)")
        return 0

    payload = json.loads(sys.stdin.read() or "{}")
    # 2.1.270 sends the text as "prompt" (observed 2026-09-13 with RELEVANT_NOTES_DEBUG). The hooks
    # docs page showed "user_input". Accept either, so a rename in either direction degrades nothing.
    query = payload.get("prompt") or payload.get("user_input") or ""
    # A background task finishing arrives through the same hook, as a <task-notification> block. It is
    # not Brad asking anything, and matching notes to a log path is noise (seen 2026-09-13).
    if query.lstrip().startswith("<task-notification>"):
        return 0
    if os.environ.get("RELEVANT_NOTES_DEBUG"):
        Path(os.environ["RELEVANT_NOTES_DEBUG"]).write_text(json.dumps(sorted(payload.keys())))
    picked = pick(query, payload.get("cwd"))
    log(picked, len(set(terms(query))))
    if not picked:
        return 0
    print(json.dumps({
        "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": render(picked)},
        "suppressOutput": True,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
