#!/usr/bin/env python3
"""What Moses says when he cannot act — the reply that cost Brad's trust on 2026-08-22.

    /home/brad/moses-venv/bin/python answer_test.py

He was asked to fix something, and answered with three confident untruths: that no model exists to
reason with, that every persona had never reported successfully (their reports were in the same
channel), and a roster dump that began mid-sentence. Being unable to act is a limitation. Being
wrong about your own state is a trust problem, and it is the one these pin.
"""
import os, sys, tempfile, json, pathlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CANON = tempfile.mkdtemp(prefix="moses-canon-")
OWN   = tempfile.mkdtemp(prefix="moses-own-")
MEM   = tempfile.mkdtemp(prefix="moses-mem-")

# The canonical store says everybody reported. The listener's OWN store is empty — exactly the
# split that produced the false "never reported successfully".
os.makedirs(f"{CANON}/personas", exist_ok=True)
for pid in ("moses", "birdeye", "therapist"):
    pathlib.Path(f"{CANON}/personas/{pid}.json").write_text(json.dumps(
        {"last_attempt": 9999999999, "last_success": 9999999999, "last_ok": True, "last_detail": "x"}))

# THE REGISTRY IS NOW A REAL STORE THIS TEST WRITES TO. Capture used to append to a flat
# ideas.md under MEM; since 2026-09-09 it files into the project registry, and without this line the
# fixture below lands in Brad's live one — which is exactly what happened the first time this ran.
REG = pathlib.Path(tempfile.mkdtemp(prefix="moses-reg-")) / "projects.json"
REG.write_text(json.dumps({"projects": [], "inbox": []}))

os.environ.update({
    "MOSES_REGISTRY": str(REG),
    "MOSES_REGISTRY_STATE": CANON,
    "MOSES_STATE": OWN,
    "MOSES_CHAT_CHANNELS": "C0BQ4PTUJV8",
    "SLACK_BOT_TOKEN": "xoxb-test", "SLACK_APP_TOKEN": "xapp-test",
})

import listener, dispatch                                     # noqa: E402
dispatch.MEMORY = pathlib.Path(MEM)

fails = []
def check(label, cond, detail=""):
    print(f"  {'✓' if cond else '✗'} {label}" + ("" if cond else f"  — {detail}"))
    if not cond: fails.append(label)

print("the reply no longer states things that are not true")

# 1. The registry is read from the canonical store, not the listener's own.
env_seen = listener._run(["/bin/sh", "-c", "printf %s \"$MOSES_STATE\""])
check("the CLI subprocess actually receives the canonical state path",
      env_seen == CANON, f"subprocess saw MOSES_STATE={env_seen!r}, wanted {CANON!r}")

REAL_RUN = listener._run                 # keep the real one; the truncation check needs it back
def fake_run(argv, limit=3500):
    return "(nobody is behind)"
listener._run = fake_run

reply = listener.answer("Moses, go ahead and task whoever you need and resolve the Therapist problem.")

# 2. It must not claim the model does not exist.
check("does not claim there is no model to reason with",
      "no model" not in reply.lower() and "don't call the claude api" not in reply.lower(), reply[:120])
check("names where the conversational half actually runs",
      "C0BQ4PTUJV8" in reply, reply[:160])

# 3. An instruction it cannot execute is written down rather than dropped.
def _inbox_text() -> str:
    return json.dumps(json.loads(REG.read_text()).get("inbox", []))

check("wrote the request down", "resolve the Therapist problem" in _inbox_text(), _inbox_text()[:200])
check("and nothing recreated the retired list", not (pathlib.Path(MEM) / "todo.md").exists())
check("and nothing recreated the retired idea FILE either",
      not (pathlib.Path(MEM) / "ideas.md").exists(),
      "ideas.md is back — capture is writing to a flat file again")
check("says so in the reply", "written the request down" in reply, reply[:200])

# 4. THE OTHER DIRECTION — capture must stay narrow, or the list becomes noise nobody reads.
before = _inbox_text()
for noise in [
    "hey moses",                                            # too short
    "what's the status of the deploy?",                     # a question
    "the deploy finished and everything looks fine to me",  # long, not a question, NOT an order
    "I was thinking about the Therapist alarm this morning",
    "nice work on that one yesterday, it came out well",
]:
    listener.answer(noise)
after = _inbox_text()
check("does NOT capture greetings or questions", before == after,
      f"added: {after[len(before):]!r}")

# 5. Truncation announces itself instead of emitting a headless fragment.
listener._run = REAL_RUN                            # restore the real one
long = listener._run(["/usr/bin/printf", "%s", "x" * 5000], limit=100)
check("truncation says it truncated", "truncated" in long, long[-60:])
check("truncation keeps the START, not the tail", long.startswith("x"), long[:20])

print(f"\n{'FAILED: ' + ', '.join(fails) if fails else 'all answer checks passed'}")
sys.exit(1 if fails else 0)
