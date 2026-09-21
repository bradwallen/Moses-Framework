#!/usr/bin/env bash
# inject-relevant-notes.sh — list the memory notes and doc sections that match the prompt.
#
# UserPromptSubmit hook. The Commandments hook delivers the rules on every prompt; this delivers the
# specific notes, which a long-lived session has otherwise compacted away. The logic, and why it is
# keyword matching with a high bar, lives in relevant-notes.py beside this file.
#
# Fails open by design, like the other prompt hooks: a broken hook must never block a prompt.

set -uo pipefail
python3 "$(dirname "$(readlink -f "$0")")/relevant-notes.py" 2>/dev/null || true
exit 0
