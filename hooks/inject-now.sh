#!/usr/bin/env bash
# inject-now.sh — stamp the REAL current time into context on every prompt.
#
# UserPromptSubmit hook. Brad keeps the Moses and Viatica sessions open for days at a stretch,
# locking the machine and coming back later, so the date a session STARTED goes stale while the
# conversation continues. On 2026-08-10 that produced work from Saturday being described as "today"
# and "this morning" — the events were the most recent thing in the conversation, and conversational
# recency got mistaken for time.
#
# A memory saying "check the clock" is a policy; policy is the weakest layer. This is a mechanism:
# the clock arrives whether or not anyone remembers to look.
#
# Deliberately not fatal — if this ever fails, the prompt still goes through.

set -uo pipefail

now=$(date '+%A, %B %-d, %Y at %-I:%M %p %Z')

python3 -c '
import json, sys
now = sys.argv[1]
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": (
            "Current date and time: " + now + ". "
            "This session may have been open for days with long gaps between turns, so earlier "
            "events in this conversation are NOT necessarily recent. Before writing today, "
            "yesterday, this morning, or any tense that implies recency, check the actual date of "
            "the thing being described (file mtimes, git log, job records) against the time above."
        ),
    },
    "suppressOutput": True,
}))' "$now" 2>/dev/null || true
