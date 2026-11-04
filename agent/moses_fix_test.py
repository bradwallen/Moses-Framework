#!/usr/bin/env python3
"""moses_fix_test — moses-fix publishes only what passes its gates, and says why when it will not.

    ~/moses-venv/bin/python agent/moses_fix_test.py

Builds a throwaway framework checkout whose "GitHub" is a local bare repository, a stand-in secret
scanner and a stand-in sync, then tries to get private topics, secrets, a dirty checkout and failing
tests past it. Nothing here reaches GitHub, and the real framework checkout is never touched.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIX = HERE / "moses-fix"
fails = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  —  {detail}" if detail and not cond else ""))
    if not cond:
        fails += 1


def sh(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


SCAN = """#!/usr/bin/env bash
# stand-in secret scanner: refuses any tracked or working file containing FAKE_SECRET
if grep -rq FAKE_SECRET --exclude-dir=.git --exclude-dir=tools .; then echo "secret found"; exit 1; fi
exit 0
"""


class World:
    def __init__(self, tmp: Path, words: str | None = "frobnicorp\n[a-z0-9._%+-]+@mailbox\\.test\n"):
        self.tmp = tmp
        self.origin = tmp / "origin.git"
        self.fw = tmp / "fw"
        self.state = tmp / "state"
        sh("git", "init", "-q", "--bare", "-b", "main", str(self.origin))
        sh("git", "clone", "-q", str(self.origin), str(self.fw))
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        (self.fw / "tools").mkdir()
        (self.fw / "tools" / "scan-secrets.sh").write_text(SCAN)
        (self.fw / "agent").mkdir()
        # Already public before this change: an old mention must not block every later publish.
        (self.fw / "README.md").write_text("Built by Brad; he once worked at Frobnicorp.\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "init")
        self.git("push", "-q", "origin", "HEAD:main")
        self.words = tmp / "private-words"
        if words is not None:
            self.words.write_text("# test list\n" + words)
        self.sync = tmp / "fake-sync"
        self.sync_does("sys.exit(0)")

    def git(self, *a: str) -> str:
        return sh("git", "-C", str(self.fw), *a)

    def on_github(self) -> list[str]:
        return sh("git", "--git-dir", str(self.origin), "log", "--format=%s", "main").splitlines()

    def commit(self, path: str, text: str, msg: str) -> None:
        (self.fw / path).write_text(text)
        self.git("add", "--", path)
        self.git("commit", "-q", "-m", msg)

    def sync_does(self, body: str) -> None:
        self.sync.write_text(f"#!/usr/bin/env python3\nimport os, sys\nFW = sys.argv[sys.argv.index('--framework') + 1]\n{body}\n")

    def run(self, op: str, *extra: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "MOSES_FRAMEWORK": str(self.fw), "MOSES_PRIVATE_WORDS": str(self.words),
               "MOSES_FRAMEWORK_SYNC": str(self.sync), "MOSES_STATE": str(self.state),
               "MOSES_STANDUPS": str(self.tmp / "standups"), "MOSES_FIX_NOTIFY_LOG": str(self.tmp / "posts.jsonl")}
        return subprocess.run([str(FIX), op, *extra], env=env, capture_output=True, text=True, timeout=120)

    def standup_posted(self, name: str, channel: str, ts: str) -> None:
        d = self.tmp / "standups" / name
        d.mkdir(parents=True)
        (d / "slack.json").write_text(json.dumps({"ok": True, "channel": channel, "ts": ts}))

    def posts(self) -> list[dict]:
        f = self.tmp / "posts.jsonl"
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def log(self) -> list[dict]:
        f = self.state / "fixes.jsonl"
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []


print("framework-push")
with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    r = w.run("framework-push")
    check("nothing ahead is done, and says so", r.returncode == 0 and "nothing to publish" in r.stdout, r.stdout + r.stderr)

    w.commit("agent/a.py", "print('hello')\n", "a clean change")
    r = w.run("framework-push")
    check("a clean commit is published", r.returncode == 0 and w.on_github()[0] == "a clean change", r.stdout + r.stderr)
    check("… even though the tree already mentions a private word elsewhere", "DONE" in r.stdout, r.stdout)
    check("… and the attempt is logged as done", w.log()[-1]["result"] == "done", str(w.log()))

    w.commit("agent/b.py", "# talked to the Frobnicorp team about this\n", "mentions work")
    r = w.run("framework-push")
    check("an ADDED private topic is refused", r.returncode == 1 and "private topic" in r.stdout, r.stdout + r.stderr)
    check("… naming the file and the word", "agent/b.py" in r.stdout and "Frobnicorp" in r.stdout, r.stdout)
    check("… and GitHub did not get it", w.on_github()[0] == "a clean change", str(w.on_github()))
    check("… and the refusal is logged", w.log()[-1]["result"] == "refused", str(w.log()))

with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    w.commit("agent/c.py", "x = 'someone@mailbox.test'\n", "an address")
    r = w.run("framework-push")
    check("an address matching an email rule is refused", r.returncode == 1 and "mailbox.test" in r.stdout, r.stdout)

with tempfile.TemporaryDirectory() as t:
    w = World(Path(t), words=None)
    w.commit("agent/a.py", "print('hello')\n", "a clean change")
    r = w.run("framework-push")
    check("no private-topics list: COULD NOT RUN, never published",
          r.returncode == 2 and "COULD NOT RUN" in r.stdout and w.on_github() == ["init"], r.stdout + str(w.on_github()))

with tempfile.TemporaryDirectory() as t:
    w = World(Path(t), words="# only comments\n")
    w.commit("agent/a.py", "print('hello')\n", "a clean change")
    r = w.run("framework-push")
    check("an empty list counts as could-not-check too", r.returncode == 2 and w.on_github() == ["init"], r.stdout)

with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    w.commit("agent/a.py", "token = 'FAKE_SECRET'\n", "a secret")
    r = w.run("framework-push")
    check("the secret scanner's no stops the push", r.returncode == 1 and "secret scanner" in r.stdout
          and w.on_github() == ["init"], r.stdout)

with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    w.commit("agent/a.py", "print('hello')\n", "a clean change")
    (w.fw / "agent" / "half.py").write_text("work in progress\n")
    r = w.run("framework-push")
    check("a dirty checkout is refused, not swept in", r.returncode == 1 and "uncommitted" in r.stdout
          and w.on_github() == ["init"], r.stdout)

print("framework-sync")
PASSING_TEST = "print('ok')\\n"
with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    w.sync_does(f"open(os.path.join(FW, 'agent', 'new.py'), 'w').write('def f(): return 1\\n')\n"
                f"open(os.path.join(FW, 'agent', 'ok_test.py'), 'w').write(\"{PASSING_TEST}\")")
    r = w.run("framework-sync")
    check("a clean sync is committed and published", r.returncode == 0 and w.on_github()[0].startswith("Sync from the running tree"),
          r.stdout + r.stderr + str(w.on_github()))
    check("… names what it copied and that the tests ran", "new.py" in r.stdout and "passed" in r.stdout, r.stdout)
    check("… and leaves the checkout clean", w.git("status", "--porcelain").strip() == "", w.git("status", "--porcelain"))

with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    w.sync_does("open(os.path.join(FW, 'agent', 'new.py'), 'w').write('# ask brad.example@mailbox.test\\n')")
    r = w.run("framework-sync")
    check("a sync that would publish a private topic is refused before committing",
          r.returncode == 1 and "private topic" in r.stdout and w.git("log", "--format=%s").splitlines()[0] == "init",
          r.stdout)
    check("… and GitHub did not get it", w.on_github() == ["init"], str(w.on_github()))

with tempfile.TemporaryDirectory() as t:
    # The REAL sync stages what it copies. A check that read only unstaged changes saw nothing here and
    # committed private words locally; the push-time check was all that stopped them (trial, 2026-11-03).
    w = World(Path(t))
    w.sync_does("import subprocess\n"
                "open(os.path.join(FW, 'agent', 'staged.py'), 'w').write('# from the Frobnicorp days\\n')\n"
                "open(os.path.join(FW, 'README.md'), 'a').write('mail someone@mailbox.test\\n')\n"
                "subprocess.run(['git', '-C', FW, 'add', '-A'], check=True)")
    r = w.run("framework-sync")
    check("a STAGED new file with a private topic is refused before committing",
          r.returncode == 1 and "staged.py" in r.stdout and w.git("log", "--format=%s").splitlines()[0] == "init", r.stdout)
    check("… and so is a STAGED change to an existing file", "README.md" in r.stdout, r.stdout)

with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    w.sync_does("print('framework.map has no rule for agent/brand-new.py'); sys.exit(2)")
    r = w.run("framework-sync")
    check("the sync's own refusal is passed on in its words", r.returncode == 1 and "brand-new.py" in r.stdout, r.stdout)

with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    w.sync_does("open(os.path.join(FW, 'agent', 'bad_test.py'), 'w').write('raise SystemExit(1)\\n')")
    r = w.run("framework-sync")
    check("failing framework tests stop the commit", r.returncode == 1 and "tests failed" in r.stdout
          and w.on_github() == ["init"] and w.git("log", "--format=%s").splitlines()[0] == "init", r.stdout)

with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    r = w.run("framework-sync")
    check("nothing to copy and nothing to push is done", r.returncode == 0 and "already in step" in r.stdout, r.stdout + r.stderr)

print("--notify: the result arrives in the standup thread, not by Moses remembering")
with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    w.standup_posted("20261102-080000-1", "C_OLD", "111.1")
    w.standup_posted("20261103-080000-2", "C_OPS", "222.2")
    w.commit("agent/a.py", "print('hello')\n", "a clean change")
    r = w.run("framework-push", "--notify")
    p = w.posts()
    check("a finished fix posts once", len(p) == 1, str(p) + r.stdout)
    check("… as a reply under the LATEST standup", p and p[0]["channel"] == "C_OPS" and p[0]["thread_ts"] == "222.2", str(p))
    check("… saying it was done and what", p and "DONE" in p[0]["text"] and "a clean change" in p[0]["text"], str(p))

with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    w.standup_posted("20261103-080000-2", "C_OPS", "222.2")
    w.commit("agent/b.py", "# Frobnicorp\n", "private")
    w.run("framework-push", "--notify")
    p = w.posts()
    check("a refusal is posted too, with its reason", p and "REFUSED" in p[0]["text"] and "private topic" in p[0]["text"], str(p))

with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    w.commit("agent/a.py", "print('hello')\n", "a clean change")
    r = w.run("framework-push", "--notify")
    check("no standup on record: says it could not report, still does the fix",
          "could not report" in r.stdout and w.on_github()[0] == "a clean change", r.stdout)

with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    w.standup_posted("20261103-080000-2", "C_OPS", "222.2")
    w.commit("agent/a.py", "print('hello')\n", "a clean change")
    w.run("framework-push")
    check("without --notify nothing is posted", w.posts() == [], str(w.posts()))

print("which python runs the framework's tests")
fix_mod_spec = __import__("importlib.util").util
_spec = fix_mod_spec.spec_from_loader("moses_fix", __import__("importlib.machinery").machinery.SourceFileLoader("moses_fix", str(FIX)))
F = fix_mod_spec.module_from_spec(_spec); _spec.loader.exec_module(F)
with tempfile.TemporaryDirectory() as t:
    F.FRAMEWORK = Path(t)
    os.environ.pop("MOSES_TEST_PYTHON", None)
    home_venv = Path.home() / "moses-venv" / "bin" / "python"
    check("no framework venv: the Moses venv runs them, not the bare interpreter",
          F.test_python() == str(home_venv) if home_venv.exists() else F.test_python() == __import__("sys").executable,
          F.test_python())
    (Path(t) / ".venv" / "bin").mkdir(parents=True)
    (Path(t) / ".venv" / "bin" / "python").write_text("")
    check("a framework venv wins when there is one", F.test_python() == str(Path(t) / ".venv" / "bin" / "python"), F.test_python())

print("the list")
with tempfile.TemporaryDirectory() as t:
    w = World(Path(t))
    r = w.run("rm-rf")
    check("an op not on the list is refused by name", r.returncode == 1 and "not on the list" in r.stderr, r.stderr)
    check("… and logged", w.log() and w.log()[-1]["result"] == "refused", str(w.log()))

print(f"\n{'FAILED' if fails else 'OK'}{f' — {fails} failure(s)' if fails else ''}")
raise SystemExit(1 if fails else 0)
