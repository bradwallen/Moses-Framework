#!/usr/bin/env python3
"""moses-project must be able to reach the module it wraps.

    python3 agent/moses_project_test.py

WHY THIS EXISTS: the wrapper has no logic of its own — every command is a call into mcp/projects.py,
found through a path relative to the wrapper's own file. An installed copy once hardcoded
/home/brad/moses-mcp instead, and when the module moved it broke silently for weeks: nothing ran
the wrapper, so nothing noticed `import projects` failing. This runs it, the way `moses projects`
does, from a directory that is not the checkout, so the import can only succeed via its own path.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
WRAPPER = HERE / "moses-project"

fails = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  — {detail}"))
    if not cond:
        fails.append(label)


# A registry of its own, so running the wrapper can never read or touch Brad's live one.
work = Path(tempfile.mkdtemp(prefix="moses-project-test-"))
reg = work / "projects.json"
reg.write_text(json.dumps({"projects": [{"id": "fixture", "name": "Fixture Project", "rank": 1}],
                           "inbox": []}))
env = {"PATH": "/usr/bin:/bin", "HOME": str(work), "MOSES_REGISTRY": str(reg)}


def run(*args):
    return subprocess.run([sys.executable, str(WRAPPER), *args], cwd=work, env=env,
                          capture_output=True, text=True, timeout=30)


print("the wrapper imports projects and answers through it")
r = run("list")
check("`list` exits 0", r.returncode == 0, f"rc={r.returncode}: {r.stderr.strip()[-300:]}")
check("no import failure", "ModuleNotFoundError" not in r.stderr and "ImportError" not in r.stderr,
      r.stderr.strip()[-300:])
check("the fixture project comes back from the module", "Fixture Project" in r.stdout,
      f"stdout was {r.stdout.strip()[:200]!r}")

print("the docstring names the module that actually exists")
r = run("help")
check("help names mcp/projects.py", "mcp/projects.py" in r.stdout and "moses-mcp/" not in r.stdout,
      r.stdout.strip()[:200])
check("and that file is there", (HERE.parent / "mcp" / "projects.py").is_file())

shutil.rmtree(work, ignore_errors=True)
print(f"\n  {len(fails)} failed" if fails else "\n  all good")
sys.exit(1 if fails else 0)
