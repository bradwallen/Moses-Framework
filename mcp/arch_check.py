"""arch_check — does docs/ARCHITECTURE.md still describe the machine it claims to describe?

Commandment #5 says every project carries architecture diagrams. Brad's follow-up (2026-08-06) is the
part that makes it real: *"we do not want architecture drift."* A stale diagram is worse than none —
it is confidently wrong, and the next person plans against it.

So this extracts the CHECKABLE claims out of the document and tests each against live state. It does
not try to understand the prose; it pulls out the concrete identifiers a diagram inevitably contains
— service names, host:port binds, absolute paths, hostnames, roster ids — and asks whether each one
still exists. Anything it cannot verify, it says so rather than passing silently, because a checker
that quietly skips what it cannot test is the same failure as a monitor that stopped monitoring.

It is deliberately one-directional: it catches things the DOC claims that reality no longer has. It
cannot catch a new component nobody documented — no automated check can, and pretending otherwise
would be the more dangerous error.
"""

from __future__ import annotations

import sys as _sys, pathlib as _pl  # noqa: E401
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent / "agent" / "lib"))
import moses_env as _env  # noqa: E402 — whose home, where Moses lives, the operator's settings
import os
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

DOC = _env.ROOT / "agent/docs/ARCHITECTURE.md"
ROSTER = Path(os.environ.get("MOSES_ROSTER", str(Path.home() / ".config" / "moses" / "roster.json")))


@dataclass
class Finding:
    status: str      # "ok" | "drift" | "unknown"
    kind: str
    claim: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _unit_scope(unit: str) -> str | None:
    """Which systemd scope actually knows this unit — user, system, or neither.

    Asking both and taking whichever answers first is wrong: `systemctl --user is-active` returns
    a confident "inactive" for a SYSTEM unit it has never heard of. That made this checker report
    moses.service and moses-morning.timer as down while both were running. Establish the scope,
    then ask only that scope.
    """
    for flag, name in ((["--user"], "user"), ([], "system")):
        rc, out = _sh(["systemctl", *flag, "list-unit-files", unit])
        if rc == 0 and unit in out:
            return name
    return None


def _env() -> dict:
    """`systemctl --user` is blind without XDG_RUNTIME_DIR, and answers as though nothing exists.

    Run from a shell that lacks it, this checker reported moses-mcp.service and
    moses-mcp-public.service as "no such unit" while both were running — drift invented by the
    checker's own environment. A checker that raises false alarms gets ignored, which is the one
    outcome that makes it worse than not having it. Set it explicitly rather than hoping the caller did.
    """
    import os
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def _sh(argv: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=25, env=_env())
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 127, ""


def _listening() -> set[str]:
    _, out = _sh(["ss", "-tln"])
    return {m for m in re.findall(r"(\S+:\d+)\s", out)}


def check(doc: Path = DOC) -> list[Finding]:
    if not doc.is_file():
        return [Finding("drift", "doc", str(doc), "the architecture document itself is missing")]
    text = doc.read_text(encoding="utf-8")
    out: list[Finding] = []

    # ── units the doc says are off ON PURPOSE ────────────────────────────────
    # OFF-BY-DESIGN AND FELL-OVER LOOK IDENTICAL FROM HERE, and reporting the first as a fault is
    # the false positive that gets a checker ignored. Worse, it left the REASON somewhere the
    # standup could not see: Brad, 2026-09-04, "Moses knows 2 things have drifted but he doesn't
    # know WHY". So intent is declared in the doc, in a marked table, and the reason travels into
    # the finding.
    #
    # It cuts both ways. A unit declared off that is RUNNING is drift — something started it while
    # the stated reason was still unresolved, and that is exactly as worth saying.
    deliberate: dict[str, str] = {}
    marker = text.find("<!-- arch-check: deliberately-inactive -->")
    if marker != -1:
        for line in text[marker:].splitlines():
            if line.startswith("## "):          # the section ended
                break
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and re.fullmatch(r"`[a-z0-9@._-]+\.(?:service|timer)`", cells[0]):
                deliberate[cells[0].strip("`")] = cells[1]

    # ── systemd units the doc names ──────────────────────────────────────────
    for unit in sorted(set(re.findall(r"\b([a-z0-9@._-]+\.(?:service|timer))\b", text))):
        if unit in deliberate:
            scope = _unit_scope(unit)
            if scope is None:
                out.append(Finding("drift", "unit", unit,
                                   "declared deliberately off, but no such unit exists at all"))
                continue
            flag = ["--user"] if scope == "user" else []
            _, act = _sh(["systemctl", *flag, "is-active", unit])
            state = act.strip().splitlines()[0] if act.strip() else "unknown"
            why = deliberate[unit]
            if state in ("active", "activating"):
                out.append(Finding("drift", "unit", unit,
                                   f"RUNNING, but the doc says it should be off — {why}"))
            else:
                out.append(Finding("ok", "unit", unit, f"{state} on purpose — {why}"))
            continue
        scope = _unit_scope(unit)
        if scope is None:
            out.append(Finding("drift", "unit", unit, "named in the doc but no such unit exists"))
            continue
        flag = ["--user"] if scope == "user" else []
        _, act = _sh(["systemctl", *flag, "is-active", unit])
        state = act.strip().splitlines()[0] if act.strip() else "unknown"
        if state in ("active", "activating"):
            out.append(Finding("ok", "unit", unit, f"{state} ({scope})"))
        elif unit.endswith(".service") and _unit_scope(unit[:-8] + ".timer"):
            # A oneshot driven by a timer is inactive between firings. That is correct, not drift.
            out.append(Finding("ok", "unit", unit, f"{state} — timer-driven oneshot ({scope})"))
        else:
            out.append(Finding("drift", "unit", unit, f"exists but is {state} ({scope})"))

    # ── host:port binds ──────────────────────────────────────────────────────
    live = _listening()
    for bind in sorted(set(re.findall(r"\b((?:\d{1,3}\.){3}\d{1,3}:\d{4,5})\b", text))):
        if bind in live:
            out.append(Finding("ok", "bind", bind, "listening"))
        else:
            also = [l for l in live if l.endswith(":" + bind.split(":")[1])]
            out.append(Finding("drift", "bind", bind,
                               f"NOT listening" + (f" (but {', '.join(also)} is)" if also else "")))

    # ── absolute paths ───────────────────────────────────────────────────────
    # A PATH IN A SENTENCE ABOUT THE PAST IS NOT A CLAIM ABOUT THE PRESENT.
    #
    # This fired every week from 2026-08-31 on `/opt/moses/mcp_server.py — MISSING`. The doc does not
    # claim that file exists; it says the tracked units USED to point at it, wrongly, before the MCP
    # server became a user service. The checker was reading a history lesson as an assertion, and a
    # guard that fires on correct prose is one that gets ignored — which is stated in this file's own
    # docstring, twenty lines up.
    #
    # Skipped paths are REPORTED, not silently dropped: over-suppression would be the same bug
    # facing the other way, and the only way to see it is to print what was set aside.
    HISTORICAL = re.compile(
        r"\b(used to|no longer|from before|pre-migration|were wrong|was wrong|"
        r"before the|until |replaced by|instead of|formerly|previously|had been)\b", re.I)
    historical: set[str] = set()
    claimed: set[str] = set()
    for line in text.splitlines():
        found = re.findall(r"(/(?:etc|var|opt|home)/[A-Za-z0-9._/-]+)", line)
        (historical if HISTORICAL.search(line) else claimed).update(found)
    # A path claimed anywhere else stays a claim even if some other line reminisces about it.
    paths = claimed
    for p in sorted(historical - claimed):
        out.append(Finding("skipped", "path", p.rstrip("/.,"),
                           "named in a sentence about the past — not checked"))
    for p in sorted(paths):
        p = p.rstrip("/.,")
        if len(p.split("/")) < 3:
            continue
        target = Path(p)
        if target.exists():
            out.append(Finding("ok", "path", p, "exists"))
        else:
            # A path inside a root-only directory is unreadable, not absent — do not cry wolf.
            parent_unreadable = any(
                not Path(a).is_dir() or not __import__("os").access(a, 4)
                for a in [str(target.parent)]
            )
            out.append(Finding(
                "unknown" if parent_unreadable else "drift", "path", p,
                "unreadable from this account" if parent_unreadable else "MISSING"))

    # ── public hostnames ─────────────────────────────────────────────────────
    for host in sorted(set(re.findall(r"\b([a-z0-9-]+\.viatica\.travel)\b", text))):
        rc, out_dig = _sh(["dig", "+short", host])
        if out_dig.strip():
            out.append(Finding("ok", "dns", host, out_dig.strip().splitlines()[0]))
        else:
            out.append(Finding("drift", "dns", host, "does not resolve"))

    # ── roster ids named in the persona table ────────────────────────────────
    if ROSTER.is_file():
        try:
            ids = {p["id"] for p in json.loads(ROSTER.read_text(encoding="utf-8"))["personas"]}
            table = set(re.findall(r"^\|\s*([a-z]+)\s*\|\s*(?:service|job|agent)\s*\|", text, re.M))
            for missing in sorted(table - ids):
                out.append(Finding("drift", "roster", missing, "in the doc's table but not in roster.json"))
            for undocumented in sorted(ids - table):
                out.append(Finding("drift", "roster", undocumented, "in roster.json but absent from the doc"))
            if table and not (table ^ ids):
                out.append(Finding("ok", "roster", f"{len(ids)} entries", "doc matches roster.json"))
        except (json.JSONDecodeError, KeyError, OSError) as e:
            out.append(Finding("drift", "roster", str(ROSTER), f"unreadable: {e}"))

    return out


def report(doc: Path = DOC) -> str:
    findings = check(doc)
    drift   = [f for f in findings if f.status == "drift"]
    unknown = [f for f in findings if f.status == "unknown"]
    lines = [f"Architecture check — {doc}"]
    if not drift:
        lines.append(f"No drift. {len(findings)-len(unknown)} claim(s) verified: "
                     + ", ".join(sorted({f.kind for f in findings if f.status == "ok"})) + ".")
    else:
        lines.append(f"⚠️  {len(drift)} of {len(findings)} claims no longer hold:")
        for f in drift:
            lines.append(f"   [{f.kind}] {f.claim} — {f.detail}")
        lines.append("")
        lines.append("Either the machine changed and the diagram is stale, or the diagram was")
        lines.append("wrong when written. Both need a human; neither should be left alone.")
    if unknown:
        lines.append("")
        lines.append(f"{len(unknown)} claim(s) could not be checked from this account "
                     f"(not drift, just unverifiable):")
        for f in unknown:
            lines.append(f"   [{f.kind}] {f.claim} — {f.detail}")
    skipped = [f for f in findings if f.status == "skipped"]
    if skipped:
        lines.append("")
        # PRINTED, not silently dropped. Suppression that nobody can see is how a checker starts
        # agreeing with everything — the same failure as crying wolf, facing the other way.
        lines.append(f"{len(skipped)} path(s) read as history rather than as a claim, and NOT "
                     f"checked. If one of these is a real claim, reword the line:")
        for f in skipped:
            lines.append(f"   [{f.kind}] {f.claim} — {f.detail}")
    lines.append("")
    lines.append("Only catches claims the DOC makes that reality no longer supports.")
    lines.append("A component nobody documented is invisible to it — no checker can find those.")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    text = report()
    print(text)
    sys.exit(1 if "⚠️" in text else 0)
