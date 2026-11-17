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

# ── After MF8 the registry is Moses's; the operator only reads it (2026-11-11) ──────────────────────
# Every write failed with "Permission denied" (J57). Writes now go through the Moses MCP server's own
# project tools with the operator key. A stand-in server records each call and answers like the real one.
import http.server  # noqa: E402
import os  # noqa: E402
import threading  # noqa: E402

print("a registry this account cannot write: changes go through Moses")
CALLS: list[dict] = []
ANSWER = {"text": "ok from moses", "code": 200}


class FakeMoses(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        CALLS.append({"auth": self.headers.get("Authorization"), "body": body})
        if ANSWER["code"] != 200:
            self.send_response(ANSWER["code"]); self.end_headers(); return
        out = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": ANSWER["text"]}]}}
        data = ("event: message\ndata: " + json.dumps(out) + "\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), FakeMoses)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_port}/mcp"

work = Path(tempfile.mkdtemp(prefix="moses-project-ro-"))
data_dir, peer = work / "moses-data", work / "state-moses"
data_dir.mkdir(); peer.mkdir()
reg = data_dir / "projects.json"
reg.write_text(json.dumps({"projects": [{"id": "fixture", "name": "Fixture Project", "rank": 1}], "inbox": []}))
before = reg.read_text()
KEY = "k" * 40
(peer / "mcp-operator.key").write_text(KEY + "\n")
reg.chmod(0o444); data_dir.chmod(0o555)
env = {"PATH": "/usr/bin:/bin", "HOME": str(work), "MOSES_REGISTRY": str(reg),
       "MOSES_PEER_STATE": str(peer), "MOSES_MCP_URL": URL}


def last():
    return CALLS[-1]["body"]["params"] if CALLS else {}


r = run("list")
check("reading still comes straight from the file", r.returncode == 0 and "Fixture Project" in r.stdout
      and not CALLS, r.stdout + r.stderr)

r = run("set", "fixture", "next", "ship", "it")
check("a change is sent to Moses's project_update tool", last().get("name") == "project_update"
      and last().get("arguments") == {"project": "fixture", "field": "next", "value": "ship it"}, str(last()))
check("… with the operator key as a bearer token", CALLS and CALLS[-1]["auth"] == f"Bearer {KEY}", str(CALLS[-1:]))
check("… and Moses's answer is printed", r.returncode == 0 and "ok from moses" in r.stdout, r.stdout + r.stderr)
check("… and this account never touched the file", reg.read_text() == before)

r = run("idea", "fixture", "a thought", "--note", "why it matters")
check("an idea keeps its note on the way through", last().get("name") == "add_idea"
      and last().get("arguments") == {"idea": "a thought", "project": "fixture", "note": "why it matters"}, str(last()))

r = run("done", "FX3")
check("a code alone is passed as the item", last().get("name") == "project_complete_milestone"
      and last().get("arguments") == {"item": "FX3", "project": "", "undo": False}, str(last()))

ANSWER["text"] = "Could not update it: no project matches 'nope'"
r = run("set", "nope", "next", "x")
check("Moses's 'Could not' is a failure here too", r.returncode == 1 and "no project matches" in r.stderr, r.stdout + r.stderr)
ANSWER["text"] = "ok from moses"

ANSWER["code"] = 401
r = run("set", "fixture", "next", "x")
check("a refused key says so, and that nothing changed", r.returncode == 1 and "refused the operator key" in r.stderr
      and "Nothing was changed" in r.stderr, r.stderr)
ANSWER["code"] = 200

(peer / "mcp-operator.key").unlink()
n = len(CALLS)
r = run("set", "fixture", "next", "x")
check("no key: refused with the way out, and nothing is sent", r.returncode == 1 and "no Moses MCP operator key" in r.stderr
      and "connector" in r.stderr and len(CALLS) == n, r.stderr)
(peer / "mcp-operator.key").write_text(KEY + "\n")

srv.shutdown(); srv.server_close()
r = run("set", "fixture", "next", "x")
check("Moses not answering: says so, nothing changed", r.returncode == 1 and "did not answer" in r.stderr, r.stderr)

data_dir.chmod(0o755); reg.chmod(0o644)
n = len(CALLS)
r = run("set", "fixture", "next", "local")
check("a registry this account CAN write is still written directly", r.returncode == 0 and len(CALLS) == n
      and "local" in reg.read_text(), r.stdout + r.stderr)

shutil.rmtree(work, ignore_errors=True)
print(f"\n  {len(fails)} failed" if fails else "\n  all good")
sys.exit(1 if fails else 0)
