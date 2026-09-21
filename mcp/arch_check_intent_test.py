#!/usr/bin/env python3
"""A unit that is off ON PURPOSE is not drift — and one that is running against that declaration is.

    python3 mcp/arch_check_intent_test.py

WHY THIS EXISTS. On 2026-09-04 the standup reported two faults that were both intended states, and
Brad's objection was not the false alarm but the missing half: "Moses knows 2 things have drifted but
he doesn't know WHY. You know why burtbot isn't active, he does not." The reason lived in a commit
message and in Claude's head; the standup reads the architecture document. So intent is declared
there and parsed here, and the reason travels into the finding.

THE INVERSE IS THE POINT. Suppressing "declared off and is off" is easy and would be worth nothing on
its own — the value is that "declared off and RUNNING" now fails, because something started it while
the stated reason was unresolved. A suppression with no matching alarm is just a switched-off check.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arch_check as A  # noqa: E402

fails: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✓' if cond else '✗'} {label}" + (f" — {detail}" if not cond and detail else ""))
    if not cond:
        fails.append(label)


DOC = """
# fixture

<!-- arch-check: deliberately-inactive -->

| unit | why it is off | what would turn it back on |
|---|---|---|
| `{unit}` | the reason it is off, which must reach the report | someone does the thing |

## next section
"""


def findings_for(unit: str, tmp: Path):
    tmp.write_text(DOC.format(unit=unit), encoding="utf-8")
    return {f.claim: f for f in A.check(doc=tmp)}


import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    tmp = Path(d) / "ARCH.md"

    # A real user unit that is installed and NOT running — the burtbot case.
    got = findings_for("burtbot.service", tmp).get("burtbot.service")
    check("a unit declared off, and off, is OK not drift", got is not None and got.status == "ok",
          f"got {got.status if got else 'nothing'}")
    check("and the REASON reaches the finding",
          got is not None and "the reason it is off" in got.detail,
          f"detail was: {got.detail if got else 'none'}")

    # A real user unit that IS running, declared off — must fail.
    got = findings_for("moses.service", tmp).get("moses.service")
    check("a unit declared off but RUNNING is drift", got is not None and got.status == "drift",
          f"got {got.status if got else 'nothing'}")
    check("and says so plainly", got is not None and "RUNNING" in got.detail,
          f"detail was: {got.detail if got else 'none'}")

    # A unit that does not exist at all is still drift, declaration or not.
    got = findings_for("nosuchthing.service", tmp).get("nosuchthing.service")
    check("declaring a unit that does not exist is still drift",
          got is not None and got.status == "drift", f"got {got.status if got else 'nothing'}")

    # Without the marker, nothing is suppressed.
    tmp.write_text("# fixture\n\n`burtbot.service` is mentioned with no declaration.\n", encoding="utf-8")
    got = {f.claim: f for f in A.check(doc=tmp)}.get("burtbot.service")
    check("with no declaration table, an inactive unit is drift again",
          got is not None and got.status == "drift", f"got {got.status if got else 'nothing'}")

print(f"\n  {len(fails)} failed" if fails else "\n  all good")
sys.exit(1 if fails else 0)
