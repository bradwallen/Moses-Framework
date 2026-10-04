"""When a tool breaks, hand the caller the evidence — not a one-line error and a question for Brad.

WHY THIS EXISTS (2026-10-03). `knight_jobs` raised on every call because a job title had been cut in
the middle of an em dash. All Moses got back was the SDK's one line — "Error executing tool
knight_jobs: 'utf-8' codec can't decode byte 0xe2 in position 1184" — and his chat has no shell, by
design. He could see that it failed and nothing about why, so he did the one thing left to him and
asked Brad to run `xxd`. The diagnosis needed the bytes around position 1184; the tool had them and
threw them away.

The answer is NOT a shell for Moses: his chat takes input from Atlas and anyone in the workspace, and
the fixed tool list is the whole safety model. The answer is that a tool which fails reports what a
person with a shell would have gone and looked at — which tool, the fixed command it ran, the
exception, where in our code it was raised, and for a decode failure the offending bytes in context.
Nothing here runs anything: it only describes a failure that already happened, so it widens no wall.

Every failure is also appended to tool-failures.jsonl, so a fault is a record (read with the
`tool_failures` tool) rather than a sentence in one chat turn that scrolls away.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import traceback
from pathlib import Path

# Same shapes diagnose.py refuses to pass on: a diagnosis quotes raw output, which is exactly where a
# token would surface. Kept as a copy rather than an import so this module loads without the agent tree.
SECRET = re.compile(r"(sk-ant-[A-Za-z0-9_\-]+|re_[A-Za-z0-9_\-]{10,}|sk_live_[A-Za-z0-9]+|"
                    r"whsec_[A-Za-z0-9]+|xox[baprs]-[A-Za-z0-9\-]+|xapp-[A-Za-z0-9\-]+|"
                    r"postgres(?:ql)?://[^\s]*:[^\s]*@[^\s]*)")
CONTEXT = 40          # bytes either side of an undecodable one
MAX_ARG = 200         # a tool argument is model-written; quote enough to recognize it, no more


def redact(text: str) -> str:
    return SECRET.sub("«redacted»", text or "")


def byte_context(data: bytes, start: int, end: int | None = None) -> str:
    """The bytes around a bad one: readable text with the culprits shown as \\xNN, and the offset."""
    end = start + 1 if end is None else end
    lo, hi = max(0, start - CONTEXT), min(len(data), end + CONTEXT)
    before = data[lo:start].decode("utf-8", "backslashreplace")
    bad = "".join(f"\\x{b:02x}" for b in data[start:end])
    after = data[end:hi].decode("utf-8", "backslashreplace")
    return (f"byte offset {start} of {len(data)}: "
            f"{'…' if lo else ''}{before}⟦{bad}⟧{after}{'…' if hi < len(data) else ''}")


def first_bad_byte(data: bytes) -> str | None:
    """None when `data` is valid UTF-8; otherwise the context around the first byte that is not."""
    try:
        data.decode("utf-8")
        return None
    except UnicodeDecodeError as e:
        return byte_context(data, e.start, e.end)


def _where(exc: BaseException) -> list[str]:
    """The last few frames, as path:line in function — where in OUR code it went wrong."""
    frames = traceback.extract_tb(exc.__traceback__)[-4:]
    return [f"{Path(f.filename).name}:{f.lineno} in {f.name}" for f in frames]


def diagnose(tool: str, args: dict, exc: BaseException) -> dict:
    """Everything a person at a shell would have looked at, gathered from the failure itself."""
    d = {
        "tool": tool,
        "args": {k: (repr(v)[:MAX_ARG]) for k, v in (args or {}).items()},
        "error": f"{type(exc).__name__}: {exc}"[:600],
        "where": _where(exc),
    }
    if isinstance(exc, UnicodeDecodeError) and isinstance(exc.object, (bytes, bytearray)):
        d["evidence"] = byte_context(bytes(exc.object), exc.start, exc.end)
    elif isinstance(exc, subprocess.CalledProcessError):
        d["command"] = " ".join(map(str, exc.cmd)) if isinstance(exc.cmd, (list, tuple)) else str(exc.cmd)
        d["exit"] = exc.returncode
        tail = exc.stderr or exc.output or b""
        tail = tail.decode("utf-8", "replace") if isinstance(tail, (bytes, bytearray)) else str(tail)
        d["evidence"] = tail.strip()[-800:]
    elif isinstance(exc, subprocess.TimeoutExpired):
        d["command"] = " ".join(map(str, exc.cmd)) if isinstance(exc.cmd, (list, tuple)) else str(exc.cmd)
        d["evidence"] = f"no answer within {exc.timeout}s"
    return {k: (redact(v) if isinstance(v, str) else v) for k, v in d.items()}


def render(d: dict) -> str:
    """The text the caller reads. Ends by saying what to do with it, because the old ending was 'ask Brad'."""
    lines = [f"TOOL FAILURE in {d['tool']} — the tool broke; this is not about what you asked.",
             f"error: {d['error']}"]
    if d.get("command"):
        lines.append(f"command: {d['command']}" + (f" (exit {d['exit']})" if "exit" in d else ""))
    if d.get("evidence"):
        lines.append(f"evidence: {d['evidence']}")
    if d.get("where"):
        lines.append("raised at: " + " ← ".join(reversed(d["where"])))
    if d.get("args"):
        lines.append("called with: " + ", ".join(f"{k}={v}" for k, v in d["args"].items()))
    lines.append("This is recorded (tool_failures). Do not ask Brad to run commands to investigate — the "
                 "evidence above is what a shell would show. Say what broke in one line; if it needs a "
                 "code change, propose a Knight job on the `moses` target that quotes this evidence.")
    return "\n".join(lines)


def record(ledger: Path, d: dict) -> None:
    """Append to the ledger. A ledger that cannot be written never hides the diagnosis itself."""
    try:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **d}) + "\n")
    except OSError:
        pass


def recent(ledger: Path, limit: int = 10) -> str:
    try:
        rows = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except FileNotFoundError:
        return "No tool has failed since the ledger began."
    except (OSError, ValueError) as e:
        return f"Could not read {ledger}: {e}"
    if not rows:
        return "No tool has failed since the ledger began."
    out = [f"{len(rows)} tool failure(s) recorded; newest first:"]
    for r in reversed(rows[-limit:]):
        out.append(f"\n{r.get('at', '?')}  {r.get('tool', '?')}  {r.get('error', '')}")
        if r.get("evidence"):
            out.append(f"  evidence: {r['evidence']}")
        if r.get("where"):
            out.append(f"  raised at: {r['where'][-1]}")
    return "\n".join(out)
