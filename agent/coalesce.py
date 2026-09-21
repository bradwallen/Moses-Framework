"""coalesce — answer a burst of messages ONCE, not once each.

THE INCIDENT (2026-08-15 12:51). Three messages landed in the same second — two from Brad, one from
Atlas. Slack's Socket Mode client dispatches on a thread pool, so three handlers ran concurrently.
Each read the channel, each decided independently to reply, each spent four seconds in the model,
and each posted. Brad got three answers to one conversational moment, carrying three differently
worded versions of the SAME proposed task, and the bow-out line fired twice on top.

WHY A TIMER ALONE DOES NOT FIX IT, which was the first instinct and worth writing down: if every
handler sleeps two seconds first, all three sleep in parallel and all three still post. The race is
not that the reply is too fast. It is that each message is handled independently and none of them can
see that the others are in flight. A delay narrows the window; it does not close it.

WHAT CLOSES IT IS A CLAIM. The first handler to arrive for a conversation takes ownership of the
window. Every other handler in that window hands its message over and exits immediately. When the
window elapses, the owner reads the channel once — by then it contains everything — and produces one
reply to the whole burst.

That is deterministic rather than probabilistic, and it makes the ANSWER BETTER, not just cheaper:
Moses responds to the conversation instead of to three partial views of it.

NOT A RATE LIMIT. This delays a reply by a couple of seconds; it never suppresses one. The distinction
matters here — a minimum-gap rule was tried in this project and had to be removed because it silently
muted him. Every message that arrives still gets answered; they just get answered together.
"""

from __future__ import annotations

import os
import threading
import time

# How long to keep collecting before answering. Long enough to swallow a burst, short enough to be
# invisible next to the model call it precedes (~4s) and the other agent's typical reply (1–9s).
WINDOW_S = float(os.environ.get("MOSES_COALESCE_WINDOW", "2.5"))


class Coalescer:
    """One claim per conversation, with everything else folded into it.

    A conversation is (channel, thread) — a thread and its parent channel are different rooms, so a
    burst in one must not swallow a message meant for the other.
    """

    def __init__(self, window: float | None = None) -> None:
        self._lock = threading.Lock()
        self._open: dict[tuple[str, str], dict] = {}
        self.window = WINDOW_S if window is None else window

    def claim(self, key: tuple[str, str], human: bool = True, now: float | None = None) -> bool:
        """True if the caller now OWNS this window and must produce the reply.

        False means a window is already open and this message has been folded into it — the caller
        should return immediately and say nothing.

        `human` records who spoke, because the reply-cap budget is deliberately asymmetric: a human
        message always gets an answer, a bot's is rationed. If ANY message in the window came from a
        person, the single reply that covers them all must be charged as a reply to a person.
        """
        now = time.time() if now is None else now
        with self._lock:
            w = self._open.get(key)
            if w and now < w["deadline"]:
                w["folded"] += 1
                w["human"] = w["human"] or human
                return False
            # No window, or the previous one elapsed. Either way this message starts a fresh one.
            self._open[key] = {"deadline": now + self.window, "folded": 0, "human": human}
            return True

    def wait(self, key: tuple[str, str]) -> None:
        """Sleep until the claimed window closes. Called only by the owner."""
        with self._lock:
            w = self._open.get(key)
        if not w:
            return
        remaining = w["deadline"] - time.time()
        if remaining > 0:
            time.sleep(remaining)

    def release(self, key: tuple[str, str]) -> tuple[int, bool]:
        """Close the window. Returns (messages folded in, whether a person spoke in it).

        RELEASED AS SOON AS THE WINDOW ELAPSES — before reading the channel and before the model
        call, not after the reply is posted. Holding it across those several seconds would fold a
        message that arrived while Moses was thinking, and folding it means dropping it: it would
        never be answered, because the reply it was folded into had already been composed without
        it. That is the silent mute this project removed once already. A message arriving after the
        window simply opens the next one and gets its own answer.
        """
        with self._lock:
            w = self._open.pop(key, None)
        return (int(w["folded"]), bool(w["human"])) if w else (0, True)

    def folded(self, key: tuple[str, str]) -> int:
        with self._lock:
            w = self._open.get(key)
            return int(w["folded"]) if w else 0


# The listener uses one shared instance; a per-call one would defeat the entire point.
COALESCER = Coalescer()
