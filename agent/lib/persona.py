"""persona — a persona's display name, emoji and title come from the ROSTER, not from a literal.

    from persona import name_of, emoji_of, title_of
    name_of("birdeye")      # "Birdeye", or whatever this operator called it

WHY. The roster declares each persona as `id` + `name` + `emoji` + `title`: the id is what the code
knows, the name is what the operator chose. Code that writes the name as a literal makes the roster
decorative and makes another operator accept one person's naming scheme. So display text looks the
name up by id, and renaming a persona is one line of the roster.

WHERE THE ROSTER IS, first readable wins: $MOSES_ROSTER, the operator's ~/.config/moses/roster.json,
/etc/moses/roster.json, this tree's agent/roster.json, and the framework's config/roster.example.json.

NEVER RAISES, NEVER GOES SILENT. A missing roster, a corrupt one, or an id that is not in it gives the
id with its first letter capitalized ("birdeye" -> "Birdeye"): plain, correct, and visibly
un-configured. Failing to look up your own name is not a reason to stop reporting.

The shell twin is persona.sh, with the same search order and the same fallback.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent          # agent/lib
_CACHE: tuple[tuple, dict] | None = None         # ((path, mtime), id -> entry)


def _operator_config() -> Path:
    """The operator's ~/.config/moses — through moses_env, so that running as root still finds the
    operator's roster rather than /root's. moses_env can refuse (root, no operator declared); a name
    lookup must not, so that refusal falls back to this process's own home."""
    try:
        import moses_env  # noqa: PLC0415 — lazy: only needed once, and it may refuse
        return Path(moses_env.CONFIG)
    except Exception:  # noqa: BLE001 — any failure here just means "use this process's home"
        return Path.home() / ".config" / "moses"


def _candidates() -> list[Path]:
    out = []
    if os.environ.get("MOSES_ROSTER"):
        out.append(Path(os.environ["MOSES_ROSTER"]))
    out += [_operator_config() / "roster.json",
            Path.home() / ".config" / "moses" / "roster.json",
            Path("/etc/moses/roster.json"),
            _HERE.parent / "roster.json",
            _HERE.parent.parent / "config" / "roster.example.json"]
    return out


def roster_path() -> Path | None:
    for p in _candidates():
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def _load() -> dict:
    """id -> roster entry. Cached until the roster file changes, so a long-running listener picks up a
    rename without a restart and does not re-read the file on every message."""
    global _CACHE
    p = roster_path()
    try:
        key = (str(p), p.stat().st_mtime_ns) if p else (None, None)
    except OSError:
        key = (str(p), None)
    if _CACHE is not None and _CACHE[0] == key:
        return _CACHE[1]

    out: dict = {}

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("id"), str) and "name" in o:
                out[o["id"]] = o
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    if p:
        try:
            walk(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 — a corrupt roster degrades to the fallback, never raises
            out = {}
    _CACHE = (key, out)
    return out


def _field(persona_id: str, field: str) -> str:
    try:
        v = (_load().get(persona_id) or {}).get(field)
    except Exception:  # noqa: BLE001 — belt and braces: a display lookup never raises
        return ""
    return v if isinstance(v, str) else ""


def _fallback(persona_id: str) -> str:
    return (persona_id or "?")[:1].upper() + (persona_id or "?")[1:]


def name_of(persona_id: str, fallback: str = "") -> str:
    """The display name; else `fallback`; else the id capitalized."""
    return _field(persona_id, "name").strip() or fallback or _fallback(persona_id)


def emoji_of(persona_id: str, fallback: str = ":robot_face:") -> str:
    """The Slack emoji, e.g. ":eagle:". Defaults to something neutral rather than to nothing — Slack
    renders an empty icon_emoji as the app's own avatar, which undoes the per-message identity."""
    return _field(persona_id, "emoji").strip() or fallback


def title_of(persona_id: str, fallback: str = "") -> str:
    """The one-line title, e.g. "Ops"; else `fallback` (empty by default)."""
    return _field(persona_id, "title").strip() or fallback
