"""guard — the harness-level permission gate for Moses's agents (safety layer 3 of 4).

WHAT THIS IS, AND WHAT IT IS NOT
This decides, for every tool call an agent tries to make, whether it runs. It is a guard against
MISTAKES and PROMPT INJECTION, not against an adversary. A determined attacker who can already put
text in front of the model can obfuscate a command past any pattern matcher (base64, variable
indirection, a helper script written in a previous turn). Do not mistake this file for a boundary.

The boundary is elsewhere, and there are two:
  * /etc/sudoers.d/agents  — the agent user simply cannot run what is not enumerated there (phase 2)
  * SandboxNetworkConfig.deniedDomains — api.anthropic.com is refused at the network layer, so the
    Claude-API rule does not depend on catching every spelling of `curl` in a shell string

This layer exists because it is the only one that can explain itself. sudo says "not permitted";
this says which rule fired and why, which is what makes a denial actionable instead of mysterious.

THE ASYMMETRY THAT MAKES IT SAFE TO EXTEND
Deny beats allow, and anything unmatched falls through to ASK — never to allow. So adding an entry
to ALLOW can only reduce prompting; it can never widen unattended execution. A missing allow costs
a confirmation. A deny that fails to match leaves a destructive command armed. That is why the
tests in guard_test.py assert the deny cases, and why they run before any privilege is granted.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Literal

Decision = Literal["allow", "deny", "ask"]


@dataclass(frozen=True)
class Verdict:
    decision: Decision
    rule: str        # which rule fired, for the audit trail
    reason: str      # what to tell Brad (or the model, on a deny)


# ── Paths that must never be read, written, or handed to a subprocess ─────────
# Credentials first: an agent that can read Viatica's .env can reach the production Anthropic key,
# which is the specific thing Brad ruled out. The agent's own credential is in this list too — it
# has no reason to read the file it is already authenticated by, and doing so is how a key ends up
# echoed into a Slack message.
SECRET_PATHS = [
    r"/etc/moses/[a-z]+\.env",
    # One product's .env and one persona's config directory used to be named here. Both were REDUNDANT
    # — the general rule below matches any path ending in .env, which is strictly broader — and a
    # framework safety list that names one person's repo implies the protection is specific when it is
    # not. guard_test.py still denies reading that exact product .env, through this rule.
    r"\.env(\.|$)",
    r"/home/[a-z]+/\.ssh/",
    r"/root/",
    r"oauth_tokens\.json",
    r"code-server/config\.yaml",
]

# Data whose loss is unrecoverable. Named explicitly rather than by a general "destructive" rule,
# because the point is to be specific about what is irreplaceable: the drives hold the only copy of
# the BCM footage and the family photos, and the memory corpus is the project brain.
IRREPLACEABLE = [
    r"/mnt/",
    # ANY operator's backups and memory corpus, not one named home's. A rule that protected only one
    # home would have silently protected nobody else's.
    r"/home/[a-z][a-z0-9_-]*/Backups",
    r"/home/[a-z][a-z0-9_-]*/\.claude/memory",
    r"/opt/moses/memory",
]

# ── Command patterns that are denied outright ────────────────────────────────
# Each entry is (regex, rule-name, human reason). Matched against every SEGMENT of a command line,
# not just the first — `ls; mkfs.ext4 /dev/sdb` must not pass because it starts with `ls`.
DENY_COMMANDS: list[tuple[str, str, str]] = [
    # Irreversible storage operations. These are not reachable through bash by design; they exist
    # as typed tools (prepare_new_drive) so the harness can show which device and what is on it.
    (r"\b(mkfs(\.\w+)?|mkswap|wipefs|shred|blkdiscard)\b", "storage.format",
     "formatting/erasing a device is irreversible — use the prepare_new_drive tool, which confirms first"),
    (r"\b(parted|fdisk|sfdisk|sgdisk|cfdisk)\b", "storage.partition",
     "partitioning is irreversible — use the prepare_new_drive tool"),
    (r"\bdd\b.*\bof=/dev/", "storage.dd",
     "dd to a block device destroys it with no undo"),

    # Deleting the irreplaceable.
    (r"\brm\b.*\s(-\w*[rf]\w*\s+)+.*(" + "|".join(IRREPLACEABLE) + ")", "delete.irreplaceable",
     "recursive delete inside irreplaceable data"),
    (r"\brsync\b.*--delete.*" + "|".join(IRREPLACEABLE), "delete.rsync",
     "rsync --delete against irreplaceable data can empty the destination"),

    # Turning monitoring or backups off. A stopped service is how a system goes quiet without
    # anything reporting it — the exact failure the standup exists to catch.
    (r"\bsystemctl\b.*\b(stop|disable|mask)\b", "service.stop",
     "stopping or disabling a service needs confirmation — it can silence monitoring"),
    (r"\bcrontab\b.*\s-r\b", "service.crontab",
     "crontab -r wipes the schedule"),

    # The Claude API rule. Belt-and-braces with the network sandbox: this catches the intent in a
    # readable way, deniedDomains catches the packet.
    (r"\bapi\.anthropic\.com\b", "api.anthropic",
     "agents must not call the Claude API directly — cost and Viatica rate-limit interference"),
    (r"(^|/)(claude|ant)\s", "api.cli",
     "the claude/ant CLIs spend against the API outside the agent's metered budget"),
    (r"/(itinerary/(generate|import)|api/ingest|api/slack/command)\b", "api.viatica",
     "these Viatica routes invoke Claude — calling them costs money and can disturb itinerary parsing"),

    # Git history rewriting on published work.
    (r"\bgit\b.*\bpush\b.*(--force|-f)\b", "git.force",
     "force-push rewrites published history"),
    (r"\bgit\b.*\breset\b.*--hard", "git.reset",
     "git reset --hard discards uncommitted work irreversibly"),
    (r"\bgit\b.*\bcommit\b.*--amend", "git.amend",
     "amending can rewrite a published commit"),

    # Bypassing safety checks is never the fix.
    (r"--no-verify\b", "bypass.hooks", "--no-verify skips the checks that exist to catch this"),
    (r"--accept-data-loss\b", "bypass.prisma", "--accept-data-loss outside a documented deploy"),
    (r"\bchmod\b\s+(-\w+\s+)*777\b", "bypass.chmod", "chmod 777 is never the right fix"),

    # Reading credentials.
    (r"(" + "|".join(SECRET_PATHS) + ")", "secret.read",
     "that path holds credentials"),
]

# ── Commands that are safe enough to run unattended ──────────────────────────
# Read-only inspection, plus the enumerated ops Brad approved as "fairly liberal". Anything not
# here still works — it just asks first.
ALLOW_COMMANDS: list[tuple[str, str]] = [
    (r"^(ls|cat|head|tail|wc|file|stat|readlink|realpath|basename|dirname)\b", "read.file"),
    (r"^(grep|rg|find|fd|awk|sed|sort|uniq|cut|tr|jq|diff|comm)\b", "read.text"),
    # df/du/lsblk/findmnt are read-only whatever arguments they take. `mount` is not — bare it
    # lists, with arguments it mounts — so it is anchored to the bare form and otherwise asks.
    (r"^(df|du|lsblk|findmnt|blkid)\b", "read.disk"),
    (r"^mount$", "read.mounts"),
    (r"^(ps|top|pgrep|uptime|free|id|whoami|hostname|date|uname|env)\b", "read.proc"),
    (r"^(systemctl)\s+(status|is-active|is-enabled|list-units|list-timers|cat|show)\b", "read.service"),
    # Restarting a NAMED, documented unit is commandment #17's "act immediately" case. The unit list
    # is explicit on purpose: `systemctl restart` with an arbitrary argument is not the same act, and
    # a restart of something unlisted should still get a look.
    (r"^systemctl\s+restart\s+(" + "|".join([
        r"moses\.service", r"moses-morning\.(service|timer)", r"reserve-backup\.(service|timer)",
        r"idrivecron\.service", r"smbd\.service", r"code-server@brad\.service",
    ]) + r")\s*$", "ops.restart-known"),
    (r"^journalctl\b", "read.logs"),
    (r"^(git)\s+(status|log|diff|show|branch|remote|rev-parse|blame)\b", "read.git"),
    (r"^(ss|ip|ping|dig|host)\b", "read.net"),
    (r"^(birdeye|birdeye-report|moses)\b", "ops.own-tools"),
    (r"^(echo|printf|true|false|test|\[)\b", "read.trivial"),
    (r"^(python3|node)\s+-c\b", "read.eval"),   # still passes through segment deny checks below
]

# Tools other than Bash. File writes are allowed but confined; network reads are allowed except to
# the API. Anything not listed asks.
TOOL_POLICY: dict[str, Decision] = {
    "Read": "allow", "Glob": "allow", "Grep": "allow", "WebSearch": "allow",
    "TodoWrite": "allow", "NotebookRead": "allow",
    "Write": "ask", "Edit": "ask", "NotebookEdit": "ask",
    "WebFetch": "allow",     # domain-checked below
    "Bash": "allow",         # command-checked below
}

_SEGMENT_SPLIT = re.compile(r"\|\||&&|[;|&]|\$\(|\)|`|\n")


def _segments(command: str) -> list[str]:
    """Split a shell string into the individual commands it will actually run.

    Checking only the first word is the classic bypass: `ls; mkfs.ext4 /dev/sdb` starts with `ls`.
    Substitutions ($(...) and backticks) are split out too, since they execute as well.
    """
    parts = [p.strip() for p in _SEGMENT_SPLIT.split(command)]
    return [p for p in parts if p]


def _strip_prefixes(segment: str) -> str:
    """Remove sudo / env-assignment prefixes so the real command is what gets matched."""
    try:
        words = shlex.split(segment)
    except ValueError:
        words = segment.split()
    i = 0
    while i < len(words):
        w = words[i]
        if w in ("sudo", "doas", "command", "builtin", "exec", "nohup", "time", "env"):
            i += 1
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", w):
            i += 1          # FOO=bar cmd
        elif w.startswith("-") and i > 0:
            break
        else:
            break
    rest = " ".join(words[i:]) if i < len(words) else ""
    return re.sub(r"^\S*/", "", rest)   # /sbin/mkfs.ext4 -> mkfs.ext4


def check_command(command: str) -> Verdict:
    """Decide a single Bash command string."""
    segs = _segments(command)
    if not segs:
        return Verdict("ask", "bash.empty", "empty command")

    # Deny wins, and it is checked against the RAW segment as well as the stripped one — a deny
    # pattern that mentions a path must still fire when the command is `sudo /sbin/mkfs …`.
    for seg in segs:
        stripped = _strip_prefixes(seg)
        for pattern, rule, reason in DENY_COMMANDS:
            if re.search(pattern, seg, re.I) or re.search(pattern, stripped, re.I):
                return Verdict("deny", rule, reason)

    # Allow only if EVERY segment is independently allowed. One unknown segment makes the whole
    # line ask — a chained command is only as safe as its least-known part.
    rules: list[str] = []
    for seg in segs:
        stripped = _strip_prefixes(seg)
        hit = next((r for p, r in ALLOW_COMMANDS if re.search(p, stripped, re.I)), None)
        if not hit:
            return Verdict("ask", "bash.unknown", f"`{stripped[:60]}` is not on the allowlist")
        rules.append(hit)
    return Verdict("allow", "+".join(sorted(set(rules))), "read-only or enumerated safe operation")


def check_path(path: str) -> Verdict | None:
    """Deny reads/writes of credential files. Returns None when the path is fine."""
    for pat in SECRET_PATHS:
        if re.search(pat, path):
            return Verdict("deny", "secret.read", f"{path} holds credentials")
    return None


def check(tool_name: str, tool_input: dict) -> Verdict:
    """The single entry point. Every tool call goes through here."""
    if tool_name == "Bash":
        return check_command(str(tool_input.get("command", "")))

    if tool_name == "WebFetch":
        url = str(tool_input.get("url", ""))
        if re.search(r"\bapi\.anthropic\.com\b", url, re.I):
            return Verdict("deny", "api.anthropic", "agents must not call the Claude API directly")
        if re.search(r"/(itinerary/(generate|import)|api/ingest|api/slack/command)\b", url, re.I):
            return Verdict("deny", "api.viatica", "that Viatica route invokes Claude")
        return Verdict("allow", "web.fetch", "read-only web fetch")

    for key in ("file_path", "path", "notebook_path"):
        if key in tool_input:
            bad = check_path(str(tool_input[key]))
            if bad:
                return bad

    decision = TOOL_POLICY.get(tool_name)
    if decision is None:
        return Verdict("ask", "tool.unknown", f"{tool_name} is not in the tool policy")
    return Verdict(decision, f"tool.{tool_name.lower()}", f"{tool_name} policy")
