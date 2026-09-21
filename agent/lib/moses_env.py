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
