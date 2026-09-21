"""diagnose — when a persona reports trouble, work out whether it is real. NEVER fix it.

Brad, 2026-08-14: *"Therapist reports something isn't green, asks Moses to take a look and decides
what the fix should be and attempts it. Am I wrong?"* — and then, after the case below: *"let's build
phase 1 and measure how often we run into diagnosed issues."*

PHASE 1 IS DIAGNOSIS ONLY, AND THE REASON IS CONCRETE. That morning Therapist reported "cannot reach
the push remote" while `knight doctor` printed "reachable ✓" minutes later. The alarm was false — the
check ran as root while Knight belongs to brad. **Had remediation been automatic, Moses would have
been dispatched to fix a working system**, and the plausible fixes (rewrite the git remote,
regenerate a key, switch to HTTPS) all break something that was fine, at 8am, unattended.

The failure mode of automatic remediation is not "fails to fix". It is "confidently changes what was
never broken". So this reads, reasons and reports. It cannot write, and it is given no tools with
which to try.

MEASUREMENT IS THE POINT. Every diagnosis records a verdict to a ledger, so "how often is an alarm
real?" becomes a number instead of an impression. Phase 2 — remediation through Knight, which already
has a test gate and no push rights — is a decision to make against that number, not before it.

THREE RULES THAT KEEP THIS SAFE:

1. **Probes are FIXED COMMANDS, chosen by keyword.** Nothing is ever built from the alarm text. An
   alarm is attacker-influenced in principle (anyone in the workspace can post one), and a diagnosis
   tool that ran commands derived from a message would be a remote shell with extra steps.

2. **The model gets no tools.** It receives a bundle of already-gathered output and returns prose.
   It cannot read a file, run a command, or reach the network.

3. **"Could not check" is never reported as "broken".** This is the exact bug being diagnosed, and
   it is easy to reproduce inside the diagnostician: run as brad, `birdeye-customs` cannot read the
   root-only VIATICA_APP_URL and says "nothing was checked" — which `--oneline` renders as
   "UNREACHABLE" while the site returns HTTP 200. Probes therefore report their own blindness
   explicitly, and the prompt is told to treat a blind probe as evidence about the CHECK, not the
   system.
"""

from __future__ import annotations

import sys as _sys, pathlib as _pl  # noqa: E401
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent / "lib"))
import moses_env as _env  # noqa: E402 — whose home, where Moses lives, the operator's settings
import getpass
import json
import os
import re
import shutil
import socket
import subprocess
import time
from datetime import date
from urllib.parse import urlparse
from pathlib import Path

STATE = Path(os.environ.get("MOSES_STATE", "/var/lib/moses"))
LEDGER = STATE / "diagnoses.jsonl"

# THE AGENTS' OWN CLI, installed per-user on 2026-09-13 (2.1.270) so it can be updated without root.
# /usr/bin/claude is a root-owned global npm install that sat at 2.1.220, below the 2.1.248 that
# `--restricted` needs, and Brad's own sessions never used it (his editor ships its own binary).
CLI = _env.claude_cli()
MODEL = os.environ.get("MOSES_DIAG_MODEL", "claude-opus-5")
EFFORT = os.environ.get("MOSES_DIAG_EFFORT", "low")
TIMEOUT_S = int(os.environ.get("MOSES_DIAG_TIMEOUT", "180"))

NO_TOOLS = ("Bash,Read,Write,Edit,MultiEdit,NotebookEdit,Glob,Grep,WebFetch,WebSearch,Task,Agent,"
            "TodoWrite,KillShell,BashOutput,Artifact,Skill,SlashCommand,ExitPlanMode,ListAgents,"
            "SendMessage,Monitor,ToolSearch")

# An alarm is a persona saying something is wrong. Deliberately narrow: a persona's *clean* report
# also mentions its subject, so matching on subject alone would diagnose every green morning.
#
# NEGATED COUNTS ARE NOT ALARMS. This matched "failed" inside Birdeye's healthiest possible line —
#     2 of 56241 files · 147.9 MB transferred · 0 failed · took 0m
# — so every clean backup report triggered a paid model call and a Slack post concluding "nothing
# indicates a fault". Observed 2026-08-21 on a status Brad had asked for himself. A detector that
# fires on good news is worse than none: it costs money, it adds noise, and it trains the reader to
# skip diagnoses.
#
# So counts are stripped before matching. "0 failed", "no errors", "0 failures" say the opposite of
# what the keyword suggests, and reading the digit is the whole difference.
NEGATED = re.compile(r"\b(?:0|no|zero)\s+(?:failed|failures?|errors?|issues?|problems?)\b", re.I)

ALARM = re.compile(
    r"(needs attention|needs a look|not ready|⚠️|:warning:|:red_circle:|🔴|FAILED|failing|"
    r"unreachable|expired|expiring|overdue|didn't report|behind|stalled|error|out of credit)", re.I)


# Credentials must never reach the model or Slack. Same shapes the inventory guard refuses to
# publish — a diagnosis quotes raw command output, which is exactly where a token would surface.
SECRET = re.compile(r"(sk-ant-[A-Za-z0-9_\-]+|re_[A-Za-z0-9_\-]{10,}|sk_live_[A-Za-z0-9]+|"
                    r"whsec_[A-Za-z0-9]+|xox[baprs]-[A-Za-z0-9\-]+|xapp-[A-Za-z0-9\-]+|"
                    r"postgres(?:ql)?://[^\s]*:[^\s]*@[^\s]*)")


def redact(text: str) -> str:
    return SECRET.sub("«redacted»", text or "")


# ── Probes ──────────────────────────────────────────────────────────────────
# keyword -> (label, argv, note-about-limits, target). argv is a FIXED list; nothing interpolated.
# TARGET is what the probe is registered to look at, written the way scope() reads the invocation: a URL,
# a unit, the paths it was given, the tool whose own state it reports, or `host <name>`.
HOST = socket.gethostname()
VIATICA = "https://viatica.travel/"

PROBES: list[tuple[tuple[str, ...], str, list[str], str, str]] = [
    (("knight", "push", "remote", "build"),
     "knight doctor (as the user who actually pushes)",
     [str(_env.ROOT / "knight/bin/knight"), "doctor"], "",
     str(_env.ROOT / "knight/bin/knight")),
    (("customs", "viatica", "reachable", "site", "deps", "cert", "domain"),
     "birdeye-customs",
     ["/usr/local/bin/birdeye-customs"],
     "Runs as brad, who CANNOT read the root-only VIATICA_APP_URL. If it says nothing was checked, "
     "that is this probe being blind — NOT the site being down.",
     VIATICA),
    (("customs", "viatica", "reachable", "site"),
     "direct HTTP check of Customs",
     ["curl", "-sS", "-o", "/dev/null", "-w", "HTTP %{http_code} in %{time_total}s at %{url_effective}",
      "--max-time", "20", VIATICA], "",
     VIATICA),
    (("backup", "idrive", "disk", "quota", "stale"),
     "birdeye jobs",
     ["/usr/local/bin/birdeye", "list"], "",
     "/usr/local/bin/birdeye"),
    (("moses", "listener", "slack", "connector"),
     "moses listener state",
     ["systemctl", "--user", "is-active", "moses.service"], "",
     "moses.service"),
]

ALWAYS: list[tuple[str, list[str], str]] = [
    ("uptime / load", ["uptime"], f"host {HOST}"),
    ("disk", ["df", "-h", "/", "/home"], "/ /home"),
]


# A probe that could not LOOK must never read as a probe that found nothing wrong — or worse, as one
# that found something. Atlas, 2026-08-14, sharpening the morning's bug into its durable form:
#
#   "the root bug wasn't the identity, it was the probe collapsing 'I couldn't look' into 'it's
#    broken'. Fix the identity and it reads true today; the day brad's key rotates or HOME moves, it
#    lies again the same way, because a null result still renders as a negative one."
#
# These are the shapes a blind probe actually produces on this box. `birdeye-customs` run as brad
# says "is not set, so nothing here was checked" — and its own --oneline renders that as UNREACHABLE
# while the site returns HTTP 200.
BLIND = re.compile(r"(not set|nothing here was checked|permission denied|not installed|no such file|"
                   r"command not found|could not read|unable to open|not authorized|no credential)",
                   re.I)


URL = re.compile(r"https?://[^\s`'\"<>)]+")


def scope(argv: list[str], target: str) -> str | None:
    """The concrete thing this probe was invoked against, or None when nothing names one.

    Coverage alone answered "did it look?" and not "at WHAT?" — and a check aimed at the wrong target
    looks exactly as healthy as one aimed at the right one: staging answering 200 reads the same as
    production answering 200.

    This used to be scraped from the check's output, which lost exactly the case that matters: a check
    that fails without printing its URL (birdeye-customs, erroring out) read as "scope unknown", and a
    real site-down was reported as "couldn't tell". So the scope comes from the INVOCATION, known
    before the check runs and whether or not it completes: the URL, unit or paths it was handed. A
    check handed none of those (birdeye-customs reads VIATICA_APP_URL itself, `knight doctor` reports
    its own state) was invoked as its registration, so it ran against the registered target. Output is
    only a second opinion — see crosscheck().
    """
    # A URL outranks paths: curl's `-o /dev/null` is plumbing, the site is what it looked at.
    named = ([a for a in argv[1:] if URL.match(a)]
             or [a for a in argv[1:] if a.endswith(".service") or a.startswith("/")])
    if named:
        return " ".join(named)
    return target or (argv[0] if argv[0].startswith("/") else None)


def _same(a: str, b: str) -> bool:
    # A trailing slash is the same site: a check may print or be handed its URL without one.
    return (a.rstrip("/") if URL.match(a) else a) == (b.rstrip("/") if URL.match(b) else b)


def crosscheck(ran: str | None, out: str) -> str | None:
    """A site the output names when none of them is the site the check was invoked against, else None.

    Never the source of the scope: a check that prints no URL says nothing here, and its scope stands.
    It catches what the invocation cannot see — a URL configured INSIDE the check (birdeye-customs)
    pointing at staging. Hosts are compared, not whole URLs, so a page on the right site is not flagged.
    """
    if not URL.match(ran or ""):
        return None
    said = URL.findall(out or "")
    host = urlparse(ran).hostname
    if not said or any(urlparse(u).hostname == host for u in said):
        return None
    return said[0]


def coverage(argv: list[str], out: str, rc: int | None, label: str = "", target: str = "") -> str:
    """State what this probe actually managed to check, under whose identity, and against what.

    THE DURABLE FIX. A verdict without its coverage is unfalsifiable: "no problem found" and "I never
    looked" are the same sentence. Emitting the identity and scope means a null result can never wear
    a negative one's clothes, whoever runs it and whenever a key rotates.

    The check's name, scope and exit status are appended AFTER the original fields, so the line still
    starts the way it always did. A scope that differs from the registered target is flagged. One
    nobody can name is flagged too — its pass proves nothing — but its exit status stays on the line,
    so a check that failed still reads as a failure rather than "couldn't tell".
    """
    looked = "no" if (rc is None or BLIND.search(out or "")) else "yes"
    why = ""
    if looked == "no":
        m = BLIND.search(out or "")
        why = f" ({m.group(1).lower()})" if m else " (did not complete)"
    ran = scope(argv, target)
    status = "" if rc is None else f" · exit **{rc}**" + ("" if rc == 0 else " (failed)")
    line = (f"_coverage: ran `{' '.join(argv[:2])}` as **{getpass.getuser()}** · "
            f"actually looked: **{looked}**{why} · check **{label or 'unnamed'}** · "
            f"scope **{ran or 'unknown'}**{status}_")
    other = crosscheck(ran, out)
    if ran is None:
        line += ("\n_⚠️ scope unknown: nothing names what it ran against — a pass from it proves "
                 "nothing, but a failure is still a failure_")
    elif not target:
        line += "\n_⚠️ scope unverified: this check is registered for no target_"
    elif not _same(ran, target):
        line += (f"\n_⚠️ scope MISMATCH: registered for **{target}**, invoked against **{ran}** — its "
                 f"result describes the wrong target, not the system_")
    elif other:
        line += (f"\n_⚠️ scope MISMATCH: invoked against **{ran}**, but its output names only "
                 f"**{other}** — its result may describe the wrong target, not the system_")
    return line


def _run(argv: list[str], limit: int = 2500, label: str = "", target: str = "") -> tuple[str, str]:
    """Returns (output, coverage-line). Never just the output — see coverage()."""
    if not shutil.which(argv[0]) and not os.path.exists(argv[0]):
        out = f"(not installed: {argv[0]})"
        return out, coverage(argv, out, None, label, target)
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=90, env=env)
    except subprocess.TimeoutExpired:
        return "(timed out)", coverage(argv, "", None, label, target)
    except Exception as e:
        out = f"({type(e).__name__}: {e})"
        return out, coverage(argv, out, None, label, target)
    out = ((p.stdout or "") + (p.stderr or "")).strip() or "(no output)"
    out = redact(out[:limit])
    return out, coverage(argv, out, p.returncode, label, target)


def gather(alarm: str) -> str:
    """Collect evidence for this alarm. Fixed probes only, selected by keyword."""
    low = (alarm or "").lower()
    seen: set[str] = set()
    parts: list[str] = []
    for keys, label, argv, note, target in PROBES:
        if not any(k in low for k in keys) or label in seen:
            continue
        seen.add(label)
        out, cov = _run(argv, label=label, target=target)
        block = f"### {label}\n{cov}\n"
        if note:
            block += f"_Limitation: {note}_\n"
        block += "```\n" + out + "\n```"
        parts.append(block)
    for label, argv, target in ALWAYS:
        out, cov = _run(argv, limit=600, label=label, target=target)
        parts.append(f"### {label}\n{cov}\n```\n{out}\n```")
    return "\n\n".join(parts)


SYSTEM = """You are Moses, diagnosing an alarm raised by one of Brad Allen's monitoring personas on
his home server. You are NOT fixing anything and you have no ability to — you have been given
already-collected command output and nothing else.

Your job is to decide whether the alarm is REAL, and say what you actually know.

THE MOST IMPORTANT DISTINCTION, and the reason this exists: a check that could not look is not the
same as a problem. On 2026-08-14 an alarm said "cannot reach the push remote" when the remote was
fine — the check had run as the wrong user. If the evidence shows a probe was blind (no config, no
permission, not installed), that is evidence about the CHECK, not about the system. Say so. The same
goes for a probe flagged "scope MISMATCH": it looked at the wrong thing, so its result is not evidence
either way about the alarm's subject. A probe flagged "scope unknown" is only half that: its pass
proves nothing, but its failure (a nonzero exit) is still evidence of a problem.

Weigh the evidence against the alarm. If a direct probe contradicts the alarm, the direct probe
usually wins, and the interesting question becomes why the alarm disagreed.

Answer in exactly this form, nothing before or after:

VERDICT: real | false-alarm | unclear
SUMMARY: one sentence, what is actually true
WHY: the specific line(s) of evidence that decided it, quoted or named
NEXT: what Brad should do — or "nothing, the alarm was wrong" — and if you are not sure, say what
      single check would settle it

Be blunt and short. Never guess at a cause you cannot see in the evidence; "unclear" is a respectable
verdict and a better one than a confident wrong answer. Never suggest a fix you cannot support from
the evidence in front of you."""


def _invoke(prompt: str) -> dict:
    """Run the CLI as this user. Isolated so tests can stub it.

    The command comes from the one runner (M20, 2026-09-14): the "moses-diagnosis" profile in
    claude_runner.py, which offers no built-in tool and approves nothing.
    """
    import claude_runner
    argv = claude_runner.build_argv("moses-diagnosis", prompt, cli=CLI, system_prompt=SYSTEM)
    p = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_S)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "no output").strip()[:300])
    return json.loads(p.stdout)


VERDICT_LINE = re.compile(r"^\s*VERDICT:\s*(real|false-alarm|unclear)\b", re.I | re.M)


def parse_verdict(text: str) -> str:
    m = VERDICT_LINE.search(text or "")
    return m.group(1).lower() if m else "unclear"


def record(alarm_by: str, verdict: str, summary: str, channel: str = "", ts: str = "") -> None:
    """Append one diagnosis to the ledger.

    THE WHOLE POINT OF PHASE 1. Whether Moses should ever be allowed to act on an alarm depends on
    how often an alarm is real, and nobody knows that number yet — the only sample so far is one
    false positive. JSONL so it can be counted without parsing prose.
    """
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "day": date.today().isoformat(),
                "by": alarm_by, "verdict": verdict,
                "summary": summary[:300], "channel": channel, "ts": ts,
            }) + "\n")
    except OSError:
        pass


def tally() -> str:
    """How often has an alarm been real? The number Phase 2 should be decided against."""
    counts: dict[str, int] = {}
    try:
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                counts[json.loads(line).get("verdict", "unclear")] = \
                    counts.get(json.loads(line).get("verdict", "unclear"), 0) + 1
            except Exception:
                continue
    except OSError:
        return "no diagnoses recorded yet"
    total = sum(counts.values()) or 1
    return (f"{counts.get('real', 0)} real · {counts.get('false-alarm', 0)} false · "
            f"{counts.get('unclear', 0)} unclear  (of {total}, "
            f"{100 * counts.get('real', 0) // total}% real)")


def is_alarm(text: str) -> bool:
    """Is a persona reporting something WRONG?

    Negated counts are removed first — "0 failed" is not a failure, and matching it turned every
    clean backup report into a diagnosis. See NEGATED above for the line that caused it.
    """
    return bool(ALARM.search(NEGATED.sub(" ", text or "")))


def diagnose(alarm: str, alarm_by: str = "a persona") -> tuple[str, float]:
    """Produce a diagnosis for one alarm. Returns (report, cost). Never changes anything."""
    evidence = gather(alarm)
    prompt = (f"{alarm_by} raised this alarm:\n\n```\n{redact(alarm)[:2000]}\n```\n\n"
              f"Evidence gathered just now on the server:\n\n{evidence}")
    try:
        data = _invoke(prompt)
    except subprocess.TimeoutExpired:
        return "", 0.0
    except Exception as e:
        return f":warning: couldn't diagnose — {type(e).__name__}: {str(e)[:140]}", 0.0

    cost = float(data.get("total_cost_usd") or 0.0)
    if data.get("is_error"):
        return "", cost
    return redact((data.get("result") or "").strip()), cost


if __name__ == "__main__":          # `python3 diagnose.py tally`
    import sys
    print(tally() if (len(sys.argv) > 1 and sys.argv[1] == "tally") else __doc__.split("\n\n")[0])
