#!/usr/bin/env python3
"""The one runner (M20): what it promises, and proof that nothing else builds a command line.

    python3 agent/claude_runner_test.py

Every promise in claude_runner.py's docstring is checked here, and the ones that are guards are shown
going red: a reason too short to load, a stray launcher the scan must see, lists that disagree.
Nothing here runs a model. The live check, what the CLI actually hands each profile, is
agent/tool-wall-probe.
"""
import ast
import io
import json
import os
import subprocess
import sys
import tempfile
import tokenize
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("MOSES_STATE", tempfile.mkdtemp(prefix="runner-test-"))
import claude_runner as R  # noqa: E402

REPO = HERE.parent
# Run the wrapper EXACTLY as knight-run and explore.ts do: as an executable, by path. This used to go
# through `python claude-run`, which passed while the file had no execute bit, and that would have
# failed every real Knight job and every exploration on its first line (found 2026-09-14).
RUN = [str(HERE / "claude-run")]
fails = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  — {detail}"))
    if not cond:
        fails.append(label)


def values(argv, flag):
    """What follows a flag, up to the next flag."""
    if flag not in argv:
        return None
    i, out = argv.index(flag) + 1, []
    while i < len(argv) and not argv[i].startswith("--"):
        out.append(argv[i])
        i += 1
    return out


def sample(app):
    return R.build_argv(app, "say ok", **({"mcp_config": R.NO_MCP} if R.profile(app).mcp_config == "" else {}))


def refused(call) -> bool:
    try:
        call()
        return False
    except R.ProfileError:
        return True


print("every profile is locked down, with no opt-out")
for app, p in R.profiles().items():
    argv = sample(app)
    check(f"{app}: --restricted", "--restricted" in argv)
    check(f"{app}: --strict-mcp-config, with a config", "--strict-mcp-config" in argv and "--mcp-config" in argv)
    check(f"{app}: --tools is exactly what it offers", values(argv, "--tools") == [",".join(p.offered())],
          str(values(argv, "--tools")))
    approved, offered = values(argv, "--allowedTools") or [], set(p.offered())
    covered = all(t in approved or any(a.startswith(f"{t}(") for a in approved) for t in offered)
    inside = all(a in offered or a.startswith("mcp__") or (a.startswith("Bash(") and "Bash" in offered)
                 for a in approved)
    check(f"{app}: what it offers and what it approves are the same tools", covered and inside,
          f"offered={sorted(offered)} approved={approved[:6]}")
    check(f"{app}: never --bare or --dangerously-skip-permissions",
          not {"--bare", "--dangerously-skip-permissions"} & set(argv))
    check(f"{app}: a grant carries a reason", app in R.GRANTS_NOTHING or len(R.APPS[app][0]) >= R.MIN_REASON)

print("\nthe profiles grant what the launchers granted before M20")
import conversation as C  # noqa: E402
for app, want in (("moses-chat", C.tools_for(False)), ("moses-chat-directed", C.tools_for(True)),
                  ("moses-chat-reading", C.tools_for(True, True))):
    check(f"{app} approves exactly tools_for's list", set(R.profile(app).approved()) == set(want.split(",")))
check("an ordinary chat turn is offered no built-in", R.profile("moses-chat").offered() == [])
check("a link-reading turn is offered exactly WebFetch", R.profile("moses-chat-reading").offered() == ["WebFetch"])
check("a link-reading turn has no acting tool",
      not any(C._qualified(t) in R.profile("moses-chat-reading").approved() for t in C.ACT_TOOLS))
check("the diagnosis is offered and approved nothing",
      R.profile("moses-diagnosis").offered() == [] and R.profile("moses-diagnosis").approved() == [])
g = R._guards()
kb, rv = R.profile("knight-builder"), R.profile("zryachiy-reviewer")
check("the builder is offered exactly KNIGHT_TOOLS", set(kb.offered()) == set(g["KNIGHT_TOOLS"].split(",")))
check("every Bash command in KNIGHT_ALLOW is approved, and no others",
      {f"Bash({c})" for c in kb.bash} == {a for a in g["KNIGHT_ALLOW"] if a.startswith("Bash(")})
check("the builder's deny list is KNIGHT_DENY", list(kb.deny) == g["KNIGHT_DENY"])
check("the builder auto-accepts edits, as before", "acceptEdits" in values(sample("knight-builder"), "--permission-mode"))
check("the reviewer is offered exactly KNIGHT_REVIEW_TOOLS", set(rv.offered()) == set(g["KNIGHT_REVIEW_TOOLS"].split(",")))
check("the reviewer approves everything in KNIGHT_REVIEW_ALLOW", set(g["KNIGHT_REVIEW_ALLOW"]) <= set(rv.approved()))
check("the explorer is offered no built-in", R.profile("zryachiy-explorer").offered() == [])

print("\na caller can narrow, never widen")
for label, call in [
    ("chat cannot be pointed at another MCP server",
     lambda: R.build_argv("moses-chat", "x", mcp_config='{"mcpServers":{"other":{}}}')),
    ("the builder cannot be given another directory (his sandbox is his clone)",
     lambda: R.build_argv("knight-builder", "x", add_dirs=["/etc"])),
    ("the builder takes no system prompt", lambda: R.build_argv("knight-builder", "x", system_prompt="y")),
    ("the explorer cannot approve a built-in",
     lambda: R.build_argv("zryachiy-explorer", "x", mcp_config=R.NO_MCP, approve=["Bash"])),
    ("the explorer cannot approve the browser tool that runs code outside the browser",
     lambda: R.build_argv("zryachiy-explorer", "x", mcp_config=R.NO_MCP,
                          approve=["mcp__traveler__browser_run_code_unsafe"])),
    ("the explorer cannot approve a non-browser tool", lambda: R.build_argv(
        "zryachiy-explorer", "x", mcp_config=R.NO_MCP, approve=["mcp__moses__knight_start"])),
    ("the explorer cannot run without its own servers", lambda: R.build_argv("zryachiy-explorer", "x")),
    ("chat cannot approve anything extra", lambda: R.build_argv("moses-chat", "x", approve=["mcp__x__browser_click"])),
    ("an unknown app is refused", lambda: R.build_argv("somebody", "x")),
]:
    check(label, refused(call))
_ex = R.build_argv("zryachiy-explorer", "x", mcp_config=R.NO_MCP, approve=["mcp__traveler__browser_click"])
check("the explorer may approve a browser tool on its own server", "mcp__traveler__browser_click" in _ex)
check("the reviewer may be given its job folder",
      values(R.build_argv("zryachiy-reviewer", "x", add_dirs=["/tmp/job"]), "--add-dir") == ["/tmp/job"])
check("anyone may refuse more", "ExtraDeny" in (values(R.build_argv("moses-chat", "x", deny=["ExtraDeny"]),
                                                       "--disallowedTools") or []))

print("\na grant must say why, or the runner does not load")
check("a short reason is refused", refused(lambda: R.check_reasons({"x": ("too short", None)})))
check("a long reason passes", not refused(lambda: R.check_reasons({"x": ("a reason that says why it needs it", None)})))
_src = (HERE / "claude_runner.py").read_text()
_old = ('("drives the sandbox\'s browsers through their own MCP servers, with no "\n'
        '                          "built-in tool at all", _explorer)')
check("(the text this breaks is where the test expects it)", _src.count(_old) == 1)
with tempfile.TemporaryDirectory() as _d:
    Path(_d, "claude_runner.py").write_text(_src.replace(_old, '("browsers", _explorer)'))
    # The copy finds its settings the way the real one does: lib/ beside it.
    Path(_d, "lib").mkdir()
    Path(_d, "lib", "moses_env.py").write_text((HERE / "lib" / "moses_env.py").read_text())
    _r = subprocess.run([sys.executable, "-c", "import claude_runner"], cwd=_d, capture_output=True, text=True)
check("a runner whose grant lost its reason refuses to import",
      _r.returncode != 0 and "ProfileError" in _r.stderr, _r.stderr[-200:])

print("\nguards.env's two lists cannot disagree")
check("offering Bash with no Bash command approved is refused", refused(lambda: R._split_grants("t", "Read,Bash", ["Read"])))
check("approving Bash commands without offering Bash is refused", refused(lambda: R._split_grants("t", "Read", ["Bash(ls:*)"])))
check("approving a tool that is not offered is refused", refused(lambda: R._split_grants("t", "Read", ["Write"])))
check("a pair that agrees is accepted", R._split_grants("t", "Read,Bash", ["Read", "Bash(ls:*)"]) == (("Read",), ("ls:*",)))

print("\na refused tool is not a success")
check("finished, nothing refused: ok", R.classify({"is_error": False, "result": "x", "permission_denials": []}) == "ok")
_deg = {"is_error": False, "result": "x", "permission_denials": [{"tool_name": "WebFetch", "tool_input": {}}]}
check("finished with a refusal: degraded, a third state", R.classify(_deg) == "degraded")
check("and it names what was refused", R.denials(_deg) == ["WebFetch"])
check("an error result: failed", R.classify({"is_error": True}) == "failed")
check("no result at all: failed", R.classify(None) == "failed" and R.classify("junk") == "failed")
with tempfile.TemporaryDirectory() as _d:
    Path(_d, "r.json").write_text(json.dumps(_deg))
    _out = subprocess.run(RUN + ["--classify", str(Path(_d, "r.json"))], capture_output=True, text=True).stdout.strip()
check("the wrapper classifies a result file for knight-run", _out == "degraded: WebFetch", _out)
_w = subprocess.run(RUN + ["--app", "knight-builder", "--prompt", "x", "--add-dir", "/etc", "--print-argv"],
                    capture_output=True, text=True)
check("the wrapper refuses a widened request, exit 64, nothing run", _w.returncode == 64 and "refused" in _w.stderr)
_p = subprocess.run(RUN + ["--app", "knight-builder", "--prompt", "x", "--print-argv"], capture_output=True, text=True)
check("the wrapper builds exactly what the module builds",
      _p.returncode == 0 and json.loads(_p.stdout or "null") == R.build_argv("knight-builder", "x"))

# ── Nothing else builds a command line ───────────────────────────────────────
# Atlas's chokepoint test (2026-09-14): the probe proves each declared profile gets what it declares,
# but it cannot see a launcher that never registered one. This can. A file "builds a command line" if
# it carries a tool-grant flag as a real string (Python) or as a word on a non-comment line (shell).
# Docstrings and comments that merely mention a flag do not count.
print("\nnothing else builds a command line")
GRANT_FLAGS = ("--tools", "--allowedTools", "--allowed-tools", "--disallowedTools", "--disallowed-tools",
               "--permission-mode", "--dangerously-skip-permissions", "--restricted")
RUNNER_FILES = {"agent/claude_runner.py"}
# Each entry is an exception with its reason. The list must shrink: an entry whose file is gone, or
# that no longer builds a command line, fails below just like a new stray launcher does.
ALLOWED = {
    "agent/claude_runner_test.py": "this test names the flags in order to look for them",
    "agent/conversation_test.py": "asserts on the command the runner built for the chat; launches nothing",
    "agent/tool-wall-probe": "fault injection: strips --tools from a runner-built command to prove the probe goes red",
    "knight/test-guards.sh": "tests Knight's deny list on its own against the real CLI; it runs model turns, so by hand",
    "knight/test-review.sh": "asserts on the reviewer command the runner built (claude-run --print-argv); launches nothing",
}
SKIP_DIRS = {".git", "__pycache__", "venv", ".venv", "node_modules", "repo", "jobs"}


def _is_python(path: Path, text: str) -> bool:
    return path.suffix == ".py" or text.startswith("#!") and "python" in text.splitlines()[0]


def builds_argv(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return False
    if _is_python(path, text):
        try:
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type == tokenize.STRING:
                    try:
                        v = ast.literal_eval(tok.string)
                    except (ValueError, SyntaxError):
                        continue
                    if isinstance(v, str) and (v in GRANT_FLAGS or v.split("=")[0] in GRANT_FLAGS):
                        return True
        except (tokenize.TokenError, IndentationError, SyntaxError):
            return False
        return False
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        s = s.split(" #")[0]
        for f in GRANT_FLAGS:
            i = s.find(f)
            while i >= 0:
                before = s[i - 1] if i else " "
                after = s[i + len(f)] if i + len(f) < len(s) else " "
                if not (before.isalnum() or before in "-_") and not (after.isalnum() or after in "-_"):
                    return True
                i = s.find(f, i + 1)
    return False


def scan(root: Path, dirs=("agent", "knight", "mcp")) -> tuple[set[str], int]:
    found, seen = set(), 0
    for top in dirs:
        base = root / top
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                p = Path(dirpath, name)
                if p.suffix in (".py", ".sh", ".env") or (not p.suffix and p.is_file()):
                    seen += 1
                    # Tool-grant flags OR a print-mode launch. The second is how moses-liveness went
                    # unseen through M20 (2026-09-15): it passed no flags at all.
                    if builds_argv(p) or R.launches_cli(p):
                        found.add(str(p.relative_to(root)))
    return found, seen


# The detector must be seen to see. A stray launcher in each language is caught; a comment and a
# docstring that only mention a flag are not.
with tempfile.TemporaryDirectory() as _d:
    _root = Path(_d)
    (_root / "agent").mkdir()
    (_root / "agent/stray.py").write_text('argv = [CLI, "-p", x, "--allowedTools", "Bash"]\n')
    (_root / "agent/stray.sh").write_text('#!/usr/bin/env bash\nclaude -p "$x" --tools Bash\n')
    (_root / "agent/mentions.py").write_text('"""Uses --tools and --restricted, via the runner."""\n# --tools\n')
    (_root / "agent/mentions.sh").write_text('#!/usr/bin/env bash\n# claude -p --tools Bash\necho ok\n')
    # THE ONE THAT GOT THROUGH: the liveness probe's launch as it was until 2026-09-15 (commit 71a4e32),
    # verbatim. No tool flags at all, which the first version of this scan could not see. Written in
    # rather than read from git history, because the public framework's history does not have it.
    _old_live = """#!/usr/bin/env bash
probe() {
  local out rc
  local CLI="${MOSES_CHAT_CLI:-$HOME/.local/bin/claude}"
  if [ -n "$CFG" ]; then
    out=$(cd /tmp && timeout "$TIMEOUT" env CLAUDE_CONFIG_DIR="$CFG" "$CLI" --print "reply with the single word: alive" 2>&1); rc=$?
  else
    out=$(cd /tmp && timeout "$TIMEOUT" "$CLI" --print "reply with the single word: alive" 2>&1); rc=$?
  fi
}
"""
    (_root / "agent/old-liveness").write_text(_old_live)
    (_root / "agent/stray_print.py").write_text('CLI = "/home/brad/.local/bin/claude"\nsubprocess.run([CLI, "--print", q])\n')
    (_root / "agent/stray_print.sh").write_text('#!/usr/bin/env bash\nout=$(claude --print "hi")\n')
    # And three that are not launches: a version check, a script's own --print option, a docstring.
    (_root / "agent/version.sh").write_text('#!/usr/bin/env bash\n"$KNIGHT_CLI" --version\n[ "$MODE" = "--print" ] && echo x\n')
    (_root / "agent/doc.py").write_text('"""`claude -p` takes one prompt."""\nCLI = "/home/brad/.local/bin/claude"\n')
    _found, _ = scan(_root, dirs=("agent",))
check("the old liveness probe (no tool flags, just --print) is caught",
      bool(_old_live) and "agent/old-liveness" in _found, str(sorted(_found)))
check("so is a print-mode launch in Python and in shell",
      {"agent/stray_print.py", "agent/stray_print.sh"} <= _found, str(sorted(_found)))
check("a version check, a script's own --print option and a docstring are not launches",
      not _found & {"agent/version.sh", "agent/doc.py"}, str(sorted(_found)))
check("the scan catches a stray launcher in Python and in shell",
      _found >= {"agent/stray.py", "agent/stray.sh"}, str(sorted(_found)))
check("and does not fire on a comment or a docstring that mentions a flag",
      not _found & {"agent/mentions.py", "agent/mentions.sh"}, str(sorted(_found)))

found, seen = scan(REPO)
# A floor, not a count: the author's tree has ~130 files here and the framework ~100. Zero is the
# failure this exists for — a scan of nothing reports clean.
check("the scan actually read the tree (a scan of nothing reports clean)", seen > 50, f"{seen} files")
stray = sorted(found - RUNNER_FILES - set(ALLOWED))
check("no file outside the runner builds a command line", not stray,
      "build it with claude_runner (or claude-run) instead: " + ", ".join(stray))
stale = sorted(f for f in ALLOWED if f not in found)
check("every allowlisted exception is still true (the list only shrinks)", not stale,
      "remove from ALLOWED: " + ", ".join(stale))
check("every allowlisted exception says why", all(len(r) >= 20 for r in ALLOWED.values()))
check("the runner itself is where the flags are", RUNNER_FILES <= found)

print(f"\n  {len(fails)} failed" if fails else "\n  all good")
sys.exit(1 if fails else 0)
