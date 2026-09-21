#!/usr/bin/env python3
"""framework_sync_test — moses-framework-sync's refusals, and moses_env's rules, each watched failing.

    ~/moses-venv/bin/python agent/framework_sync_test.py

Everything runs against throwaway git repositories in a temp directory: the real framework checkout and
the operator's settings are never touched. The framework side gets a stand-in secret scanner with the
same contract as tools/scan-secrets.sh (exit non-zero on a staged secret), so the refusal path is
exercised without depending on another repository being present.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SYNC = HERE / "moses-framework-sync"
fails = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  —  {detail}" if detail and not cond else ""))
    if not cond:
        fails += 1


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def new_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q")
    git(path, "config", "user.email", "t@t.test")
    git(path, "config", "user.name", "t")
    return path


def commit_all(repo: Path) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "x", "--allow-empty")


SCANNER = """#!/usr/bin/env bash
# Stand-in with tools/scan-secrets.sh's contract: a staged file containing a token shape fails.
if git diff --cached --name-only -z | xargs -0 -r grep -l 'xoxb-1111111111-2222222222-' 2>/dev/null | grep -q .; then
  echo "SECRET"; exit 1; fi
exit 0
"""


def world(tmp: Path, extra_map: str = "") -> tuple[Path, Path]:
    """An instance tree (with this checkout's sync script and map rules) and a framework checkout."""
    inst = new_repo(tmp / "inst")
    (inst / "agent").mkdir()
    shutil.copy2(SYNC, inst / "agent" / "moses-framework-sync")
    for i in range(55):   # past the script's "this checkout looks wrong" floor
        (inst / "agent" / f"m{i}.py").write_text(f"x = {i}\n")
    (inst / "agent" / "roster.json").write_text("{}\n")
    (inst / "framework.map").write_text(
        "framework.map  private  lists what is private\n"
        "agent/roster.json  private  the live roster\n" + extra_map + "agent/*  ship\n")
    commit_all(inst)
    fw = new_repo(tmp / "fw")
    (fw / "tools").mkdir()
    (fw / "tools" / "scan-secrets.sh").write_text(SCANNER)
    (fw / "tools" / "scan-secrets.sh").chmod(0o755)
    (fw / "README.md").write_text("framework's own\n")
    (fw / "agent").mkdir()
    (fw / "agent" / "gone.py").write_text("stale\n")
    commit_all(fw)
    return inst, fw


def run(inst: Path, fw: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(inst / "agent" / "moses-framework-sync"), "--framework", str(fw), *args],
                          capture_output=True, text=True)


print("moses-framework-sync")
with tempfile.TemporaryDirectory() as t:
    inst, fw = world(Path(t))
    r = run(inst, fw, "--check")
    check("--check reports drift as exit 1, and names the gap", r.returncode == 1 and "behind" in r.stdout, r.stdout + r.stderr)
    r = run(inst, fw)
    check("a sync succeeds", r.returncode == 0, r.stderr)
    check("a shipped file is copied byte for byte", (fw / "agent" / "m7.py").read_text() == "x = 7\n")
    check("a private file is NOT copied", not (fw / "agent" / "roster.json").exists())
    check("the map itself is not copied", not (fw / "framework.map").exists())
    check("a framework file this tree no longer ships is removed", not (fw / "agent" / "gone.py").exists())
    check("the framework's own files are left alone", (fw / "README.md").read_text() == "framework's own\n")
    check("nothing is committed — the change waits in the framework's index",
          "agent/m7.py" in git(fw, "diff", "--cached", "--name-only"))
    commit_all(fw)
    r = run(inst, fw, "--check")
    check("once synced, --check says in step (exit 0)", r.returncode == 0, r.stdout + r.stderr)

    (fw / "README.md").write_text("edited, not committed\n")
    r = run(inst, fw)
    check("a framework with uncommitted work is refused (exit 2), so nothing is buried",
          r.returncode == 2 and "uncommitted" in r.stderr, r.stderr)
    git(fw, "checkout", "-q", "--", "README.md")

    # Assembled at run time: written out whole, this line was itself refused by the framework's real
    # scanner on the first sync (2026-09-21) — the guard doing its job on its own test.
    fake = "xox" + "b-1111111111-2222222222-" + "abcdefghijklmnopqrst"
    (inst / "agent" / "leak.py").write_text(f'TOKEN = "{fake}"\n')
    commit_all(inst)
    r = run(inst, fw)
    check("a shipped file the framework's scanner rejects stops the sync (exit 2)",
          r.returncode == 2 and "secret scanner" in r.stderr, r.stderr)
    check("…and nothing from that run is left behind in the framework",
          not (fw / "agent" / "leak.py").exists() and not git(fw, "status", "--porcelain").strip(),
          git(fw, "status", "--porcelain"))

with tempfile.TemporaryDirectory() as t:
    inst, fw = world(Path(t))
    (fw / ".gitignore").write_text("agent/m3.py\n")
    commit_all(fw)
    r = run(inst, fw)
    check("a shipped file the framework's .gitignore excludes stops the sync, by name (exit 2)",
          r.returncode == 2 and "agent/m3.py" in r.stderr, r.stderr)
    check("…and leaves no ignored copy behind, where nothing would ever notice it",
          not (fw / "agent" / "m3.py").exists() and not git(fw, "status", "--porcelain").strip())

with tempfile.TemporaryDirectory() as t:
    inst, fw = world(Path(t))
    (inst / "stray.txt").write_text("no rule covers me\n")
    commit_all(inst)
    r = run(inst, fw, "--check")
    check("a file no line of the map covers stops even --check (exit 2), by name",
          r.returncode == 2 and "stray.txt" in r.stderr, r.stderr)

with tempfile.TemporaryDirectory() as t:
    inst, fw = world(Path(t), extra_map="agent/m1.py  private\n")
    r = run(inst, fw, "--check")
    check("a private line with no reason is refused", r.returncode == 2 and "no reason" in r.stderr, r.stderr)

with tempfile.TemporaryDirectory() as t:
    inst, fw = world(Path(t), extra_map="agent/m1.py  ship  because it is useful\n")
    r = run(inst, fw, "--check")
    check("a ship line carrying a reason instead of a path is refused",
          r.returncode == 2 and "not a destination path" in r.stderr, r.stderr)


print("\nmoses_env — the settings every entry point reads")
PROBE = """
import os, sys, json
sys.path.insert(0, sys.argv[1])
import moses_env as e
print(json.dumps({"home": str(e.HOME), "a": e.setting("T_A", "dflt"), "b": e.setting("T_B", "dflt"),
                  "c": e.setting("T_C", "dflt"), "q": e.setting("T_Q"), "mem": str(e.MEMORY)}))
"""
with tempfile.TemporaryDirectory() as t:
    home = Path(t) / "home"
    (home / ".config" / "moses").mkdir(parents=True)
    (home / ".config" / "moses" / "moses.env").write_text(
        "# comment\nT_A=from-file\nT_B=from-file\nT_Q=\"quoted value\"\nexport T_X=1\nnot a setting\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MOSES_", "T_"))}
    env.update(HOME=str(home), T_B="from-env")
    r = subprocess.run([sys.executable, "-c", PROBE, str(HERE / "lib")], capture_output=True, text=True, env=env)
    import json
    got = json.loads(r.stdout) if r.returncode == 0 else {}
    check("the settings file is read from the operator's home", got.get("a") == "from-file", r.stderr)
    check("the process environment wins over the file", got.get("b") == "from-env")
    check("the caller's default is the last resort", got.get("c") == "dflt")
    check("quotes around a value are removed", got.get("q") == "quoted value")
    check("the memory directory follows the operator's home", got.get("mem") == str(home / ".claude/memory"))

    # The shell half must give the same answers — two readers that disagree are the drift this replaced.
    sh = subprocess.run(["bash", "-c", f'. "{HERE}/lib/moses-env.sh"; printf "%s|%s|%s|%s" "$T_A" "$T_B" "$T_Q" "$MOSES_MEMORY_DIR"'],
                        capture_output=True, text=True, env=env)
    check("moses-env.sh gives the same answers as moses_env.py",
          sh.stdout == f"from-file|from-env|quoted value|{home}/.claude/memory", sh.stdout + sh.stderr)

    # As root with no operator, both halves must STOP rather than guess. Simulated: the check is on
    # euid, so the probe replaces os.geteuid before importing.
    ROOT_PROBE = ("import os, sys; os.geteuid = lambda: 0; sys.path.insert(0, sys.argv[1])\n"
                  "import moses_env\nprint(moses_env.HOME)\n")
    renv = {k: v for k, v in env.items() if k not in ("SUDO_USER", "MOSES_OPERATOR_USER", "MOSES_HOME")}
    r = subprocess.run([sys.executable, "-c", ROOT_PROBE, str(HERE / "lib")], capture_output=True, text=True, env=renv)
    import pwd as _pwd
    if Path("/etc/moses/operator").exists():
        check("as root with no operator (skipped: /etc/moses/operator exists here)", True)
    else:
        want = _pwd.getpwuid(1000).pw_dir
        check("as root with nothing declared, moses_env uses account 1000 — never root's own home",
              r.stdout.strip() == want, r.stdout + r.stderr)
        check("…and SAYS it assumed so, every time", "using account 1000" in r.stderr, r.stderr)
    renv["SUDO_USER"] = _pwd.getpwuid(os.getuid()).pw_name
    r = subprocess.run([sys.executable, "-c", ROOT_PROBE, str(HERE / "lib")], capture_output=True, text=True, env=renv)
    check("as root under sudo, the person who ran sudo is the operator, silently",
          r.stdout.strip() == _pwd.getpwuid(os.getuid()).pw_dir and not r.stderr.strip(), r.stdout + r.stderr)

print("\n" + ("all good" if not fails else f"{fails} failed"))
sys.exit(1 if fails else 0)
