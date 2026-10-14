#!/usr/bin/env bash
# gate-handoff.sh — refuse to hand Brad work without stating the diligence first.
#
# Stop hook. Reads the drafted response and blocks it when:
#   1. it asks Brad to run a command / open a console / click / paste, without a diligence block;
#   2. it CLAIMS diligence but the turn made no tool calls at all (a claim with no work behind it);
#   3. the ask is buried in a non-final message, so it scrolls away behind later tool calls.
#
# WHY (Brad, 2026-08-13): *"You not verifying things costs me time at the keyboard as well as session
# time that I'm paying for … Do your due diligence and that due diligence MUST be gated before having
# me go look at something or run a command somewhere."*
#
# WHY A GATE AND NOT A COMMANDMENT: the commandments were loaded in full, on every turn this went
# wrong, and did not stop it. Prose is the weakest layer — Brad's own agent-action policy says so.
#
# WHY IT LOGS: Atlas (Jon's overseer, which fought the same failure) made the sharpest point — a gate
# never seen going red is decoration, and a shape-only gate decays as the model learns the sentence
# that passes. Every block is counted so the underlying failure rate is VISIBLE rather than assumed,
# and `moses-gate-selftest` re-proves the patterns weekly.
#
# WHAT IT CANNOT DO: judge whether the verification was any good. Check 2 is the partial answer —
# it makes the empty version mechanically detectable — but a thin claim with a token tool call still
# passes. It reduces the failure surface; it does not guarantee.
#
# FAILS OPEN. A broken gate must never wedge a session: any parse problem exits 0.

set -uo pipefail

INPUT=$(cat 2>/dev/null)

python3 - "$INPUT" <<'PY' 2>/dev/null || exit 0
import json, os, re, sys, time

# The self-test points this at /dev/null: its fixtures are not real blocks, and counting them made
# every Monday's test run look like four real refusals.
OPERATOR = os.environ.get("CLAUDE_PLUGIN_OPTION_OPERATOR_NAME") or os.environ.get("MOSES_OPERATOR") or "the operator"
LOG = os.environ.get("GATE_LOG") or os.path.expanduser("~/.local/state/moses/handoff-blocks.log")

def allow():
    sys.exit(0)

# ── The second pass, 2026-10-14 ───────────────────────────────────────────────────────────────
# This used to allow ANY reply once stop_hook_active was set, to avoid a loop. That made the gate a
# one-shot: seen live on 2026-10-14, a blocked handoff came back with a diligence block reading
# "checked: nothing" and zero tool calls, and walked through. Now the revision is judged by the same
# rules, and the loop is bounded here instead: at most MAX_BLOCKS blocks per turn, then it lets the
# reply through and logs "cap:<code>", so a gate that keeps losing is visible rather than wedging.
STATE = os.environ.get("GATE_STATE") or os.path.expanduser("~/.local/state/moses/gate-turns.json")
MAX_BLOCKS = 2
TURN = None   # set once the transcript is read: which turn of which session this is

def _turn_count(bump):
    try:
        seen = json.load(open(STATE))
    except Exception:
        seen = {}
    n = seen.get(TURN, 0)
    if bump:
        seen[TURN] = n + 1
        seen = dict(list(seen.items())[-50:])   # only recent turns matter
        try:
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            with open(STATE, "w") as fh:
                json.dump(seen, fh)
        except Exception:
            pass
    return n

def block(code, reason):
    if TURN is not None:
        n = _turn_count(bump=False)
        if n >= MAX_BLOCKS:
            code = "cap:" + code
            reason = None
        else:
            _turn_count(bump=True)
            if n:
                reason = ("SECOND BLOCK THIS TURN — the revision still fails the same check. A "
                          "diligence block with nothing behind it is not a fix.\n\n" + reason)
    # Count it. Without this there is no way to tell a falling failure rate from a model that has
    # simply learned which sentence gets through.
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{code}\n")
    except Exception:
        pass
    if reason is None:
        allow()
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)

try:
    payload = json.loads(sys.argv[1]) if sys.argv[1].strip() else {}
except Exception:
    allow()

path = payload.get("transcript_path") or ""
if not path or not os.path.exists(path):
    allow()

# ── Read THIS TURN, not just the last message ───────────────────────────────
# The turn is everything after Brad's most recent real message. Tool results also arrive as
# "user" events, so they are excluded by requiring actual text content — mistaking one for a new
# turn would reset the tool-call count and defeat check 2.
events = []
try:
    with open(path, errors="ignore") as fh:
        for line in fh:
            try:
                events.append(json.loads(line))
            except Exception:
                continue
except Exception:
    allow()

def is_real_user_turn(ev):
    # Hook feedback (a Stop block's reason) is recorded as a "user" event marked isMeta. Counting it
    # as a new turn reset the turn at every block: the tool calls made before it were forgotten, and
    # the per-turn cap never reached 2. Seen live 2026-10-14: seven blocks in one turn.
    if ev.get("type") != "user" or ev.get("isMeta"):
        return False
    content = (ev.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(isinstance(c, dict) and c.get("type") == "text" for c in content)
    return False

start = 0
for i, ev in enumerate(events):
    if is_real_user_turn(ev):
        start = i + 1

# The ATTEMPT is what came after the last block's feedback. Its text is what gets judged: a revision
# that drops the ask must not be blocked for the ask it dropped. Tool calls count across the whole
# turn, because checks made before a block are still checks.
attempt = start
for i in range(start, len(events)):
    if events[i].get("type") == "user" and events[i].get("isMeta"):
        attempt = i + 1

TURN = f"{payload.get('session_id') or path}:{start}"

texts, tool_calls = [], 0
for i, ev in enumerate(events[start:], start):
    if ev.get("type") != "assistant":
        continue
    for c in (ev.get("message") or {}).get("content") or []:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "text" and c.get("text", "").strip() and i >= attempt:
            texts.append(c["text"])
        elif c.get("type") == "tool_use":
            tool_calls += 1

if not texts:
    allow()

final = texts[-1]
earlier = texts[:-1]

# ── Is this a handoff? ──────────────────────────────────────────────────────
# Deliberately narrow. A gate that fires on ordinary prose gets switched off within a day, and a
# switched-off gate protects nothing. These mean "Brad, go do something", not "here is what happened".
HANDOFF = [
    r"(?m)^\s*```[a-z]*\s*\n[^`]*\bsudo\b",
    # ANY absolute path, not one person's home. The old form was `/home/[a-z]` — which both tied
    # this to an operator whose scripts happen to live under their home AND missed the common
    # case of `sudo /usr/local/bin/...` or `sudo /opt/...`, so a real handoff walked through it.
    r"\bsudo\s+/[a-z]",
    r"\b(go|head) to\b.*\b(dashboard|console|settings)\b",
    r"\b(in|open)\b.*\b(Railway|Cloudflare|Anthropic|Resend|Stripe|Zero Trust|GitHub|Vercel|Hostinger)\b.*\b(dashboard|console|settings|panel)\b",
    # A UI PATH IS A HANDOFF even with no verb in front of it. On 2026-08-15 this gate let through
    # "Two minutes in Settings → Branches → add a rule for master" — no "go to", no "click", and
    # GitHub was not in the vendor list above, so nothing matched and Brad was sent to configure a
    # feature that (a) lived under a different menu, (b) needs a paid plan on a private repo, and
    # (c) was unnecessary, because the same guarantee was reachable from a file already in the repo.
    # The arrow is the tell: it only ever appears when describing somebody else's console.
    r"(?:^|\s)(Settings|Preferences|Dashboard|Console)\s*(?:→|->|›|»|>)\s*\w",
    # Naming a vendor's settings area at all, however politely it is phrased.
    r"\b(GitHub|Railway|Cloudflare|Anthropic|Resend|Stripe|Vercel|Hostinger)\b[^.\n]{0,40}\b(repo|repository|branch|account|project)?\s*settings\b",
    r"\bclick\b.*\b(save|create|add|connect|generate)\b",
    r"\bpaste\b.*\b(token|key|secret|value)\b",
    r"\brun (this|the following|it) (command|script)\b",
    r"\bcan you (check|confirm|verify|look)\b",
    r"\b(check|confirm) (whether|that|if)\b.*\b(dashboard|console|Railway|Cloudflare|Resend)\b",
]
# TALKING ABOUT AN ASK IS NOT MAKING ONE. Within an hour of the UI-path pattern shipping, this gate
# blocked a response whose entire subject was the gate's own false-negative — it quoted the offending
# sentence to explain what had been fixed, and the quote matched. That is the documented way these
# controls die: a guard that fires on correct work gets switched off, and a switched-off guard
# protects nothing. The marketing-copy guard solved the same problem by reading RENDERED text, so a
# comment discussing "coming soon" is not a finding; this is that idea for prose.
#
# Only SHORT spans are stripped — inline code, and quotations under ~120 chars. A real instruction to
# Brad is unquoted prose in the flow of the message; a retrospective quote is brief and marked. The
# limit is deliberate: wrapping a whole paragraph in quotes must not become a way around the gate.
QUOTED = [
    r"`[^`\n]{1,120}`",            # inline code
    r"\"[^\"\n]{1,120}\"",           # straight double quotes
    r"[\u201c][^\u201d\n]{1,120}[\u201d]",   # curly double quotes
]

def strip_quoted(s):
    for pat in QUOTED:
        s = re.sub(pat, " ", s)
    return s


def is_handoff(s):
    return any(re.search(p, strip_quoted(s), re.I) for p in HANDOFF)

# Accept the marker in any ordinary markdown form: bold, a heading, or bare. The first version
# took only the bold form and blocked a response whose diligence was present and correct but
# written as "## Before you touch anything". A gate that rejects a right answer on a technicality
# is one you learn to resent and eventually switch off — and a switched-off gate protects nothing.
# The PHRASE is still required; only the decoration around it is flexible.
MARKER = r"(?im)^\s*#{0,6}\s*\*{0,2}(Before you touch anything|Diligence)\b"

handoff_final = is_handoff(final)
handoff_earlier = any(is_handoff(t) for t in earlier)
has_marker = bool(re.search(MARKER, "\n".join(texts)))

# ── 3. The ask must end the turn ────────────────────────────────────────────
# An ask followed by six more tool calls scrolls off screen and was effectively never sent.
if handoff_earlier and not handoff_final:
    block("ask-not-final",
          "STOP — you asked " + OPERATOR + " to do something and then kept working, so the ask is "
          "buried above later output and they will not see it.\n\n"
          "Put the ask at the END of the turn and stop there. Do the tool work first, then ask, "
          "then end. One ask per message, nothing after it.")

if not handoff_final:
    allow()

# ── 1. A handoff must state the diligence ───────────────────────────────────
if not has_marker:
    block("no-diligence",
          "STOP — this response asks " + OPERATOR + " to run a command or open a console, and it does not state "
          "what you already verified yourself.\n\n"
          "Before sending, actually do the checks you can do from here: read the script instead of "
          "recalling how it behaves, grep the tooling for which files it reads, curl the endpoint, "
          "query the database, check the service state, re-read what earlier evidence in THIS "
          "conversation already proves or disproves.\n\n"
          "Then add a section headed exactly:\n\n"
          "**Before you touch anything**\n"
          "- checked: <what you ran or read, and what it showed>\n"
          "- could not check: <what needs them, and precisely why you cannot do it yourself>\n\n"
          "If everything is checkable from here, do it and drop the handoff entirely. Handing them a "
          "task you could have finished is the failure this gate exists to prevent.")

# ── 2. The claim needs work behind it ───────────────────────────────────────
# Shape alone decays: the sentence that passes gets learned. A diligence claim in a turn that made
# ZERO tool calls did not verify anything — that is mechanically detectable, so detect it.
if tool_calls == 0:
    block("claim-without-work",
          "STOP — this response claims diligence but the turn made no tool calls at all.\n\n"
          "Nothing was read, run, or queried, so there is nothing behind the claim. Either do the "
          "checks now — read the file, run the command, hit the endpoint — or remove the diligence "
          "block and say plainly that you have not verified anything yet.\n\n"
          "Writing the shape without doing the work is the exact decay this gate is meant to catch.")

allow()
PY
