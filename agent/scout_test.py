#!/usr/bin/env python3
"""The scout must never turn doubt into a pass, or an outage into a verdict.

    python3 agent/scout_test.py        (free: the model is replaced; no tokens, no network)

scout.py checks an idea against the code before it is proposed or built. Two properties carry the whole
design and are what this pins:

  1. The verdict is the FIRST LINE or it is UNCLEAR. A reply that says "looks grounded to me" in prose
     must not count as GROUNDED — that is a model talking itself into agreement, the exact failure this
     module exists to stop.
  2. Every way the check can fail — no repo, a timeout, a crash, unreadable output, an error result —
     comes back as COULD_NOT_CHECK, never as a pass and never as an exception. Knight builds on
     COULD_NOT_CHECK but is told so; a check that said nothing when it could not look would be a claim.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scout  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  — {detail}"))
    if not cond:
        fails.append(label)


REPO = Path(tempfile.mkdtemp(prefix="scout-test-"))
subprocess.run(["git", "init", "-q", str(REPO)], check=True)
subprocess.run(["git", "-C", str(REPO), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                "--allow-empty", "-m", "root"], check=True)


def fake(result=None, rc=0, raw=None, raises=None):
    """Replace the one place a model starts. Returns what the CLI would have printed."""
    def run(argv, cwd, timeout):
        if raises:
            raise raises
        if raw is not None:
            return rc, raw, ""
        return rc, json.dumps({"result": result, "is_error": rc != 0}), ""
    scout.run = run


print("the verdict is the first line, or it is unclear")
check("GROUNDED on line one is grounded", scout.parse("GROUNDED\nFILES: a.py")["verdict"] == "GROUNDED")
check("markdown around the word is tolerated", scout.parse("**PREMISE_FALSE**\nGAP: x")["verdict"] == "PREMISE_FALSE")
p = scout.parse("Looks GROUNDED to me — the files are there.\nFILES: a.py")
check("prose that merely mentions a verdict is UNCLEAR, not a pass", p["verdict"] == "UNCLEAR", p)
check("and says why it is unclear", "did not open with a verdict" in p["findings"], p["findings"])
check("an empty reply is UNCLEAR", scout.parse("")["verdict"] == "UNCLEAR")
check("the FILES line is lifted out", scout.parse("GROUNDED\nFILES: a.py, b.py\nFOUND: x")["files"] == "a.py, b.py")

print("\nonly a confident negative stops the work")
check("PREMISE_FALSE and ALREADY_DONE stop it", scout.REJECT == {"PREMISE_FALSE", "ALREADY_DONE"})
check("UNCLEAR does not — doubt is not evidence", "UNCLEAR" not in scout.REJECT)
check("COULD_NOT_CHECK does not — the scout's outage must not block a build",
      scout.COULD_NOT_CHECK not in scout.REJECT)

print("\nevery failure is COULD_NOT_CHECK, never a pass and never a crash")
fake("GROUNDED\nFILES: a.py")
r = scout.check("add a mail probe to the diagnosis", REPO)
check("a real answer comes back with the commit it was checked at", r["verdict"] == "GROUNDED" and r["at"] != "?", r)
check("no repository", scout.check("a task long enough", "/nonexistent/repo")["verdict"] == scout.COULD_NOT_CHECK)
check("no task", scout.check("", REPO)["verdict"] == scout.COULD_NOT_CHECK)
fake(raises=subprocess.TimeoutExpired("claude", 1))
check("a timeout", scout.check("a task long enough", REPO)["verdict"] == scout.COULD_NOT_CHECK)
fake(raises=OSError("no such binary"))
check("the CLI cannot start", scout.check("a task long enough", REPO)["verdict"] == scout.COULD_NOT_CHECK)
fake(raw="not json at all")
check("unreadable output", scout.check("a task long enough", REPO)["verdict"] == scout.COULD_NOT_CHECK)
fake(result="GROUNDED\nFILES: a.py", rc=1)
check("an error result, even one that SAYS grounded",
      scout.check("a task long enough", REPO)["verdict"] == scout.COULD_NOT_CHECK)

print("\nit is aimed at the right code, and only ever reads it")
check("a known target resolves to its checkout", scout.target_source("moses") is not None)
check("an unknown target is not a code project", scout.check_target("anything at all", "no-such-target") is None)
prof = scout.claude_runner.profile("moses-scout")
check("the scout is offered only Read, Grep and Glob", sorted(prof.offered()) == ["Glob", "Grep", "Read"], prof.offered())
check("it can run nothing — no Bash grants", prof.bash == ())
check("and reaches no MCP server", json.loads(prof.mcp_config) == {"mcpServers": {}}, prof.mcp_config)

print("\nwhat people and Knight read")
g = {"verdict": "PREMISE_FALSE", "findings": "FOUND: x exists\nGAP: there is no coverage assertion", "at": "abc123"}
check("the one-liner leads with the gap", "no coverage assertion" in scout.one_line(g) and "abc123" in scout.one_line(g))
c = {"verdict": scout.COULD_NOT_CHECK, "findings": "the check took longer than 240s", "at": "?"}
check("an unchecked brief says it was NOT checked, and why",
      "NOT checked" in scout.brief_section(c) and "240s" in scout.brief_section(c))

print(f"\n  {len(fails)} failed" if fails else "\n  all good")
sys.exit(1 if fails else 0)
