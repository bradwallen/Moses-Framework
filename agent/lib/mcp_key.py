"""mcp_key — the keys that let a caller into the Moses MCP server.

WHY (2026-11-06): both MCP listeners answered anyone who could reach the port. The full one is on the
tailnet address, so every tailnet peer and every process on Reserve could call it, and the public one is
on loopback, so any local process skipped Cloudflare Access by calling it directly. Atlas found the same
hole in their own MCP on 2026-11-03 ("localhost is trusted"), and after MF8 it matters more here: Moses's
tokens sit behind that server.

TWO KEYS, ONE PER CALLER, so the log says who called and either can be revoked alone:

    listener   the Slack listener, which runs as the same account as the server. Kept in the server
               account's own config folder, mode 600. Nobody else reads it.
    operator   the operator's own jobs (the Monday blog agent runs as brad). Written to the server
               account's state folder, which the operator reads as MOSES_PEER_STATE (group moses-share,
               mode 640). The operator is trusted by Moses (MF8: every share flows one way), so this
               gives him nothing he could not already do; it shuts out everyone else.

Whoever needs a key first makes it (written whole, then linked into place, so two processes starting at
once agree on one). The server re-reads the files every few seconds and the listener before every turn,
so to REVOKE a key, delete its file: the next request makes a new one, and the old one stops working
within seconds. Nothing needs restarting.
Keys never go on a command line (argv is readable by every account) and never into a log.
"""
from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

NAMES = ("listener", "operator")


def key_path(name: str, config: Path, state: Path) -> Path:
    if name == "listener":
        return config / "mcp-keys" / "listener.key"
    if name == "operator":
        return state / "mcp-operator.key"
    raise ValueError(f"unknown MCP key {name!r}")


def _read(path: Path) -> str | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(fd) as f:
        value = f.read().strip()
    return value if len(value) >= 32 else None


def load(name: str, config: Path, state: Path) -> str | None:
    """The key, or None when it is missing, unreadable, a link, or too short to be one of ours."""
    return _read(key_path(name, config, state))


def load_or_create(name: str, config: Path, state: Path) -> str:
    """Read the key; make it first if there is none. Only the server account calls this."""
    path = key_path(name, config, state)
    found = _read(path)
    if found:
        return found
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if name == "listener" else 0o750)
    mode = 0o600 if name == "listener" else 0o640
    value = secrets.token_urlsafe(32)
    # Written whole to a private temp name, then linked to the real name: link() is atomic and fails if
    # the name exists, so a reader never sees a half-written key (review 2026-11-06: create-then-write
    # let a second process read an empty file and give up).
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(value + "\n")
        os.chmod(tmp, mode)                 # umask can narrow the mode open() was given
        try:
            os.link(tmp, path)
        except FileExistsError:
            # Another process made it first, or a bad file sits there (a link, an empty file). Read it;
            # never overwrite, because a file we did not make is not ours to replace.
            found = _read(path)
            if found:
                return found
            raise RuntimeError(f"{path} exists but is not a usable key; remove it and restart") from None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return value


def matches(presented: str, keys: dict[str, str]) -> str | None:
    """Which key the caller presented (its name), or None. Constant-time against every key."""
    hit = None
    for name, value in keys.items():
        if value and hmac.compare_digest(presented.encode(), value.encode()):
            hit = name
    return hit
