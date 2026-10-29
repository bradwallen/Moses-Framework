#!/usr/bin/env python3
"""memory-areas.py — keep one generated index per area of the memory corpus, so no map goes stale.

SessionStart hook (no output: it writes files, it injects nothing). Adopted 2026-10-29 from a
comparison with jaredrhod/ai-memory-vault, which keeps an index note in every folder of an Obsidian
vault and tells the model to "keep the map true" by hand in the same pass as every edit. That is an
instruction, and instructions lapse. Here the map is GENERATED from each note's own frontmatter, so it
cannot disagree with the notes.

WHY AREAS AT ALL: MEMORY.md is the one index every session loads, and at ~190 notes it had to be
compacted (2026-10-12) to stay readable — several notes per line, the full list in an archive. The
area files hold the complete list, one line per note, without making the boot index longer.

WHAT IT WRITES: <memory>/areas/<area>.md, one per area, plus areas/README.md. Each line is the note's
[[name]] and description, marked JOB or LOCKED where the note is one, and "not in MEMORY.md" where the
boot index never mentions it — the orphans a session would otherwise never find. The files have no
frontmatter on purpose: relevant-notes.py skips files without a description, so a list that names
every note can never match every prompt (the index-detail-archive problem).

WHERE A NOTE GOES: an `area:` line in its frontmatter wins. Otherwise the first area (<memory>/areas.conf, else the defaults) whose
pattern matches its name. A note no rule matches lands in "Reference and other", never nowhere.

Writes only when the content changed, through a temp file and a rename, so two sessions starting at
once cannot leave a half-written file. Fails open: any error and the session starts normally.

CLI:  memory-areas.py [--memory DIR] [--check]   (--check: exit 1 if the files are out of date)
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

MEMORY = Path(os.environ.get("MOSES_MEMORY_DIR") or os.environ.get("CLAUDE_PLUGIN_OPTION_MEMORY_DIR")
              or Path.home() / ".claude" / "memory")

# (slug, title, pattern on the note's name). First match wins, so the specific rules come first.
# These are the neutral defaults. An operator's own areas (one per product, say) belong to the
# operator, not the framework, so they live in <memory>/areas.conf beside the notes they sort:
#   slug<TAB>Title<TAB>regex on the note's name     (# starts a comment)
DEFAULT_AREAS = [
    ("jobs", "Jobs — recurring operations with written steps", r"^job-"),
    ("working-rules", "Working rules", r"^feedback-"),
    ("projects", "Projects", r"^project[-_]"),
]
CATCH_ALL = ("reference", "Reference and other", r"")
SKIP = {"MEMORY", "index-detail-archive"}


def areas_for(memory: Path) -> list[tuple[str, str, str]]:
    """The operator's areas.conf if there is one, else the defaults. Either way a catch-all comes
    last, so a note no rule matches is listed somewhere rather than nowhere."""
    rules = []
    conf = memory / "areas.conf"
    if conf.exists():
        for line in conf.read_text(errors="ignore").splitlines():
            parts = line.split("\t")
            if line.strip() and not line.lstrip().startswith("#") and len(parts) == 3:
                re.compile(parts[2])          # a bad pattern fails here, and the hook fails open
                rules.append((parts[0].strip(), parts[1].strip(), parts[2]))
    rules = rules or list(DEFAULT_AREAS)
    if not any(slug == CATCH_ALL[0] for slug, _, _ in rules):
        rules.append(CATCH_ALL)
    return rules


def frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    meta = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^(name|description|locked|area):\s*(.*)$", line)
        if m:
            v = m.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] == '"':
                v = v[1:-1]
            meta[m.group(1)] = v.replace('\\"', '"')
    return meta


def build(memory: Path) -> dict[str, str]:
    index = (memory / "MEMORY.md").read_text(errors="ignore") if (memory / "MEMORY.md").exists() else ""
    areas = areas_for(memory)
    groups: dict[str, list[str]] = {slug: [] for slug, _, _ in areas}
    for p in sorted(memory.glob("*.md")):
        if p.stem in SKIP:
            continue
        meta = frontmatter(p.read_text(errors="ignore"))
        if not meta.get("description"):
            continue                      # scratch files, ledgers and generated inventories
        name = meta.get("name") or p.stem
        slug = meta.get("area") if meta.get("area") in groups else next(
            s for s, _, pat in areas if re.search(pat, name, re.I))
        marks = []
        if name.startswith("job-"):
            marks.append("JOB")
        if meta.get("locked"):
            marks.append("LOCKED")
        if p.name not in index and f"[[{name}]]" not in index:
            marks.append("not in MEMORY.md")
        tag = f" `{' · '.join(marks)}`" if marks else ""
        groups[slug].append(f"- [[{name}]]{tag} — {meta['description']}")
    out = {}
    head = "<!-- GENERATED by memory-areas.py at session start. Do not edit: change the notes' frontmatter. -->\n"
    for slug, title, _ in areas:
        lines = groups[slug]
        out[f"{slug}.md"] = f"{head}# {title}\n\n{len(lines)} notes.\n\n" + "\n".join(lines) + "\n"
    readme = [head, "# Memory by area\n", "One generated list per area, every note in it. MEMORY.md stays the short boot index.\n"]
    readme += [f"- [[{slug}]] — {title}: {len(groups[slug])} notes" for slug, title, _ in areas]
    out["README.md"] = "\n".join(readme) + "\n"
    return out


def main() -> int:
    memory = Path(sys.argv[sys.argv.index("--memory") + 1]) if "--memory" in sys.argv else MEMORY
    if not memory.is_dir():
        return 0
    files = build(memory)
    areas = memory / "areas"
    stale = [n for n, body in files.items()
             if not (areas / n).exists() or (areas / n).read_text(errors="ignore") != body]
    if "--check" in sys.argv:
        print("areas up to date" if not stale else "out of date: " + ", ".join(stale))
        return 1 if stale else 0
    areas.mkdir(exist_ok=True)
    for n in stale:
        tmp = areas / f".{n}.{os.getpid()}.tmp"
        tmp.write_text(files[n])
        os.replace(tmp, areas / n)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)        # fail open: a broken index generator must never stop a session starting
