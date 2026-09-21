"""budget — the spend ceiling and ledger for Moses's agents.

Brad's constraint: agents must never spend against the Claude API without authorization, because it
costs money and can interfere with Viatica's itinerary parsing. Two halves to honoring that:

  1. ISOLATION — agents authenticate with their own credential, never Viatica's production key, so
     their traffic cannot consume Viatica's rate limit. That lives in the runner, not here.
  2. A CEILING — this file. A hard daily cap per agent, enforced before a session starts and again
     by the SDK's own `max_budget_usd` while it runs.

Two enforcement points on purpose. The pre-flight check refuses to start work that would blow the
cap; the SDK's ceiling stops a session that runs away mid-flight. Neither alone is enough: the first
can't see a session that turns out expensive, and the second can't stop a fifth session after four
cheap ones already spent the day's budget.

Spend is recorded in real dollars from `ResultMessage.total_cost_usd`, never estimated from tokens.
The ledger is per-day and append-only, so `moses budget` can show where the money went and Big Pipe
can carry agent spend as a real line rather than a guess — consistent with Anthropic credits already
being booked as one-time purchases in the books.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

STATE = Path(os.environ.get("MOSES_STATE", "/var/lib/moses"))
LEDGER_DIR = STATE / "spend"

# Fallback when the roster doesn't set one. Deliberately small — a runaway agent should hit this and
# stop, and Brad should have to consciously raise it rather than discover the bill later.
DEFAULT_DAILY_USD = 5.00


@dataclass(frozen=True)
class Status:
    agent: str
    spent_usd: float
    cap_usd: float
    runs: int

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)

    @property
    def exhausted(self) -> bool:
        return self.spent_usd >= self.cap_usd


def _ledger_path(day: date | None = None) -> Path:
    return LEDGER_DIR / f"{(day or date.today()).isoformat()}.json"


def _load(day: date | None = None) -> dict:
    p = _ledger_path(day)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt ledger must not read as "nothing spent today" — that would silently reset the
        # cap and let an agent spend the budget twice. Treat it as untrusted and fail closed.
        return {"__corrupt__": True}


def record(agent: str, cost_usd: float, *, turns: int = 0, denials: int = 0) -> None:
    """Append one run's real cost. Called after every agent session, success or failure."""
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    data = _load()
    data.pop("__corrupt__", None)
    entry = data.setdefault(agent, {"usd": 0.0, "runs": 0, "turns": 0, "denials": 0})
    entry["usd"] = round(entry["usd"] + max(0.0, cost_usd), 6)
    entry["runs"] += 1
    entry["turns"] += turns
    entry["denials"] += denials

    tmp = _ledger_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(_ledger_path())


def status(agent: str, cap_usd: float | None = None) -> Status:
    data = _load()
    cap = DEFAULT_DAILY_USD if cap_usd is None else cap_usd
    if data.get("__corrupt__"):
        # Unreadable ledger ⇒ assume the cap is gone. Failing closed on a bookkeeping error is the
        # cheap mistake; failing open is the expensive one.
        return Status(agent, cap, cap, 0)
    e = data.get(agent, {})
    return Status(agent, float(e.get("usd", 0.0)), cap, int(e.get("runs", 0)))


def may_start(agent: str, cap_usd: float | None = None) -> tuple[bool, Status]:
    """Pre-flight gate. False means don't start the session at all."""
    st = status(agent, cap_usd)
    return (not st.exhausted), st


def session_ceiling(st: Status) -> float:
    """What to hand the SDK as `max_budget_usd` for one session.

    The remaining daily allowance, so a single session can never overrun the day's cap even if it
    is the first run. Floored just above zero because the SDK treats 0 as unset.
    """
    return max(0.01, round(st.remaining_usd, 4))


def report(day: date | None = None) -> str:
    """Human-readable ledger for `moses budget` and the standup."""
    data = _load(day)
    when = (day or date.today()).isoformat()
    if data.get("__corrupt__"):
        return f"⚠️ *Agent spend {when}* — ledger unreadable; agents are held until it is fixed."
    if not data:
        return f"*Agent spend {when}* — nothing yet."
    lines = [f"*Agent spend {when}*"]
    total = 0.0
    for agent in sorted(data):
        e = data[agent]
        total += e["usd"]
        extra = f" · {e['denials']} blocked" if e.get("denials") else ""
        lines.append(f"   {agent}: ${e['usd']:.4f} over {e['runs']} run(s){extra}")
    lines.append(f"   *total: ${total:.4f}*")
    return "\n".join(lines)


if __name__ == "__main__":          # `moses budget` shells to this
    import sys
    print(report(date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None))
