#!/usr/bin/env python3
"""The remediation wrapper refuses everything it is not sure about.

    python3 agent/remediate_test.py

WHAT THIS USED TO TEST AND NO LONGER DOES. Until 2026-09-02 this file covered `deploy-status` and
`deploy-moses`, two ops that pushed the agent modules into /opt/moses. That directory was a COPY of
the source; the listener now runs as a systemd user unit executing the source directly, so there is
no copy, nothing to push, and deploying is `systemctl --user restart moses` with no sudo. The ops
were removed rather than left pointing at a directory that no longer exists, and the assertions that
they exist were removed with them — a test pinning a world that is gone is worse than no test,
because it goes green forever while measuring nothing.

What remains is the property that actually matters now: the roster is the OPERATOR'S file, and this
wrapper runs as root. Its safety no longer comes from the roster being root-owned.
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WRAPPER = HERE / "moses-remediate"
GUARD = HERE / "lib" / "roster-exec-guard.sh"

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{('' if cond else ' — ' + detail)}")
    if not cond:
        fails.append(name)


def run(*args, roster: Path, log: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "MOSES_ROSTER": str(roster), "MOSES_EXEC_GUARD": str(GUARD),
           "MOSES_REMEDIATION_LOG": str(log)}
    return subprocess.run([str(WRAPPER), *args], capture_output=True, text=True, env=env, timeout=60)


import json
import tempfile

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    log = tmp / "ledger.jsonl"
    roster = tmp / "roster.json"
    roster.write_text(json.dumps({"personas": [
        {"id": "safe", "name": "Safe", "check": {"type": "command", "argv": ["/usr/local/bin/therapist", "morning"]}},
        {"id": "evil", "name": "Evil", "check": {"type": "command", "argv": [str(tmp / "evil.sh")]}},
        {"id": "bare", "name": "Bare"},
    ]}))
    evil = tmp / "evil.sh"
    evil.write_text("#!/bin/sh\necho PWNED\n")
    evil.chmod(0o755)

    r = run("ops", roster=roster, log=log)
    check("ops lists the personas from the roster", "safe" in r.stdout and "evil" in r.stdout, r.stdout[:120])
    # The ops that pushed to /opt/moses must be GONE, not merely unused. A wrapper still advertising
    # them would send someone to deploy into a directory that no longer exists.
    check("the retired deploy ops are gone", "deploy-status" not in r.stdout and "deploy-moses" not in r.stdout,
          r.stdout[:160])

    r = run("rerun-probe", "evil", roster=roster, log=log)
    check("a roster naming an operator-owned command is REFUSED", r.returncode != 0, r.stdout[:160])
    check("and PWNED never ran", "PWNED" not in (r.stdout + r.stderr), (r.stdout + r.stderr)[:160])
    check("the refusal is recorded", log.exists() and "unsafe" in log.read_text(),
          log.read_text()[-200:] if log.exists() else "no ledger")

    r = run("rerun-probe", "bare", roster=roster, log=log)
    check("a persona with no command check is refused by name", r.returncode != 0 and "no command check" in r.stderr,
          (r.stderr or r.stdout)[:160])

    r = run("rm-rf", roster=roster, log=log)
    check("an unknown op is refused", r.returncode != 0 and "unknown op" in r.stderr, (r.stderr or r.stdout)[:160])

    r = run("ops", roster=tmp / "nonexistent.json", log=log)
    check("an unreadable roster says NOTHING was checked", "NOTHING was checked" in r.stderr, r.stderr[:160])
    check("and does not answer as though the allowlist were empty", r.returncode != 0)

print()
if fails:
    print(f"{len(fails)} FAILED: " + ", ".join(fails))
    sys.exit(1)
print("remediate_test: all checks pass")
