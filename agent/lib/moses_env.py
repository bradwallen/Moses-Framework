"""moses_env — where the operator's things are, and the operator's own settings. One place.

WHY (2026-09-21). The code named one person's machine in ~230 places: `/home/brad/...` as a default in
53 files, his Slack ids, his tailnet address, his product. Every one of them had to be scrubbed by hand
in the public framework, and the scrubbing was undone by the next copy across. So nothing personal is
written in the code any more: paths come from the operator's home, and everything else from the
operator's settings file.

    from moses_env import HOME, ROOT, CONFIG, MEMORY, STATE, setting

PRECEDENCE, highest first: the process environment, then `~/.config/moses/moses.env` (KEY=value lines,
no secrets — those stay in their own files), then the default the caller passes. The settings file is
read by the code itself rather than handed in by each systemd unit, because Moses is started from a
dozen places — timers, the CLI, Knight, the standup, sudo — and a setting that only some of them
receive is the drift this replaced.

WHOSE HOME, WHEN RUNNING AS ROOT. $HOME is /root there, which is never the operator. So, as root: an
explicit MOSES_OPERATOR_USER, else the person who ran sudo, else /etc/moses/operator (one line, the
user name), else account 1000 — the first account, which is what this code hard-coded before — and it
says on stderr that it assumed so. With no account 1000 either, it raises rather than pick someone.
"""
from __future__ import annotations

import os
import pwd
from pathlib import Path


def _operator_home() -> Path:
    explicit = os.environ.get("MOSES_HOME")
    if explicit:
        return Path(explicit)
    if os.geteuid() != 0:
        return Path(os.environ.get("HOME") or pwd.getpwuid(os.geteuid()).pw_dir)
    user = os.environ.get("MOSES_OPERATOR_USER") or ""
    if not user and os.environ.get("SUDO_USER", "root") != "root":
        user = os.environ["SUDO_USER"]
    if not user:
        try:
            user = Path("/etc/moses/operator").read_text().strip()
        except OSError:
            user = ""
    if not user:
        # The last resort is DECLARED, not inferred: account 1000, the first one created — which is
        # what this code hard-coded before (XDG_RUNTIME_DIR=/run/user/1000) and what a one-operator
        # machine means. Said out loud every time, so it is never mistaken for a configured answer.
        try:
            user = pwd.getpwuid(1000).pw_name
        except KeyError:
            raise RuntimeError("running as root with no operator: set MOSES_OPERATOR_USER, run through "
                               "sudo, or write the user name to /etc/moses/operator") from None
        import sys
        print(f"moses_env: no operator declared — using account 1000 ({user}); "
              "write the user name to /etc/moses/operator to say so explicitly", file=sys.stderr)
    return Path(pwd.getpwnam(user).pw_dir)


HOME = _operator_home()
CONFIG = Path(os.environ.get("MOSES_CONFIG_DIR") or HOME / ".config" / "moses")
SETTINGS_FILE = Path(os.environ.get("MOSES_SETTINGS") or CONFIG / "moses.env")


def _read_settings(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text()
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v
    return out


_SETTINGS = _read_settings(SETTINGS_FILE)


def setting(key: str, default: str = "") -> str:
    """The process environment, then the operator's settings file, then `default`."""
    v = os.environ.get(key)
    if v is not None:
        return v
    return _SETTINGS.get(key, default)


# The Moses checkout this file belongs to: agent/lib/moses_env.py → the repo root.
ROOT = Path(setting("MOSES_ROOT") or Path(__file__).resolve().parents[2])
MEMORY = Path(setting("MOSES_MEMORY_DIR") or HOME / ".claude" / "memory")
STATE = Path(setting("MOSES_STATE") or HOME / ".local" / "state" / "moses")

# MF8 (2026-11-05): when Moses runs as his own account, every share flows ONE way. The corpus is read-only
# to him, so the registry he writes lives in a folder he owns (MOSES_REGISTRY); the transcript index is
# built by brad and read-only to him (MOSES_TRANSCRIPTS_DB); and each account writes its own state and
# reads the other's from MOSES_PEER_STATE, never writing there. Unset, everything is where it always was.
REGISTRY = Path(setting("MOSES_REGISTRY") or MEMORY / "projects.json")
TRANSCRIPTS_DB = Path(setting("MOSES_TRANSCRIPTS_DB") or ROOT / "mcp" / "transcripts.db")
# What Moses writes down for Brad (look-into-further.md): his own folder once the corpus is read-only.
DATA = Path(setting("MOSES_DATA") or MEMORY)
PEER_STATE = Path(setting("MOSES_PEER_STATE")) if setting("MOSES_PEER_STATE") else None


def read_state(name: str) -> str | None:
    """A state file by name: this account's own first, then the other account's (MOSES_PEER_STATE).

    The other account is less trusted in one direction or the other, so its file is opened without
    following a symlink and must be a regular file directly in that folder: a link planted there must
    never make this account read (and then report or post) one of its own secrets. None when absent.
    """
    if "/" in name or name.startswith("."):
        raise ValueError(f"not a plain state file name: {name!r}")
    own = STATE / name
    try:
        return own.read_text(encoding="utf-8")
    except FileNotFoundError:
        return read_peer(name)


def read_peer(name: str) -> str | None:
    """Only the other account's copy of a state file (see read_state), or None."""
    if "/" in name or name.startswith("."):
        raise ValueError(f"not a plain state file name: {name!r}")
    if PEER_STATE is None:
        return None
    import stat as _stat
    try:
        fd = os.open(PEER_STATE / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except (FileNotFoundError, OSError):
        return None
    try:
        if not _stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, encoding="utf-8") as fh:
            fd = -1
            return fh.read()
    finally:
        if fd >= 0:
            os.close(fd)


def product_name() -> str:
    """What the operator's product is called, for prompt and message text: MOSES_PRODUCT_NAME, else
    "the product". Read live, so a test or a restarted service sees the current setting."""
    return setting("MOSES_PRODUCT_NAME", "the product").strip() or "the product"


def product_label() -> str:
    """The product's name with MOSES_PRODUCT_DESCRIPTION after it in parentheses, when there is one —
    for the sentence that introduces the product to a model. Just the name otherwise."""
    desc = setting("MOSES_PRODUCT_DESCRIPTION").strip()
    return f"{product_name()} ({desc})" if desc else product_name()


def claude_cli() -> str:
    """The `claude` CLI: MOSES_CHAT_CLI, else ~/.local/bin/claude (a per-user install, which updates
    without root — how the author runs it), else whatever PATH finds, else that per-user path anyway so
    the error names a real place to put it."""
    explicit = setting("MOSES_CHAT_CLI")
    if explicit:
        return explicit
    local = HOME / ".local" / "bin" / "claude"
    if os.access(local, os.X_OK):
        return str(local)
    import shutil
    return shutil.which("claude") or str(local)
