#!/usr/bin/env python3
"""moses-new-target builds nothing without a code, adopts nothing that exists, and lands on a branch.

    python3 agent/moses_new_target_test.py

The REAL script against the REAL helper (unprivileged, scratch seed) and real git, in a scratch
directory. Knight is a stub that records what it was asked, because starting him costs a job; what
is under test is what this script hands him. Then each guard is removed and its check must go red.
"""
import base64
import hashlib
import hmac
import json
import re
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "moses-new-target"
HELPER = HERE / "moses-mfa"
T0 = 1_800_000_000.0
SECRET = base64.b32encode(b"new-target-test-seed").decode().rstrip("=")
PID = "example-app"


def code_at(t: float) -> str:
    key = base64.b32decode(SECRET + "=" * (-len(SECRET) % 8))
    mac = hmac.new(key, struct.pack(">Q", int(t // 30)), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    return str((struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000).zfill(6)


def suite(script: Path, verbose: bool) -> tuple[list[str], list[str]]:
    passed: list[str] = []
    failed: list[str] = []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        if verbose:
            print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' — ' + str(detail)[-400:]}")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "seed").write_text(SECRET + "\n")
        moses = tmp / "moses"                                    # stands in for the Moses tree
        targets = moses / "knight" / "targets"
        targets.mkdir(parents=True)
        (targets / "viatica.env").write_text("KT_SOURCE=/x\n")
        gitenv = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                  "GIT_COMMITTER_EMAIL": "t@t"}
        env0 = {**{k: v for k, v in os.environ.items() if not k.startswith(("MOSES_", "KNIGHT_"))}, **gitenv}
        g = lambda *a, cwd=moses: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, env=env0)
        g("init", "-q", "-b", "main")
        g("add", ".")
        g("commit", "-q", "-m", "seed")
        (moses / "someone-elses-work.txt").write_text("uncommitted, and not ours to commit\n")
        projects = tmp / "Projects"
        projects.mkdir()
        reg = tmp / "projects.json"
        reg.write_text(json.dumps({"projects": [
            {"id": PID, "name": "Listings finder", "rank": 13, "status": "active", "stage": "scope",
             "kind": "internal", "milestones": [], "ideas": [],
             "scope": "Aggregate GIS analyst internships for summer 2027, weighted toward cartography."},
            {"id": "other", "name": "Other", "rank": 14, "status": "active", "stage": "scope",
             "kind": "internal", "milestones": [], "ideas": [], "scope": "Something else."},
        ]}))
        calls = tmp / "knight-calls.jsonl"
        knight = tmp / "knight"
        knight.write_text("#!/usr/bin/env python3\nimport json,sys,os\n"
                          f"open({str(calls)!r},'a').write(json.dumps(sys.argv[1:])+'\\n')\n"
                          "if sys.argv[1]=='provision' and os.environ.get('FAKE_PROVISION_FAIL'):\n"
                          "    print('RED: gate failed'); sys.exit(1)\n"
                          "print('started  20270115-020000-1')\n"
                          "print('Check with: knight status 20270115-020000-1')\n")
        knight.chmod(0o755)
        env = {**env0, "MOSES_MFA_CMD": str(HELPER), "MOSES_MFA_SEED": str(tmp / "seed"),
               "MOSES_MFA_STATE": str(tmp / "mfa"), "MOSES_MFA_NOW": str(T0),
               "KNIGHT_BIN": str(knight), "KNIGHT_TARGETS": str(targets),
               "MOSES_PROJECTS_DIR": str(projects), "MOSES_REGISTRY": str(reg),
               "MOSES_MCP_DIR": str(HERE.parent / "mcp")}

        def build(pid=PID, **extra):
            return subprocess.run([str(script), pid], capture_output=True, text=True, env={**env, **extra}, timeout=120)

        def authorize(pid=PID, t=T0):
            m = lambda *a, stdin="": subprocess.run([str(HELPER), *a], input=stdin, capture_output=True, text=True,
                                                    env={**env, "MOSES_MFA_NOW": str(t)})
            c = json.loads(m("challenge", "new-build", pid).stdout)["challenge"]
            return m("verify", c, stdin=code_at(t)).returncode == 0

        def knight_calls():
            return [json.loads(x) for x in calls.read_text().splitlines()] if calls.exists() else []

        # ── No code, no build ────────────────────────────────────────────
        r = build()
        check("with no authorization, nothing is created",
              r.returncode == 1 and not (projects / PID).exists() and not (targets / f"{PID}.env").exists()
              and knight_calls() == [], r.stdout + r.stderr)

        # ── A code for ANOTHER project does not build this one ───────────
        authorize("other", t=T0)
        r = build()
        check("an authorization for another project builds nothing here",
              r.returncode == 1 and not (projects / PID).exists(), r.stdout)

        # ── The real thing ───────────────────────────────────────────────
        authorize(PID, t=T0 + 30)
        r = build()
        src = projects / PID
        log = subprocess.run(["git", "-C", str(src), "log", "--oneline"], capture_output=True, text=True).stdout
        check("an authorized build makes the directory under git, with one commit",
              r.returncode == 0 and (src / ".git").is_dir() and len(log.splitlines()) == 1, r.stdout + r.stderr)
        check("the starting gate and prep are executable", os.access(src / "gate.sh", os.X_OK) and os.access(src / "prep.sh", os.X_OK))
        envtext = (targets / f"{PID}.env").read_text() if (targets / f"{PID}.env").exists() else ""
        # The ASSIGNMENT, not the text: the file's own comment says "KT_PUSH_MODE=branch because…", and a
        # substring check read that comment and passed with the setting itself switched to direct.
        modes = re.findall(r"^KT_PUSH_MODE=(\S+)", envtext, re.M)
        check("the target lands on a branch, never direct", modes == ["branch"], modes)
        check("the target's source is the new directory", f"KT_SOURCE={src}" in envtext, envtext)
        check("an internal project is set to land by itself and keep building",
              re.findall(r"^KT_AUTO_LAND=(\S+)", envtext, re.M) == ["1"]
              and re.findall(r"^KT_CONTINUE=(\S+)", envtext, re.M) == ["1"], envtext[-300:])
        check("Moses's report carries no terminal command", "Check with" not in r.stdout and "started" in r.stdout, r.stdout)
        committed = g("show", "--name-only", "--format=", "HEAD").stdout.split()
        check("the target and brief are committed in the Moses tree, and nothing else",
              sorted(committed) == sorted([f"knight/targets/{PID}.brief", f"knight/targets/{PID}.env"]), committed)
        check("someone else's uncommitted work is left alone",
              "?? someone-elses-work.txt" in g("status", "--porcelain").stdout)
        kc = knight_calls()
        check("Knight is provisioned, then given the first job on THIS target",
              len(kc) == 2 and kc[0] == ["provision", PID] and kc[1][:3] == ["start", "--target", PID]
              and "--acceptance" in kc[1] and "gate.sh" in kc[1][kc[1].index("--acceptance") + 1], kc)
        check("the first job's brief carries the tracker scope", bool(kc) and "weighted toward cartography" in kc[-1][-1], kc)
        p = next(x for x in json.loads(reg.read_text())["projects"] if x["id"] == PID)
        check("the tracker says building, with the repo", p.get("stage") == "building" and p.get("repo") == str(src), p)

        # ── Once only ────────────────────────────────────────────────────
        g("rm", "-q", "--ignore-unmatch", "--", f"knight/targets/{PID}.env", f"knight/targets/{PID}.brief")
        g("commit", "-q", "-m", "test: forget the target")
        (targets / f"{PID}.env").unlink(missing_ok=True)
        import shutil
        shutil.rmtree(src, ignore_errors=True)
        r = build()
        check("the same authorization cannot build twice", r.returncode == 1 and not src.exists(), r.stdout)

        # ── An existing directory is never adopted, and the code is not spent ──
        shutil.rmtree(src, ignore_errors=True)       # a broken copy may have built it again above
        src.mkdir()
        (src / "precious.txt").write_text("Brad's\n")
        authorize(PID, t=T0 + 60)
        r = build()
        st = json.loads(subprocess.run([str(HELPER), "status"], capture_output=True, text=True,
                                       env={**env, "MOSES_MFA_NOW": str(T0 + 61)}).stdout)
        check("an existing directory is refused and left exactly as it was",
              r.returncode == 1 and sorted(x.name for x in src.iterdir()) == ["precious.txt"], r.stdout)
        check("a refusal before anything is made does not spend the code", f"new-build:{PID}" in st["waiting"], st)
        shutil.rmtree(src, ignore_errors=True)
        # The refused run spent nothing, so its authorization is still waiting; spend it the way the
        # real build would, so the provision case below runs on a code of its own.
        subprocess.run([str(script), PID], capture_output=True, text=True, env={**env, "MOSES_MFA_NOW": str(T0 + 61)})
        g("rm", "-q", "--ignore-unmatch", "--", f"knight/targets/{PID}.env", f"knight/targets/{PID}.brief")
        g("commit", "-q", "-m", "test: forget the target again")
        for f in (targets / f"{PID}.env", targets / f"{PID}.brief"):
            f.unlink(missing_ok=True)
        shutil.rmtree(src, ignore_errors=True)
        authorize(PID, t=T0 + 90)

        # ── A provision failure starts no job and says what exists ───────
        calls.unlink(missing_ok=True)
        r = build(FAKE_PROVISION_FAIL="1")
        check("a red provision starts no job and lists what was done",
              r.returncode == 1 and [c[0] for c in knight_calls()] == ["provision"] and "Done before it stopped" in r.stdout,
              r.stdout)
    return passed, failed


MUTANTS = [
    # Two barriers stand here — the authorized check, and consume refusing a non-authorization — so
    # the mutant removes both. Removing one and staying green is defense in depth, not blindness.
    ("no authorization check", [('if rc != 0:\n            raise Stop(f"no unused authorization',
                                 'if False:\n            raise Stop(f"no unused authorization'),
                                ('if rc != 0:\n            raise Stop(f"could not use the authorization',
                                 'if False:\n            raise Stop(f"could not use the authorization')],
     "with no authorization, nothing is created"),
    ("direct landing", "KT_PUSH_MODE=branch\nKT_PUSH_REMOTE", "KT_PUSH_MODE=direct\nKT_PUSH_REMOTE",
     "the target lands on a branch, never direct"),
    # Likewise two: the explicit check, and mkdir refusing a directory that is there.
    ("adopts an existing directory", [("        if src.exists():\n            raise Stop(",
                                       "        if False:\n            raise Stop("),
                                      ("src.mkdir(parents=True)", "src.mkdir(parents=True, exist_ok=True)")],
     "an existing directory is refused and left exactly as it was"),
    ("commits everything", 'git(top, "add", "--", *rel)', 'git(top, "add", "-A")',
     "someone else's uncommitted work is left alone"),
    ("spends the code too early", "        auth = out.strip().splitlines()[-1]\n",
     "        auth = out.strip().splitlines()[-1]\n        run([*MFA_CMD, \"consume\", auth], timeout=30)\n",
     "a refusal before anything is made does not spend the code"),
]

if __name__ == "__main__":
    print("MOSES-NEW-TARGET")
    passed, failed = suite(SCRIPT, True)
    print(f"\n  {len(passed)}/{len(passed) + len(failed)} pass")
    print("\nWATCH EACH GUARD FAIL")
    src = SCRIPT.read_text()
    blind = []
    with tempfile.TemporaryDirectory() as md:
        for mutant in MUTANTS:
            name, edits, expect = (mutant[0], mutant[1], mutant[2]) if len(mutant) == 3 else \
                (mutant[0], [(mutant[1], mutant[2])], mutant[3])
            text = src
            if any(text.count(old) != 1 for old, _ in edits):
                print(f"  FAIL {name}: the sabotage no longer applies — update MUTANTS")
                blind.append(name)
                continue
            for old, new in edits:
                text = text.replace(old, new)
            m = Path(md) / "moses-new-target"
            m.write_text(text)
            m.chmod(0o755)
            # The mutant must find lib/ and mcp/ where the real one does.
            os.makedirs(Path(md) / "lib", exist_ok=True)
            for f in (HERE / "lib").iterdir():
                if f.is_file() and not (Path(md) / "lib" / f.name).exists():
                    (Path(md) / "lib" / f.name).write_bytes(f.read_bytes())
            _, mf = suite(m, False)
            red = expect in mf
            print(f"  {'ok  ' if red else 'FAIL'} {name} → '{expect}' goes red")
            if not red:
                blind.append(name)
    if failed or blind:
        print(f"\nFAILED: {len(failed)} check(s), {len(blind)} guard(s) not watched failing")
        sys.exit(1)
    print(f"\nall {len(passed)} pass, and all {len(MUTANTS)} guards were watched failing")
