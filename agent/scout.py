#!/usr/bin/env python3
"""scout — check an idea against the code that actually exists, before it is proposed or built.

    python3 agent/scout.py --repo PATH --task-file FILE     verdict on line 1, findings after it
    python3 agent/scout.py --target viatica --task "…"      the same, against that target's checkout

WHY THIS EXISTS (Brad, 2026-10-01). Moses filed proposal 6848b820 from a conversation with Atlas: add a
per-service skip counter to "the probe coverage assertion". Brad confirmed it and said build it. Knight
looked, found that nothing in our tree keeps coverage state, has a "skipped" outcome, or ever marks a
service covered — Atlas has that; we do not — and stopped. Moses had agreed with Atlas's description of
Atlas's system and filed it as work on ours, and nine days later told Atlas it was "one of my open
proposals". Nobody checked the idea against the code until the builder did, at the last moment.

The cause was not credulity. It was that MOSES COULD NOT LOOK. His chat runs with no file-reading tool
at all, so every idea he proposed was judged in prose against his memory of the code. Brad's words:
"Moses tends to ALWAYS lean toward proposing whatever Atlas says and needs to do the due diligence
before proposing anything to me — and when I say to implement it, re-do the homework before tasking
Knight, because there can be drift since I work in both Slack and Claude Code."

So this does the homework, at both doors, as a mechanism rather than a request:

  * PROPOSING. The listener posts a proposal, then checks it against the target's checkout and replies
    in the thread — the files it would touch, or a withdrawal with the reason.
  * IMPLEMENTING. Every Knight job checks first, against the freshly fetched code it is about to
    build on — so drift from work done directly in Claude Code is caught at the only moment it matters.
    A false premise ends the job before a build; a grounded one hands Knight the findings.

THE QUESTION IS NARROW ON PURPOSE. Not "is this a good idea" — that is Brad's. Only: does everything
this task ASSUMES ALREADY EXISTS actually exist? Building something new is fine; extending something
that is not there is not. A broad question gets a broad, agreeable answer, which is the failure this
replaces.

It reads only (Read, Grep, Glob — not even Bash, so not git): what it reports is what the files say.
A check that could not run says COULD_NOT_CHECK and is never treated as a pass.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import claude_runner  # noqa: E402 — every model launch goes through the one runner (M20)

ROOT = HERE.parent
TARGETS = ROOT / "knight" / "targets"
TIMEOUT_S = int(os.environ.get("MOSES_SCOUT_TIMEOUT", "240"))

VERDICTS = ("GROUNDED", "PREMISE_FALSE", "ALREADY_DONE", "UNCLEAR")
# The two answers that stop the work. UNCLEAR does not: "I could not tell" is not evidence of a false
# premise, and a check that blocked on doubt would be routed around within a week.
REJECT = frozenset({"PREMISE_FALSE", "ALREADY_DONE"})
COULD_NOT_CHECK = "COULD_NOT_CHECK"

PROMPT = """You are checking a proposed code task against the repository in your working directory,
BEFORE anyone builds it. You can read and search files. You cannot change anything or run anything.

THE TASK:
{task}

Answer exactly one question: does everything this task ASSUMES ALREADY EXISTS actually exist in this
code, roughly as the task describes it? Building something NEW is fine — a task that adds a feature
that does not exist yet is GROUNDED as long as the things it builds ON are real. A task that changes,
extends or fixes something that is not here — or that works so differently the task cannot be done as
written — has a false premise. Look before answering; do not answer from the task's own description.

If ACCEPTANCE CRITERIA are included, check each one the same way: a criterion that requires changing
something that is not in this code, or something we cannot change at all (a third-party or
browser-injected script, another repository, a hosted service), is a false premise too — name it in GAP.
A build judged against a criterion that cannot be met is blocked however good the code is.

The FIRST line of your reply must be exactly one of these words and nothing else:
GROUNDED
PREMISE_FALSE
ALREADY_DONE
UNCLEAR

Then at most eight short lines of plain text:
FILES: the paths the change would touch, relative to the repository root
FOUND: what actually exists today, in one or two sentences, citing path:line
GAP: only if not GROUNDED — what the task assumes that is not true here
Do not propose a design. Do not write code."""


def target_source(target: str) -> Path | None:
    """The checkout a Knight target is built from, read from its registry file — never guessed."""
    f = TARGETS / f"{(target or '').strip()}.env"
    try:
        m = re.search(r"^KT_SOURCE=(\S+)", f.read_text(encoding="utf-8"), re.M)
    except OSError:
        return None
    p = Path(m.group(1).strip("'\"")).expanduser() if m else None
    return p if p and p.is_dir() else None


def head(repo: Path) -> str:
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                       capture_output=True, text=True)
    return r.stdout.strip() or "?"


def parse(text: str) -> dict:
    """The verdict is the first line or it is UNCLEAR. Never inferred from the prose that follows."""
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    first = re.sub(r"[^A-Z_]", "", lines[0].upper()) if lines else ""
    if first in VERDICTS:
        verdict, body = first, lines[1:]
    else:
        verdict = "UNCLEAR"
        body = ["(the check did not open with a verdict, so it counts as unclear)"] + lines
    files = next((ln.split(":", 1)[1].strip() for ln in body if ln.upper().startswith("FILES:")), "")
    return {"verdict": verdict, "findings": "\n".join(body)[:1500], "files": files}


def run(argv: list[str], cwd: Path, timeout: int) -> tuple[int, str, str]:
    """The one place a model is started. Tests replace this; nothing else should."""
    p = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def _could_not(why: str, repo=None) -> dict:
    return {"verdict": COULD_NOT_CHECK, "findings": why, "files": "",
            "at": head(Path(repo)) if repo and Path(repo).is_dir() else "?", "repo": str(repo or "")}


def check(task: str, repo) -> dict:
    """Check `task` against the code in `repo`. Always returns a verdict; never raises."""
    repo = Path(repo) if repo else None
    if not repo or not repo.is_dir():
        return _could_not(f"there is no repository at {repo}")
    if len((task or "").strip()) < 10:
        return _could_not("there was no task to check", repo)
    prompt = PROMPT.format(task=task.strip()[:4000])
    try:
        argv = claude_runner.build_argv("moses-scout", prompt, add_dirs=(str(repo),))
        rc, out, err = run(argv, repo, TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return _could_not(f"the check took longer than {TIMEOUT_S}s", repo)
    except Exception as e:                                              # noqa: BLE001
        return _could_not(f"the check could not start ({type(e).__name__}: {e})", repo)
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        return _could_not(f"the check returned unreadable output (exit {rc}) {(err or '')[:160]}", repo)
    if rc != 0 or data.get("is_error"):
        return _could_not(f"the check failed: {str(data.get('result') or err)[:200]}", repo)
    r = parse(data.get("result") or "")
    r.update(at=head(repo), repo=str(repo))
    return r


def check_target(task: str, target: str) -> dict | None:
    """None when `target` is not a code target — there is nothing to check an idea against."""
    src = target_source(target)
    return check(task, src) if src else None


def one_line(r: dict) -> str:
    """The sentence a person reads: the verdict, where it was checked, and the single finding that matters."""
    lines = r.get("findings", "").splitlines()
    key = next((ln for ln in lines if ln.upper().startswith("GAP:")), "") or \
          next((ln for ln in lines if ln.upper().startswith("FOUND:")), "") or (lines[0] if lines else "")
    where = f" at `{r.get('at', '?')}`" if r.get("at") and r.get("at") != "?" else ""
    return f"*{r['verdict']}*{where} — {key[:300]}".rstrip(" —")


def brief_section(r: dict) -> str:
    """Prepended to Knight's brief. What the code looked like a minute ago, so he builds from fact."""
    if r["verdict"] == COULD_NOT_CHECK:
        return ("PRE-CHECK: the task was NOT checked against the code before this job "
                f"({r['findings']}). Verify its premise yourself before building, and stop and report "
                "if what it assumes is not here.\n\n")
    return (f"PRE-CHECK (read-only scout, this checkout at {r.get('at', '?')}): {r['verdict']}\n"
            f"{r['findings']}\n"
            "This is what exists right now. Build from it; if you find it wrong, say so in the report.\n\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo")
    ap.add_argument("--target")
    ap.add_argument("--task")
    ap.add_argument("--task-file")
    a = ap.parse_args()
    task = a.task or (Path(a.task_file).read_text(encoding="utf-8") if a.task_file else "")
    repo = a.repo or (str(target_source(a.target)) if a.target and target_source(a.target) else "")
    r = check(task, repo)
    print(r["verdict"])
    print(r["findings"])
    print(f"CHECKED: {r.get('repo', '')} at {r.get('at', '?')}")
    return 0     # the verdict is the output; a failed check is a verdict too, not a crash


if __name__ == "__main__":
    raise SystemExit(main())
