#!/usr/bin/env python3
"""people_test — the identity layer, including the failure that produced it.

`bash knight/test-*.sh`-style standalone script, like every other test here: run it directly.

The case that matters is the last one. On 2026-09-17 Moses read a raw Slack id in the transcript,
guessed "Jon" from context, and told Brad his own approval belonged to somebody else. So these check
both halves: that a known id resolves to a name, and that an unknown one stays an id rather than
becoming a plausible-sounding guess.
"""

import os
import sys
# The owner is the operator's setting; this test declares its own rather than reading the machine's,
# so it answers the same on a fresh checkout as on the author's.
os.environ["MOSES_OWNER_SLACK_ID"] = "U0BKN5JT3PC"
os.environ["MOSES_OWNER_NAME"] = "Brad"

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import people  # noqa: E402

pass_n = fail_n = 0


def ok(label, cond, detail=""):
    global pass_n, fail_n
    if cond:
        print(f"  ok    {label}")
        pass_n += 1
    else:
        print(f"  FAIL  {label}\n     {detail}")
        fail_n += 1


class FakeWeb:
    """Stands in for slack_sdk's client. Counts calls, so the cache can be proven to be a cache."""

    def __init__(self, names=None, raises=False):
        self.names = names or {}
        self.raises = raises
        self.calls = 0

    def users_info(self, user):
        self.calls += 1
        if self.raises:
            raise RuntimeError("slack is down")
        if user not in self.names:
            return {"ok": False}
        return {"ok": True, "user": {"real_name": self.names[user]}}


print("people")

# ── The owner is known without asking anyone ────────────────────────────────
people._reset_for_test()
ok("Brad's id resolves to his name with no lookup at all",
   people.name_for_id(people.OWNER_ID) == "Brad", people.name_for_id(people.OWNER_ID))
ok("and is recognized as the owner by id",
   people.is_owner(people.OWNER_ID) and not people.is_owner("U_SOMEONE_ELSE"))

# ── A human is looked up, once ──────────────────────────────────────────────
people._reset_for_test()
web = FakeWeb({"U_SAM": "Sam Example"})
ok("an unknown human is resolved through Slack", people.name_for_id("U_SAM", web=web) == "Sam Example")
people.name_for_id("U_SAM", web=web)
people.name_for_id("U_SAM", web=web)
ok("and remembered, so a transcript costs one lookup per person", web.calls == 1, f"{web.calls} calls")

# ── A bot names itself; no scope, no call ───────────────────────────────────
people._reset_for_test()
web = FakeWeb()
ok("a bot is named from the message itself",
   people.speaker({"username": "Atlas", "user": "U_BOT"}, web=web) == "Atlas")
ok("a bot with only a profile name still resolves",
   people.speaker({"bot_profile": {"name": "Knight"}}, web=web) == "Knight")
ok("and neither needed a lookup", web.calls == 0, f"{web.calls} calls")

# ── Moses knows himself ─────────────────────────────────────────────────────
ok("his own messages are labeled as his",
   people.speaker({"user": "U_ME", "text": "hi"}, bot_user_id="U_ME") == "You (Moses)")

# ── THE 2026-09-17 FAILURE: never invent a name ─────────────────────────────
people._reset_for_test()
web = FakeWeb({}, raises=False)
got = people.name_for_id("U_STRANGER", web=web)
ok("an id Slack cannot resolve stays an id, rather than becoming a guess",
   got == "U_STRANGER", got)

people._reset_for_test()
web = FakeWeb(raises=True)
got = people.name_for_id("U_STRANGER", web=web)
ok("and a Slack outage degrades to the id instead of taking the turn down", got == "U_STRANGER", got)

people._reset_for_test()
ok("with no client at all, it still answers", people.name_for_id("U_X") == "U_X")

# A wrong name is worse than no name specifically because of what it decides.
people._reset_for_test()
ok("being called something in the transcript does not make you the owner",
   not people.is_owner("U_NOT_BRAD"))

print(f"\n{pass_n} ok, {fail_n} failed")
sys.exit(1 if fail_n else 0)
