#!/usr/bin/env python3
"""A tool's command output must never be thrown away for one undecodable byte.

    mcp/venv/bin/python mcp/run_decode_test.py        (free: no model, no network)

2026-10-03: `knight list` cut a job title at 60 BYTES, splitting an em dash (e2 80 | 94). _run decoded
the whole output strictly, so knight_jobs raised on every call and Moses could read no job at all —
including the one Atlas had just asked him about. Two fixes, both pinned here:

  1. _run decodes with errors="replace": a bad byte becomes U+FFFD and the rest of the evidence
     survives. Proven against the exact bytes that broke it.
  2. `knight list` truncates by characters, so the bad byte is not produced in the first place.
     Proven against the real script with a title whose em dash straddles byte 60.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mcp_server  # noqa: E402 — importing does not start the server (it runs only under __main__)

# The bad bytes below are recorded as a tool failure; that record belongs in a scratch ledger, never
# the live one Moses reads.
mcp_server.TOOL_FAILURES = Path(tempfile.mkdtemp(prefix="run-decode-test-")) / "tool-failures.jsonl"

fails = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  — {detail}"))
    if not cond:
        fails.append(label)


print("the tool boundary")
# The literal bytes from the incident: a title, then the first two bytes of an em dash, then a newline.
out = mcp_server._run(["printf", "checklist.md` \\342\\200\\n  next row"])
check("a command emitting a half character does not raise", isinstance(out, str), repr(out))
check("the bad byte becomes a replacement character", "�" in out, repr(out))
check("and the rest of the output survives", "next row" in out and "checklist.md" in out, repr(out))

print("\nthe cause: knight list cuts titles by characters")
jobs = Path(tempfile.mkdtemp(prefix="knight-list-test-"))
job = jobs / "20990101-000000-1"
job.mkdir()
# 59 ASCII bytes, then an em dash occupying bytes 60-62: `head -c 60` would keep only its first byte.
title = "x" * 59 + "— and the rest of the title"
(job / "task.txt").write_text(title + "\nsecond line of the brief\n", encoding="utf-8")
(job / "status").write_text("done\n")
env = dict(os.environ, KNIGHT_JOBS=str(jobs))
p = subprocess.run([str(HERE.parent / "knight/bin/knight"), "list"], capture_output=True, env=env)
raw = p.stdout + p.stderr
try:
    text = raw.decode("utf-8")
    ok = True
except UnicodeDecodeError as e:
    text, ok = repr(raw), False
check("a title with a multi-byte character at the cut still decodes cleanly", ok, text[-200:])
check("the job is still listed", "20990101-000000-1" in text, text[-200:])
# Atlas, 2026-10-03: assert the exit code, not only the output — iconv exits 1 on the half character.
check("and knight list exits 0", p.returncode == 0, f"exit {p.returncode}")
check("and only the first line of a multi-line brief is shown", "second line" not in text, text[-200:])

print(f"\n  {len(fails)} failed" if fails else "\n  all good")
sys.exit(1 if fails else 0)
