#!/usr/bin/env python3
"""MF8: once the memory corpus is read-only to Moses, what he writes and the registry he reads come from
moses_env (MOSES_DATA, MOSES_REGISTRY), never from beside the notes.

Each check runs the real code in a child process with the corpus a folder nobody may write. Each fix is
then reverted on purpose and the check it guards must turn red (commandment 8).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

PROBE = r'''
import sys, json
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[1] + "/lib")
import dispatch, proposals
out = {}
try:
    out["learned"] = dispatch.file_learning("bound it, don't ban it", source="Atlas")[0]
except OSError as e:
    out["learned"] = f"error: {type(e).__name__}"
out["todos"] = proposals.open_todos()
print(json.dumps(out))
'''


def run(agent_dir: Path) -> dict:
    d = Path(tempfile.mkdtemp())
    try:
        mem, data = d / "memory", d / "data"
        mem.mkdir(); data.mkdir()
        (mem / "projects.json").write_text(json.dumps({"projects": [{"id": "OLD", "name": "stale corpus copy",
            "milestones": [{"title": "from the corpus", "done": False}]}]}))
        (data / "projects.json").write_text(json.dumps({"projects": [{"id": "NEW", "name": "moses-data copy",
            "milestones": [{"title": "from moses-data", "done": False}]}]}))
        mem.chmod(0o555)
        env = {k: v for k, v in os.environ.items() if not k.startswith("MOSES_")}
        env.update(MOSES_MEMORY_DIR=str(mem), MOSES_DATA=str(data), MOSES_REGISTRY=str(data / "projects.json"),
                   MOSES_STATE=str(d / "state"), MOSES_SETTINGS=str(d / "none.env"))
        r = subprocess.run([sys.executable, "-c", PROBE, str(agent_dir)], capture_output=True, text=True, env=env)
        o = json.loads(r.stdout.splitlines()[-1]) if r.returncode == 0 and r.stdout.strip() else {"error": r.stderr[-400:]}
        o["file_in_data"] = (data / "look-into-further.md").exists()
        o["file_in_memory"] = (mem / "look-into-further.md").exists()
        return o
    finally:
        (d / "memory").chmod(0o755)
        shutil.rmtree(d)


CHECKS = [
    ("a lesson from Atlas is filed in Moses's own folder (MOSES_DATA)",
     lambda o: o.get("learned") is True and o.get("file_in_data") and not o.get("file_in_memory")),
    ("open todos come from MOSES_REGISTRY, not the corpus copy",
     lambda o: "from moses-data" in o.get("todos", [])
     and not any("corpus" in t for t in o.get("todos", []))),
]

MUTANTS = [
    ("lessons written beside the notes", "dispatch.py",
     'path = Path(memory or _env.DATA) / "look-into-further.md"', 'path = Path(memory or _env.MEMORY) / "look-into-further.md"',
     CHECKS[0][0]),
    ("registry read beside the notes", "proposals.py",
     'reg = Path(memory) / "projects.json" if memory else _env.REGISTRY',
     'reg = Path(memory or _env.MEMORY) / "projects.json"', CHECKS[1][0]),
]


def evaluate(o: dict) -> list[str]:
    return [label for label, ok in CHECKS if not ok(o)]


def main() -> int:
    o = run(HERE)
    failed = evaluate(o)
    for label, _ in CHECKS:
        print(f"  {'FAIL' if label in failed else 'PASS'}  {label}")
    if failed:
        print(f"  probe: {o}")
    blind = []
    for name, f, old, new, expect in MUTANTS:
        src = (HERE / f).read_text()
        if old not in src:
            blind.append(f"{name}: the code it breaks is gone (update the mutant)")
            continue
        d = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(HERE, d / "agent", ignore=shutil.ignore_patterns("venv", "__pycache__", "*.db"))
            (d / "agent" / f).write_text(src.replace(old, new, 1))
            if expect not in evaluate(run(d / "agent")):
                blind.append(f"{name}: '{expect}' stayed green")
        finally:
            shutil.rmtree(d)
    for b in blind:
        print(f"  FAIL guard not watched failing — {b}")
    print(f"{len(CHECKS) - len(failed)}/{len(CHECKS)} checks pass; {len(MUTANTS) - len(blind)}/{len(MUTANTS)} guards watched failing")
    return 1 if failed or blind else 0


if __name__ == "__main__":
    sys.exit(main())
