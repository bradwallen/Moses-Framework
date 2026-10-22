#!/usr/bin/env python3
"""The iOS/MCP path to a new build: the same code check as Slack, with the code typed in the app.

    mcp/venv/bin/python mcp/new_build_mcp_test.py

Brad, 2026-10-17: Moses should start a build from Slack or from the Claude app over MCP, and a code
typed in the app is acceptable ("codes rotate every 30 seconds"; they are also single-use). This calls
the real `new_build` tool function with the real agent/moses-mfa (unprivileged, scratch seed) and a
stand-in for moses-new-target, which has its own suite.

Two properties the Slack suite cannot show: the tool NEVER raises — a raised call has its arguments,
which can include the code, written to tool-failures.jsonl — and a code opened for one project cannot
build another. Each is broken on purpose at the end and must go red.
"""
import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TMP = Path(tempfile.mkdtemp(prefix="moses-mcp-newbuild-"))
T0 = 1_800_000_000.0
SECRET = base64.b32encode(b"mcp-new-build-seed!!").decode().rstrip("=")
(TMP / "seed").write_text(SECRET + "\n")
(TMP / "targets").mkdir()
(TMP / "fake-new-target").write_text("#!/bin/sh\necho \"BUILT $1\"\n")
(TMP / "fake-new-target").chmod(0o755)
REG = TMP / "projects.json"
REG.write_text(json.dumps({"projects": [
    {"id": "example-app", "name": "Listings finder", "rank": 13, "status": "active",
     "stage": "scope", "kind": "internal", "milestones": [], "ideas": [], "scope": "Aggregate internships."},
    {"id": "other", "name": "Other", "rank": 20, "status": "active", "stage": "scope", "kind": "internal",
     "milestones": [], "ideas": [], "scope": "Something else."},
]}))
os.environ.update({
    "MOSES_STATE": str(TMP / "state"), "MOSES_REGISTRY": str(REG), "KNIGHT_TARGETS": str(TMP / "targets"),
    "MOSES_MFA_CMD": str(ROOT / "agent" / "moses-mfa"), "MOSES_MFA_SEED": str(TMP / "seed"),
    "MOSES_MFA_STATE": str(TMP / "mfa"), "MOSES_MFA_NOW": str(T0),
    "MOSES_NEW_TARGET": str(TMP / "fake-new-target"),
})
sys.path.insert(0, str(HERE))
import mcp_server  # noqa: E402
import new_build   # noqa: E402  (agent/, put on the path by mcp_server)

mcp_server.TOOL_FAILURES = TMP / "tool-failures.jsonl"


def code_at(t: float) -> str:
    key = base64.b32decode(SECRET + "=" * (-len(SECRET) % 8))
    mac = hmac.new(key, struct.pack(">Q", int(t // 30)), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    return str((struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000).zfill(6)


def reset():
    new_build._save_note({})
    for f in (TMP / "mfa").glob("state.json"):
        f.unlink()


def suite(verbose: bool) -> tuple[list[str], list[str]]:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        if verbose:
            print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' — ' + str(detail)[:300]}")

    tool = mcp_server.new_build
    good = code_at(T0)

    reset()
    out = tool("13")
    check("the first call opens a code request and returns the prompt",
          "🔐" in out and "new-build:example-app" in out, out)
    out = tool("13", "12345")
    check("something that is not six digits checks nothing", "isn't a 6-digit code" in out
          and new_build._note().get("challenge"), out)
    out = tool("other", good)
    check("a code sent for another project builds nothing", "not other" in out and "BUILT" not in out, out)
    out = tool("13", good)
    check("the right code authorizes and starts the build for that project",
          "✅ Authorized" in out and "BUILT example-app" in out, out)
    out = tool("13", good)
    check("the same code cannot be used again", "BUILT" not in out, out)

    # A Slack request open, answered from the app: the note says where it was asked, and the code is
    # refused there — one request, one conversation.
    reset()
    new_build.start("13", "C_SLACK", "")
    out = tool("13", code_at(T0 + 60))
    check("a request opened in Slack is not answered from the app", "another conversation" in out, out)

    # What goes public is not set from conversation (review of 2026-10-17): project_update is granted on
    # any human-directed turn, not only the owner's.
    for field in ("publish_path", "deploy"):
        out = mcp_server.project_update("13", field, "evil")
        check(f"conversation cannot set {field}", "not set from conversation" in out, out)
    check("and nothing was written", not next(p for p in json.loads(REG.read_text())["projects"]
                                               if p["id"] == "example-app").get("publish_path"))

    # #2: from the app, Knight is never started without examples on both sides.
    out = mcp_server.knight_start("Restrict the list to GIS analyst roles by title", "Only GIS-titled roles are listed")
    check("knight_start refuses without include/exclude examples", "examples" in out and "Nothing was started" in out, out)

    # The tool never raises: break the module underneath it and the answer is still text.
    reset()
    saved = new_build.answer
    new_build.answer = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        tool("13")
        out = tool("13", code_at(T0 + 90))
        raised = False
    except Exception as e:                                     # noqa: BLE001
        out, raised = repr(e), True
    finally:
        new_build.answer = saved
    logged = mcp_server.TOOL_FAILURES.read_text() if mcp_server.TOOL_FAILURES.exists() else ""
    check("a failure underneath comes back as text, never an exception", not raised and "failed" in out, out)
    check("and no code reaches tool-failures.jsonl", code_at(T0 + 90) not in logged, logged[:200])
    return passed, failed


if __name__ == "__main__":
    print("NEW BUILD OVER MCP")
    passed, failed = suite(True)
    print(f"\n  {len(passed)}/{len(passed) + len(failed)} pass")

    # Watch the two MCP-only guards fail.
    print("\nWATCH EACH GUARD FAIL")
    blind = []
    src = (HERE / "mcp_server.py").read_text()
    mutants = [
        ("the tool raises", "    except Exception as e:                                    # noqa: BLE001 — see above\n"
                            "        return f\"⚠️ The new-build step failed ({type(e).__name__}). Nothing about the code was logged.\"\n",
         "    except ZeroDivisionError:\n        return ''\n",
         "a failure underneath comes back as text, never an exception"),
        ("any project's code", "and nb._project(project).get(\"id\") != note.get(\"project\"):",
         "and False:", "a code sent for another project builds nothing"),
    ]
    for name, old, new, expect in mutants:
        if src.count(old) != 1:
            print(f"  FAIL {name}: the sabotage no longer applies — update this test")
            blind.append(name)
            continue
        ns: dict = {}
        exec(compile(src.replace(old, new), "mcp_server_mutant", "exec"), mcp_server.__dict__)
        try:
            _, mf = suite(False)
        finally:
            exec(compile(src, str(HERE / "mcp_server.py"), "exec"), mcp_server.__dict__)
        red = expect in mf
        print(f"  {'ok  ' if red else 'FAIL'} {name} → '{expect}' goes red")
        if not red:
            blind.append(name)
    if failed or blind:
        print(f"\nFAILED: {len(failed)} check(s), {len(blind)} guard(s) not watched failing")
        sys.exit(1)
    print(f"\nall {len(passed)} pass, and every guard was watched failing")
