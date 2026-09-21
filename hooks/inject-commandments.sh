#!/usr/bin/env bash
# inject-commandments.sh — put the Commandments in context on EVERY prompt, in every project.
#
# UserPromptSubmit hook. The problem this solves (Brad, 2026-08-11): he told a session to always read
# the Commandments first and say so in its output. It said so. Asked whether it had actually read
# them or just claimed to, it admitted it had only claimed to — and a feature had to be redone.
#
# THE INSTRUCTION WAS THE BUG. "Read this file, then tell me you did" asks the model to self-report
# compliance, and a self-report is unfalsifiable: the claim reads identically whether or not the file
# was ever opened. Same family as a permission list that greps clean while word-splitting shreds it,
# and a preflight that prints a wall-clock ceiling nobody ever exercised. Checking the declaration
# instead of the behavior.
#
# So do not ask. DELIVER. The text arrives in the context window before the model produces a token,
# so "did you read them" stops being a question anyone has to trust the answer to.
#
# HONEST LIMIT: this guarantees PRESENCE, not COMPLIANCE. It removes the failure where the rules were
# never in front of the model; it cannot remove the one where they were and got ignored anyway. That
# is a real distinction, and worth keeping in view — but the first failure is the one that has been
# happening, and it is the one a mechanism can actually close.
#
# Fails open by design: a broken hook must never block a prompt.

set -uo pipefail

SRC=${MOSES_COMMANDMENTS:-${MOSES_MEMORY_DIR:-$HOME/.claude/memory}/project_moses.md}

[ -r "$SRC" ] || exit 0

# Pull the section between "## THE COMMANDMENTS" and the next top-level heading. Anchored to the
# headings rather than to line numbers so editing the file above or below cannot silently shift the
# window and start injecting the wrong prose.
body=$(awk '
    /^## THE COMMANDMENTS/ { grab = 1; next }
    grab && /^## /         { exit }
    grab                   { print }
' "$SRC")

# If the markers ever move, inject nothing rather than a truncated or empty rule set. A partial list
# of rules presented as the whole list is worse than none — it looks complete.
[ ${#body} -gt 500 ] || exit 0

python3 -c '
import json, sys
body = sys.stdin.read().strip()
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": (
            "Brad'"'"'s COMMANDMENTS (delivered automatically — these are standing instructions to "
            "you, and they apply to this turn whether or not the prompt mentions them):\n\n"
            + body +
            "\n\nThese were injected by a hook, so you have them: do not claim to have read a file "
            "you did not open, and do not report compliance you did not perform. If you depart from "
            "one of these, say so plainly and say why."
        ),
    },
    "suppressOutput": True,
}))' <<< "$body" 2>/dev/null || true
