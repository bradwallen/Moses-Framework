"""persona — a persona's display name and emoji come from the ROSTER, not from a literal.

    from persona import name_of, emoji_of
    name_of("ops-jobs")     # "Birdeye", or whatever the operator called it

The Python twin of lib/persona.sh, with the same contract and the same fallback: an id that cannot
be resolved becomes the id with its first letter capitalized. A missing or malformed roster must
never stop the agent — failing to look up your own name is not a reason to go silent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_CACHE: dict | None = None


def roster_path() -> Path | None:
    for p in (os.environ.get("MOSES_ROSTER"),
              str(Path.home() / ".config/moses/roster.json"),
              "/etc/moses/roster.json",
              str(Path(__file__).resolve().parent.parent / "config/roster.example.json")):
        if p and Path(p).is_file():
            return Path(p)
    return None


def _load() -> dict:
    """id -> entry. Cached: this is read on every persona-attributed message."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    out: dict = {}

    def walk(o):
        if isinstance(o, dict):
            if "id" in o and "name" in o:
                out[o["id"]] = o
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    p = roster_path()
    if p:
        try:
            walk(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    _CACHE = out
    return out


def name_of(persona_id: str, fallback: str = "") -> str:
    e = _load().get(persona_id) or {}
    return e.get("name") or fallback or persona_id.capitalize()


def emoji_of(persona_id: str, fallback: str = ":robot_face:") -> str:
    e = _load().get(persona_id) or {}
    return e.get("emoji") or fallback
