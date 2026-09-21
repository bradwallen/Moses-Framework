"""people — who a Slack id belongs to, looked up rather than guessed.

Brad, 2026-09-17, in #the_4_horsemen, after Moses addressed him as Jon and told him a proposal was
waiting on somebody else's approval: *"Wow Moses... you thought I was Jon???"*

WHAT ACTUALLY HAPPENED. The channel transcript went to the model with speakers labeled by raw Slack
id (`U0BKN5JT3PC: Moses, go ahead with 0c1c831f`). The model did the only thing left to it and
inferred a name from the conversation — and inferred wrong. Atlas named the class of fault in the
same thread, about a different system: a check that reads the rendered conversation instead of the
record. This is that fault, in the identity layer, where it decides WHO IS ALLOWED TO APPROVE THINGS.

WHY IT WAS LEFT THAT WAY, and why that reason is gone. `build_prompt` carried a comment saying Moses
had no `users:read` scope, so names could not be resolved and inventing them "would be a fabrication
in the one place it is least excusable". The second half is exactly right and is why this module
falls back to the bare id. The first half was simply no longer true: measured against the listener's
own token on 2026-09-17, `users.info` answers, and returns "Brad Allen" for the id above. A comment
describing a limitation outlived the limitation, and the model paid for it.

THREE RULES HERE:
  1. A bot names itself on the message (`username` / `bot_profile.name`), so the other agents need no
     lookup at all and no scope.
  2. A human is resolved once and remembered, because a name does not change between two messages in
     the same conversation and the lookup should not cost one call per line.
  3. A lookup that fails returns the ID, never a guess. An unresolved id reads as an unresolved id;
     that is honest, and it is what the model saw before — the failure was never the fallback, it was
     that the fallback was ALL there was.
"""

from __future__ import annotations

import sys as _sys, pathlib as _pl  # noqa: E401
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent / "lib"))
import moses_env as _env  # noqa: E402 — whose home, where Moses lives, the operator's settings
import os
import threading

# Resolved names, id → display name. Slack ids are stable and a person's name changes at most a few
# times a year, so this is never invalidated in-process; the service restarts often enough.
_cache: dict[str, str] = {}
_lock = threading.Lock()

# THE OWNER IS KNOWN WITHOUT ASKING SLACK. The listener already has his id (it decides who may lift
# the safeword with it), so the one identity where being wrong costs the most — approvals are his
# alone — does not depend on an API call succeeding. Same default as listener.OWNER_ID.
OWNER_ID = _env.setting("MOSES_OWNER_SLACK_ID")
OWNER_NAME = _env.setting("MOSES_OWNER_NAME", "the owner")


def _seed() -> dict[str, str]:
    """Names known without a lookup: the owner, plus any MOSES_PEOPLE=U123:Name,U456:Name pairs."""
    seeded = {OWNER_ID: OWNER_NAME}
    for pair in _env.setting("MOSES_PEOPLE").split(","):
        if ":" in pair:
            uid, _, nm = pair.partition(":")
            uid, nm = uid.strip(), nm.strip()
            if uid and nm:
                seeded[uid] = nm
    return seeded


def name_for_id(user_id: str, web=None) -> str:
    """The person's name, or the id unchanged if it cannot be established.

    `web` is the slack_sdk client; without one this resolves only what is known locally, which keeps
    the tests off the network and means a Slack outage degrades to ids rather than taking a turn down.
    """
    uid = (user_id or "").strip()
    if not uid:
        return "someone"
    with _lock:
        if not _cache:
            _cache.update(_seed())
        hit = _cache.get(uid)
    if hit:
        return hit
    if web is None:
        return uid
    try:
        # Bounded and best-effort: a name is a nicety, and a turn must never hang or fail for one.
        resp = web.users_info(user=uid)
        info = (resp.get("user") or {}) if hasattr(resp, "get") else {}
        nm = (info.get("real_name") or info.get("name") or "").strip()
    except Exception:
        nm = ""
    if not nm:
        return uid
    with _lock:
        _cache[uid] = nm
    return nm


def speaker(event: dict, bot_user_id: str = "", web=None) -> str:
    """Who said this message, for the transcript the model reads.

    A bot carries its own display name on the message, which is why the other agents resolve with no
    scope and no call. A human goes through name_for_id.
    """
    if bot_user_id and event.get("user") == bot_user_id:
        return "You (Moses)"
    if event.get("username"):
        return str(event["username"])
    prof = event.get("bot_profile") or {}
    if prof.get("name"):
        return str(prof["name"])
    uid = event.get("user")
    return name_for_id(str(uid), web=web) if uid else "someone"


def is_owner(user_id: str) -> bool:
    """Whether this id is Brad's — by ID, never by the name shown in a transcript.

    The 2026-09-17 near-miss was not just cosmetic: Moses refused a confirmation because it believed
    the wrong person had sent it. Anything deciding what somebody may APPROVE compares ids here.
    """
    return (user_id or "").strip() == OWNER_ID


def _reset_for_test() -> None:
    with _lock:
        _cache.clear()
