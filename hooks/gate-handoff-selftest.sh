#!/usr/bin/env bash
# gate-handoff-selftest.sh — prove the handoff gate still discriminates.
#
#   gate-handoff-selftest.sh            run the fixtures, print results, exit 1 on any failure
#   gate-handoff-selftest.sh --quiet    only speak up on failure (what the weekly timer uses)
#
# WHY (Atlas, via Brad, 2026-08-13): *"A gate that's never seen going red after week one is
# decoration."* A shape-checking gate decays as the model learns the sentence that passes, and
# nothing about a silent hook tells you which is happening.
#
# This is the same lesson as Knight's wall-clock ceiling, which went a full day untested while
# `doctor` printed "60 min" with total confidence. A backstop nobody has watched fail is a claim,
# not a mechanism — so these fixtures run weekly, unattended, and shout when they stop discriminating.
#
# The fixtures are REAL cases from the session that produced the gate, including the Railway ask that
# started it. Each asserts a specific outcome, so a pattern that quietly stops matching is caught by
# the fixture that depended on it rather than by Brad noticing months later.

set -uo pipefail

GATE="${GATE:-$HOME/.claude/hooks/gate-handoff.sh}"
export GATE_LOG=/dev/null     # fixture blocks are not real blocks; keep them out of the rate log
QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0; failures=""

say() { [ "$QUIET" -eq 1 ] || printf '%s\n' "$*"; }

# Build a transcript: a real user turn, then assistant text blocks, then N tool calls.
# fixture <file> <tools:int> <text...>  — each extra arg is one assistant text block, in order.
fixture() {
  local out="$1" tools="$2"; shift 2
  python3 - "$out" "$tools" "$@" <<'PY'
import json, sys
out, tools, texts = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
lines = [json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": "do the thing"}]}})]
content = [{"type": "text", "text": t} for t in texts]
# Tool calls are recorded BEFORE the final text, matching how a real turn is shaped: work, then report.
for i in range(tools):
    content.insert(max(0, len(content) - 1), {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {}})
lines.append(json.dumps({"type": "assistant", "message": {"content": content}}))
open(out, "w").write("\n".join(lines) + "\n")
PY
}

# check <label> <expect: block|allow> <file> [expected-code]
check() {
  local label="$1" expect="$2" file="$3" want_code="${4:-}"
  local out got code
  out=$(printf '{"transcript_path":"%s"}' "$file" | "$GATE" 2>/dev/null)
  if [ -n "$out" ]; then got=block; else got=allow; fi
  code=$(printf '%s' "$out" | python3 -c "
import json,sys
try: print(json.load(sys.stdin).get('reason','')[:40])
except Exception: print('')" 2>/dev/null)
  if [ "$got" = "$expect" ]; then
    say "  PASS  $label ($got)"
    pass=$((pass+1))
  else
    say "  FAIL  $label — expected $expect, got $got"
    failures="$failures
  - $label: expected $expect, got $got"
    fail=$((fail+1))
  fi
}

say "Handoff gate — fixtures"
say

# ── The original six: these guard against BOTH directions of drift ──────────
fixture "$TMP/1.jsonl" 3 'The check that settles it: in Railway, confirm the variable is spelled ANTHROPIC_ADMIN_KEY on the Customs service.'
check "the Railway ask that started this" block "$TMP/1.jsonl"

fixture "$TMP/2.jsonl" 3 '**Before you touch anything**
- checked: yesterday'"'"'s report read $17.67, so the key is set and working
- could not check: Railway env values are not readable from Reserve

Then in the Railway dashboard, confirm the spelling.'
check "same ask, diligence stated" allow "$TMP/2.jsonl"

fixture "$TMP/3.jsonl" 4 'One command finishes it:
```
sudo /opt/scripts/migrate-dennis.sh
```'
check "sudo handoff, no diligence" block "$TMP/3.jsonl"

fixture "$TMP/4.jsonl" 2 'Shipped 218efdc — 387 tests green. The retry only fires on 429 and 5xx. Big Pipe reads $17.65 against a console showing $17.67.'
check "plain report (false-positive guard)" allow "$TMP/4.jsonl"

printf '{"transcript_path":"%s","stop_hook_active":true}' "$TMP/1.jsonl" > /dev/null
out=$(printf '{"transcript_path":"%s","stop_hook_active":true}' "$TMP/1.jsonl" | "$GATE" 2>/dev/null)
if [ -z "$out" ]; then say "  PASS  loop guard (allow)"; pass=$((pass+1)); else say "  FAIL  loop guard"; fail=$((fail+1)); failures="$failures
  - loop guard did not allow"; fi

out=$(echo 'not json' | "$GATE" 2>/dev/null)
if [ -z "$out" ]; then say "  PASS  malformed input fails open"; pass=$((pass+1)); else say "  FAIL  malformed input"; fail=$((fail+1)); failures="$failures
  - malformed input did not fail open"; fi

# ── The three added 2026-08-13 ──────────────────────────────────────────────
fixture "$TMP/7.jsonl" 0 '**Before you touch anything**
- checked: nothing, really
- could not check: everything

Now go paste the token in the dashboard.'
check "diligence claimed, ZERO tool calls" block "$TMP/7.jsonl"

fixture "$TMP/8.jsonl" 3 'Run this command: sudo /opt/scripts/thing.sh' 'Meanwhile I kept working and here are the results of that work.'
check "ask buried above later output" block "$TMP/8.jsonl"

fixture "$TMP/9.jsonl" 3 'First I looked at the config.' '**Before you touch anything**
- checked: read the script, it reads both env files
- could not check: the secret is root-only

```
sudo /opt/scripts/thing.sh
```'
check "ask last, diligence, work done" allow "$TMP/9.jsonl"

# ── Marker forms: added after a REAL false block, 2026-08-13 ────────────────
# The gate blocked a response whose diligence was present and correct, because it was written as a
# "## " heading rather than bold. Both forms are pinned so a future edit cannot quietly narrow it
# back — a gate that rejects right answers is one you learn to switch off.
fixture "$TMP/10.jsonl" 2 '## Before you touch anything
- checked: read the script, it reads both env files
- could not check: the token is root-only

```
sudo /opt/scripts/install-layer4.sh
```'
check "diligence as a ## heading" allow "$TMP/10.jsonl"

fixture "$TMP/11.jsonl" 2 '**Before you touch anything**
- checked: ran the fixtures, 9/9
- could not check: the Tailscale console

```
sudo /opt/scripts/install-layer4.sh
```'
check "diligence as bold text" allow "$TMP/11.jsonl"

say
# ── The GitHub hole, 2026-08-15 ────────────────────────────────────────────
# The gate ran for two days and would never have caught a GitHub handoff: GitHub was missing from
# the vendor list, and "Two minutes in Settings → Branches" contains none of the verbs the other
# patterns look for. Brad was sent to a menu that did not exist, for a feature behind a paid plan,
# to solve a problem already solvable from a file in the repo. All three fixtures below are verbatim.
fixture "$TMP/12.jsonl" 4 'Two minutes in Settings → Branches → add a rule for `master` requiring the `verify` check.'
check "a UI path with no verb (the sentence that got through)" block "$TMP/12.jsonl"

fixture "$TMP/13.jsonl" 4 'Settings → Rules → Rulesets → New ruleset → New branch ruleset.'
check "a vendor menu path" block "$TMP/13.jsonl"

fixture "$TMP/14.jsonl" 4 "You'll need to flip that in your GitHub repository settings."
check "GitHub named as a settings destination" block "$TMP/14.jsonl"

# The inverse, which matters as much: these must NOT fire. A gate that trips on ordinary reporting
# gets switched off, and the word "settings" appears constantly in honest work summaries.
fixture "$TMP/15.jsonl" 4 'I edited settings.json and the guards now run on every push.'
check "editing a settings FILE is not a handoff" allow "$TMP/15.jsonl"

fixture "$TMP/16.jsonl" 4 'GitHub Actions now runs the suite on every push; nothing for you to do.'
check "naming GitHub while reporting finished work" allow "$TMP/16.jsonl"

# ── The gate blocking its own author, 2026-08-15 (again) ───────────────────
# One hour after the UI-path pattern shipped, it blocked a response that was REPORTING the fix — the
# retrospective quoted the offending sentence and the quote matched. Second time this gate has
# stopped a correct answer within an hour of a change, so both directions are now pinned together:
# a quoted or code-spanned path is discussion, an unquoted one is an instruction.
fixture "$TMP/17.jsonl" 4 'Two holes: GitHub was not in the vendor list, and "Two minutes in Settings → Branches" matched none of the verbs. Fixed and pinned.'
check "quoting a bad ask while reporting the fix" allow "$TMP/17.jsonl"

fixture "$TMP/18.jsonl" 4 'The gate let through `Settings → Rules → Rulesets` because no verb appeared.'
check "a UI path inside inline code is discussion" allow "$TMP/18.jsonl"

fixture "$TMP/19.jsonl" 4 'Settings → Rules → Rulesets → New ruleset → New branch ruleset.'
check "the same path UNQUOTED is still an instruction" block "$TMP/19.jsonl"

# A BARE sudo of a system path, with no other cue in the message. Found 2026-09-21: the live gate only
# matched `sudo /home/<author>/`, so this walked through it, while every fixture above passed because
# some OTHER rule caught their wording first. This one isolates the sudo rule.
fixture "$TMP/20.jsonl" 2 'Everything is ready.

sudo /usr/local/bin/install-thing.sh'
check "a bare sudo of a system path is a handoff" block "$TMP/20.jsonl"

say "  $pass passed, $fail failed"
if [ "$fail" -gt 0 ]; then
  # The timer is quiet on success and loud on failure — a weekly "all fine" trains you to ignore it.
  printf 'HANDOFF GATE IS NOT DISCRIMINATING — %d fixture(s) failed:%s\n' "$fail" "$failures" >&2
  exit 1
fi
exit 0
