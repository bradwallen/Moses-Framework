#!/usr/bin/env bash
# inject-commandments.sh — put the Commandments in context on EVERY prompt, in every project.
#
# UserPromptSubmit hook. The problem this solves (Brad, 2026-08-11): he told a session to always read
# the Commandments first and say so in its output. It said so. Asked whether it had actually read
# them or just claimed to, it admitted it had only claimed to — and a feature had to be redone.
#
# THE INSTRUCTION WAS THE BUG. "Read this file, then tell me you did" asks the model to self-report
# compliance, and a self-report is unfalsifiable: the claim reads identically whether or not the file
# was ever opened. So do not ask. DELIVER.
#
# ── SHORT FORM, 2026-10-01 ───────────────────────────────────────────────────────────────────────
# It used to deliver the whole section — about 2,600 tokens, on every single prompt. Over a fifty-turn
# session that is 130,000 tokens re-sending rules the model already had, and Brad's complaint was
# concrete: adding one project to the registry felt slow enough that he walked away from the keyboard.
#
# So PRESENCE is kept and DEPTH is made lazy. All ten rules are still delivered every turn, headline
# and all, DERIVED from the canonical file — nothing is summarized by hand, so there is no second copy
# to drift. What is no longer re-sent every turn is the reasoning and the scars behind each rule, and
# the model is told exactly when to go read them.
#
# The guarantee that survives: no session can be unaware a rule exists. The guarantee that does not:
# a session doing heavy work has to open the file to get the detail. That is the trade, stated.
#
# DEGRADES TO THE OLD BEHAVIOR. If the file's shape ever changes so that ten rules cannot be found,
# this sends the FULL section instead of a short one. A partial list presented as the whole list is
# the one outcome worse than either (commandment 8), so it is the one outcome this cannot produce.
#
# ── ONCE PER CONTEXT, 2026-10-08 ─────────────────────────────────────────────────────────────────
# Brad: "Why will the next message re-cache so many tokens?" Re-sending these ~480 tokens on every
# prompt added up (about 35k over one stretch of a long session). The full list now arrives ONCE per
# context: this same script is also a SessionStart hook, and SessionStart fires at startup, on resume
# AND after every compaction (verified 2026-10-08: 20 of the last 20 compactions in a session were
# followed by it). Every other prompt gets one line saying the rules are in context and where the
# canonical text is. Same source, so there is still no second copy to drift.
#
# Fails open by design: a broken hook must never block a prompt.

set -uo pipefail

# ── Where the rules come from ────────────────────────────────────────────────────────────────────
# First readable wins. The plugin option and MOSES_COMMANDMENTS name an operator's own copy; the
# memory corpus is where this instance has always kept it; the framework's docs/COMMANDMENTS.md is the
# default a fresh install gets. Two shapes are read: "## THE COMMANDMENTS" with "1. **Rule.**" lines
# (the corpus), and "## I — Rule" headings (the public doc). Both are derived, never transcribed.
HERE=$(dirname "$(readlink -f "$0")")
MEMDIR=${MOSES_MEMORY_DIR:-${CLAUDE_PLUGIN_OPTION_MEMORY_DIR:-$HOME/.claude/memory}}
SRC=""
for c in "${CLAUDE_PLUGIN_OPTION_COMMANDMENTS_FILE:-}" "${MOSES_COMMANDMENTS:-}" \
         "$MEMDIR/project_moses.md" "$HERE/../docs/COMMANDMENTS.md"; do
    [ -n "$c" ] && [ -r "$c" ] && { SRC=$(readlink -f "$c"); break; }
done
[ -n "$SRC" ] || exit 0

# Whose rules these are, and any house style that rides along with them. Both are this instance's
# config, not the framework's: a fresh install says "the operator" and states no house style.
OPERATOR=${CLAUDE_PLUGIN_OPTION_OPERATOR_NAME:-${MOSES_OPERATOR:-the operator}}
HOUSE=${CLAUDE_PLUGIN_OPTION_HOUSE_STYLE:-${MOSES_HOUSE_STYLE:-}}

# Which event called: SessionStart and SubagentStart get the list, UserPromptSubmit gets one line.
# Unknown input (an old caller, a manual run) gets the list, because missing the rules is worse than
# repeating them. SubagentStart is the reason this runs at all inside a sub-agent: a sub-agent sees
# none of the session-start context, so before 2026-10-14 every one of them worked without the rules.
EVENT=$(python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("hook_event_name",""))
except Exception: print("")' 2>/dev/null)
if [ "$EVENT" = "UserPromptSubmit" ]; then
    python3 -c '
import json, sys
src, who, house = sys.argv[1:4]
who = who[:1].upper() + who[1:]
print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": (
    who + "'"'"'s ten COMMANDMENTS apply to this turn. They were delivered in full at the start of this "
    "context (session start, resume or compaction); the canonical text is " + src + ". "
    + (house.split(".")[0] + ". " if house else "") + "Match the ceremony to the stakes.")},
    "suppressOutput": True}))' "$SRC" "$OPERATOR" "$HOUSE" 2>/dev/null || true
    exit 0
fi
case "$EVENT" in
    SessionStart|SubagentStart) EVENT_NAME=$EVENT ;;
    *) EVENT_NAME=UserPromptSubmit ;;
esac

# The section between "## THE COMMANDMENTS" and the next top-level heading. Anchored to headings
# rather than line numbers so editing above or below cannot silently shift the window.
body=$(awk '
    /^## THE COMMANDMENTS/ { grab = 1; next }
    grab && /^## /         { exit }
    grab                   { print }
' "$SRC")

if [ ${#body} -gt 500 ]; then
    # One line per rule, taken from the rule's own headline. Derived, never transcribed.
    short=$(printf '%s\n' "$body" | grep -oE '^[0-9]+\. \*\*[^*]+\*\*' | sed 's/\*\*//g')
else
    # The public shape: one "## <roman> — <rule>" heading per rule, the reasoning under each.
    body=$(cat "$SRC")
    short=$(python3 -c '
import re, sys
R = {"I": 1, "V": 5, "X": 10}
def num(r):
    n = 0
    for a, b in zip(r, r[1:] + " "):
        n += -R[a] if b in R and R[b] > R[a] else R[a]
    return n
for line in open(sys.argv[1]):
    m = re.match(r"^## ([IVX]+) — (.+?)\s*$", line)
    if m:
        print(f"{num(m.group(1))}. {m.group(2)}")' "$SRC" 2>/dev/null)
fi

[ ${#body} -gt 500 ] || exit 0
count=$(printf '%s\n' "$short" | grep -c '^[0-9]')

# Ten or it is not the list. Anything else and the full text goes, exactly as it did before.
if [ "$count" -ne 10 ]; then
    payload="$body"
    mode="full (the short form could not be derived — $count rules found, expected 10)"
else
    payload="$short
${HOUSE:+
House style: $HOUSE
}
MATCH THE CEREMONY TO THE STAKES. Local, reversible, nobody-sees-it work — a registry write, a
one-line fix, a note — gets done and reported in a line: no diligence block, no memory entry, no
verification pass, no investigating what was not asked about. A process that turns a thirty-second
task into five minutes gets routed around, and a routed-around process protects nothing.

READ THE FULL TEXT FIRST — $SRC — before work that publishes anything, touches security, money or
credentials, changes what a customer reads, or hands $OPERATOR an action. The reasoning and the
incidents behind each rule live there, and they decide the edge cases. These ten lines are the
index, not the library."
    mode="short"
fi

# The whether-to-delegate trigger, 2026-10-14. It used to live in the delegate skill, which a session
# only loads once it is already thinking about delegating, so the sessions that most needed the rule
# never saw it. Appended after either form, so the full-text fallback carries it too.
payload="$payload

If a task contains independent subtasks that can proceed without each other's results, spawn sub-agents for them rather than doing them in sequence. Money, credentials and production work stay in front of $OPERATOR, not delegated."

python3 -c '
import json, sys
body = sys.stdin.read().strip()
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": sys.argv[1],
        "additionalContext": (
            sys.argv[2][:1].upper() + sys.argv[2][1:] + "'"'"'s COMMANDMENTS (delivered automatically — these are standing "
            "instructions to you, and they apply to this turn whether or not the prompt mentions "
            "them):\n\n" + body +
            "\n\nThese were injected by a hook, so you have them: do not claim to have read a file "
            "you did not open, and do not report compliance you did not perform. If you depart from "
            "one of these, say so plainly and say why."
        ),
    },
    "suppressOutput": True,
}))' "$EVENT_NAME" "$OPERATOR" <<< "$payload" 2>/dev/null || true

# Not sent to the model — only for anyone running this by hand to see which branch it took.
[ -t 2 ] && echo "inject-commandments: $mode ($SRC)" >&2
exit 0
