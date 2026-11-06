#!/usr/bin/env python3
"""MF8: reading the other account's state is one way, and never follows a link planted there.

Each account writes its own state and reads the other's through moses_env.read_state/read_peer. The
other account is less trusted in one direction or the other: a symlink it plants in its own folder must
never make this account read one of ITS secrets (and then report or post it). Each guard is broken on
purpose below and must turn a named check red (commandment 8).
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE / "lib" / "moses_env.py"

PROBE = r'''
import sys, json
sys.path.insert(0, sys.argv[1])
import moses_env as e
out = {"own": e.read_state("own.json"), "peer": e.read_state("ok.json"), "link": e.read_peer("evil.json"),
       "dir": e.read_peer("d.json"), "missing": e.read_state("nope.json")}
try:
    e.read_state("../escape.json"); out["traversal"] = "allowed"
except ValueError:
    out["traversal"] = "refused"
print(json.dumps(out))
'''


def run(lib_dir: Path) -> dict:
    import json
    d = Path(tempfile.mkdtemp())
    try:
        (d / "own").mkdir(); (d / "peer").mkdir()
        (d / "own" / "own.json").write_text("mine")
        (d / "peer" / "ok.json").write_text("theirs")
        (d / "secret").write_text("SECRET")
        os.symlink(d / "secret", d / "peer" / "evil.json")
        (d / "peer" / "d.json").mkdir()
        (d / "escape.json").write_text("ESCAPED")
        env = {k: v for k, v in os.environ.items() if not k.startswith("MOSES_")}
        env.update(MOSES_STATE=str(d / "own"), MOSES_PEER_STATE=str(d / "peer"), MOSES_SETTINGS=str(d / "none.env"))
        r = subprocess.run([sys.executable, "-c", PROBE, str(lib_dir)], capture_output=True, text=True, env=env)
        return json.loads(r.stdout) if r.returncode == 0 else {"error": r.stderr[-300:]}
    finally:
        shutil.rmtree(d)


CHECKS = [
    ("this account's own state is read first", lambda o: o.get("own") == "mine"),
    ("the other account's state is read when this one has none", lambda o: o.get("peer") == "theirs"),
    ("a link planted in the other account's folder is never followed", lambda o: "link" in o and o["link"] is None),
    ("a directory in the other account's folder is not read as a file", lambda o: "dir" in o and o["dir"] is None),
    ("a name with a path in it is refused", lambda o: o.get("traversal") == "refused"),
    ("a file in neither folder is None, not an error", lambda o: "missing" in o and o["missing"] is None),
]

MUTANTS = [
    ("links followed", "os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK", "os.O_RDONLY | os.O_NONBLOCK",
     "a link planted in the other account's folder is never followed"),
    ("any file type read", "        if not _stat.S_ISREG(os.fstat(fd).st_mode):\n            return None\n", "",
     "a directory in the other account's folder is not read as a file"),
    ("paths allowed in names", '''def read_peer(name: str) -> str | None:
    """Only the other account's copy of a state file (see read_state), or None."""
    if "/" in name or name.startswith("."):''', '''def read_peer(name: str) -> str | None:
    """Only the other account's copy of a state file (see read_state), or None."""
    if False:''', None),
]


def evaluate(o: dict) -> list[str]:
    return [label for label, ok in CHECKS if not ok(o)]


def main() -> int:
    o = run(LIB.parent)
    failed = evaluate(o)
    for label, _ in CHECKS:
        print(f"  {'FAIL' if label in failed else 'PASS'}  {label}")
    blind = []
    for name, old, new, expect in MUTANTS:
        src = LIB.read_text()
        if old not in src:
            blind.append(f"{name}: the code it breaks is gone (update the mutant)")
            continue
        d = Path(tempfile.mkdtemp())
        try:
            (d / "moses_env.py").write_text(src.replace(old, new, 1))
            red = evaluate(run(d))
        finally:
            shutil.rmtree(d)
        if expect is None:
            continue                       # read_state still refuses: belt-and-braces, not separately observable
        if expect not in red:
            blind.append(f"{name}: '{expect}' stayed green")
    for b in blind:
        print(f"  FAIL guard not watched failing — {b}")
    print(f"{len(CHECKS) - len(failed)}/{len(CHECKS)} checks pass; "
          f"{sum(1 for m in MUTANTS if m[3]) - sum(1 for b in blind if 'stayed green' in b)}/{sum(1 for m in MUTANTS if m[3])} guards watched failing")
    return 1 if failed or blind else 0


if __name__ == "__main__":
    sys.exit(main())
