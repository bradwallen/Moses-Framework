#!/usr/bin/env python3
"""A tool that breaks hands back the evidence, through the real server, and records it.

    mcp/venv/bin/python mcp/tool_diagnosis_test.py        (free: no model, no network)

2026-10-03: knight_jobs raised and Moses got one line — "can't decode byte 0xe2 in position N" — and,
having no shell, asked Brad to run `xxd`. These checks call tools THROUGH THE SERVER (the seam Moses
actually uses), not the helper functions alone: a wrapper that exists but is not on the call path
would pass a unit test and change nothing.
"""
import anyio
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mcp_server  # noqa: E402 — importing does not start the server
import tool_diagnosis  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

fails = []
ledger = Path(tempfile.mkdtemp(prefix="tool-failures-test-")) / "tool-failures.jsonl"
mcp_server.TOOL_FAILURES = ledger
# The literal bytes from the incident: a title, the first two bytes of an em dash, a newline.
INCIDENT = b"20261003-101500-1  done   `checklist.md` \xe2\x80\n  next row"


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  — {detail[-600:]}"))
    if not cond:
        fails.append(label)


def call(name, args=None):
    """Call a tool the way the MCP client does. Returns (ok, text)."""
    async def go():
        return await mcp_server.server.call_tool(name, args or {})
    try:
        r = anyio.run(go)
        return True, "".join(getattr(c, "text", "") for c in getattr(r, "content", []) or [])
    except ToolError as e:
        return False, str(e)


# A tool registered the same way every real one is, that fails the way knight_jobs did.
@mcp_server.server.tool(description="test only: decodes the incident bytes strictly")
def _broken_listing(note: str = "") -> str:
    return INCIDENT.decode("utf-8")


@mcp_server.server.tool(description="test only: fails with a credential in the message")
def _leaky() -> str:
    raise RuntimeError("connect failed for xoxb-1234567890-abcdefghij")


print("a tool that raises, called through the server")
ok, text = call("_broken_listing", {"note": "why"})
check("it is reported as a failure, not a result", not ok, text)
check("the message names the tool and says the TOOL broke", "TOOL FAILURE in _broken_listing" in text, text)
check("the offending bytes are shown in hex, in context", "⟦\\xe2\\x80⟧" in text and "checklist.md" in text, text)
check("it says where in our code it was raised", "in _broken_listing" in text and "raised at:" in text, text)
check("it tells the caller not to hand Brad a command", "Do not ask Brad to run commands" in text, text)
rows = ledger.read_text().splitlines() if ledger.exists() else []
check("and the failure is recorded", len(rows) == 1 and "_broken_listing" in rows[0], str(rows))

print("\na failure that quotes a credential")
ok, text = call("_leaky")
check("the token never reaches the caller", "xoxb-" not in text and "«redacted»" in text, text)
check("nor the ledger", "xoxb-" not in ledger.read_text(), ledger.read_text())

print("\nthe tool_failures tool reads the ledger back")
ok, text = call("tool_failures", {"limit": 5})
check("it answers", ok, text)
check("newest first, with the evidence", text.find("_leaky") < text.find("_broken_listing")
      and "⟦\\xe2\\x80⟧" in text, text)

print("\na command that writes a half character")
before = len(ledger.read_text().splitlines())
out = mcp_server._run(["printf", "checklist.md` \\342\\200\\n  next row"])
check("the output survives", "next row" in out and "checklist.md" in out, out)
check("and says which bytes were bad, and where", "not valid UTF-8" in out and "⟦\\xe2\\x80⟧" in out, out)
check("and is recorded too", len(ledger.read_text().splitlines()) == before + 1, ledger.read_text())
clean = mcp_server._run(["printf", "an em dash — intact"])
check("clean output carries no note", "note:" not in clean and clean == "an em dash — intact", clean)

print("\nwrapping did not change what the tools look like")
tools = {t.name: t for t in anyio.run(mcp_server.server.list_tools)}
check("real tools keep their parameters", "query" in str(tools["search_memory"].input_schema), "")
check("tool_failures is served", "tool_failures" in tools, "")

print(f"\n  {len(fails)} failed" if fails else "\n  all good")
sys.exit(1 if fails else 0)
