"""The safeword. Moses's off switch, deliberately the dumbest code in the system.

Brad, 2026-08-13: *"Maybe with a safeword to stop him if I need to??"*

DESIGN RULES, and each one is load-bearing:

1. **It is checked BEFORE the model is called, in deterministic code.** A stop word that the model is
   asked to honor is not a stop switch — it is a request, and the thing you need stopped is exactly
   the thing you have stopped trusting. Nothing here consults a model.

2. **It is a FILE, so it survives everything.** A restart, a crash, a redeploy, a lost Slack
   connection. If Moses comes back up, he comes back up silent. An in-memory flag would quietly
   un-stop him on the next restart, which is the worst possible moment.

3. **Anyone in the channel can stop him; only Brad can restart him.** Stopping must be frictionless
   in the moment you need it — Jon should not have to find Brad. Restarting is a deliberate act by
   the person who owns the bill.

4. **Silence means silence** — not just the conversational half. If he has been told to stop, he
   stops, including his deterministic replies. A bot that keeps answering after the safeword has not
   stopped; it has changed the subject.

5. **Two forms, because the obvious design has a false-trigger.** "standdown" as one word is not
   English and matches anywhere. The natural two-word "stand down" only fires when it is the WHOLE
   message (an optional "Moses," in front is fine).

   The first version matched "stand down" anywhere, and a listener test caught the consequence
   immediately: *"we should stand down the old Pi service"* silenced him. That is an ordinary
   sentence in a channel about infrastructure. A safeword that fires on shop talk gets disabled
   within a week, and a disabled safeword is worse than none — so the emergency form stays trivially
   easy to type, and the ambiguous one has to stand alone.

The one thing it deliberately CANNOT do is claw back a request already in flight to the API. That
turn may still cost money. The switch stops the next one, and every one after it.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

STATE = Path(os.environ.get("MOSES_STATE", "/var/lib/moses"))
FLAG = STATE / "silence"

# "blackout", "stop", "halt" and "cease" are all disqualified by the very conversations this guards —
# a channel about servers says those constantly. "standdown" as one word is not English, so it can
# match anywhere; "stand down" is, so it must stand alone.
_ANYWHERE = re.compile(r"\bstanddown\b", re.I)
# An optional "Moses," or an @mention in front, the phrase, and nothing else but punctuation.
_ALONE = re.compile(r"^\s*(?:<@[A-Z0-9]+>\s*)?(?:moses\s*[,:]?\s*)?stand\s+down\s*[.!]*\s*$", re.I)

_RESUME_ANY = re.compile(r"\bmoses\s+resume\b", re.I)
_RESUME_ALONE = re.compile(r"^\s*(?:<@[A-Z0-9]+>\s*)?(?:moses\s*[,:]?\s*)?as\s+you\s+were\s*[.!]*\s*$", re.I)


def is_stop(text: str) -> bool:
    t = text or ""
    return bool(_ANYWHERE.search(t) or _ALONE.match(t))


def is_resume(text: str) -> bool:
    t = text or ""
    return bool(_RESUME_ANY.search(t) or _RESUME_ALONE.match(t))


def silenced() -> bool:
    return FLAG.exists()


def reason() -> str:
    """Who silenced him and when, for the message he posts when asked while muted."""
    try:
        return FLAG.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def engage(who: str = "", where: str = "") -> str:
    """Silence Moses. Returns the line to post back so the stop is visibly acknowledged.

    Writing the flag is best-effort in one sense only: if it CANNOT be written, that is reported as
    a failure to stop rather than swallowed. A safeword that silently does nothing is worse than no
    safeword, because it buys false confidence at the exact moment confidence matters.
    """
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        FLAG.write_text(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} by {who or 'someone'} in {where or '?'}",
                        encoding="utf-8")
    except OSError as e:
        return (f":warning: I could NOT write my own stop flag ({e}). I am probably still live — "
                f"stop the service on Reserve: `sudo systemctl stop moses`")
    return ":mute: Standing down. I'll stay quiet — including my usual commands — until Brad says " \
           "*as you were*."


def release(who: str = "") -> str:
    try:
        FLAG.unlink(missing_ok=True)
    except OSError as e:
        return f":warning: couldn't clear the stop flag ({e}) — it is at `{FLAG}`"
    return ":speaking_head_in_silhouette: Back. (Released by %s.)" % (who or "Brad")


if __name__ == "__main__":                  # a way in that does not depend on Slack working
    import sys
    arg = sys.argv[1] if len(sys.argv) > 1 else "status"
    if arg == "stop":
        print(engage("cli", "terminal"))
    elif arg in ("release", "resume"):
        print(release("cli"))
    else:
        print(f"silenced: {silenced()}" + (f" ({reason()})" if silenced() else ""))
