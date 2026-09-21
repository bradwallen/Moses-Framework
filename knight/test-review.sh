#!/usr/bin/env bash
# test-review.sh — the review gate blocks what it should, and never blocks what it should not.
#
#   ./test-review.sh
#
# Drives the REAL review_verdict out of bin/knight-run rather than restating it, because a test that
# re-implements the decision proves the copy. The function is extracted for exactly this reason: the
# review only runs after a green gate, real commits and a clean rebase, so the end-to-end path is
# unreachable from the fake-agent seam and would otherwise never have been watched go red.
#
# The cases that matter are the ones that are NOT findings. A reviewer that crashed and a reviewer
# that answered in a shape nobody can parse are both "could not look" — and if either reads as a
# pass, the gate is decorative.
set -uo pipefail
. "$(dirname "$(readlink -f "$0")")/../agent/lib/moses-env.sh"   # whose home, where Moses lives, the operator's settings
cd "$(dirname "$0")"

pass=0; fail=0
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$*"; pass=$((pass+1)); }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; fail=$((fail+1)); }

# Pull the function into this shell. It moved out of bin/knight-run and into lib/ on 2026-09-17,
# when `knight-land` needed to make the SAME judgement before merging a branch as the runner makes
# before pushing one — two copies would have drifted, and the drift would only show up as a branch
# landing that the runner would have blocked. This check stays a guard-the-guard: if the function
# stops being where it is read from, this suite is watching nothing and says so rather than passing.
fn=$(sed -n '/^review_verdict() {/,/^}/p' lib/review-verdict.sh)
[ -n "$fn" ] || { echo "  review_verdict is GONE from lib/review-verdict.sh — this test is watching nothing" >&2; exit 2; }
# MATCH THE STATEMENT, NOT A MENTION. The first version of these two greps looked for the filename
# anywhere in the file — and passed happily when the `source` line was deleted, because the comment
# ABOVE it explaining why the file is shared still named it. A guard satisfied by prose is watching
# the documentation, not the code.
grep -qE '^[[:space:]]*(source|\.)[[:space:]].*review-verdict\.sh' bin/knight-run \
    || { echo "  bin/knight-run no longer SOURCES lib/review-verdict.sh — the push gate and this test have parted ways" >&2; exit 2; }
grep -qE '^[[:space:]]*(source|\.)[[:space:]].*review-verdict\.sh' bin/knight-land \
    || { echo "  bin/knight-land no longer SOURCES lib/review-verdict.sh — the merge gate is judging by its own rules" >&2; exit 2; }
eval "$fn"

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
verdict() {  # <label> <rc> <review text> <want_ok> [want_plausible_substring]
    local label=$1 rc=$2 text=$3 want=$4 wantp=${5:-}
    printf '%s\n' "$text" > "$TMP/r.txt"
    review_verdict "$rc" "$TMP/r.txt"
    if [ "$review_ok" != "$want" ]; then
        bad "$label — review_ok=$review_ok want=$want (why: ${review_why:-none})"; return
    fi
    if [ -n "$wantp" ] && [[ "$plausible" != *"$wantp"* ]]; then
        bad "$label — plausible did not carry '$wantp' (got: ${plausible:-none})"; return
    fi
    ok "$label"
}

echo "the gate lets sound work through"
verdict "CLEAN ships"                       0 "CLEAN" 1
verdict "PLAUSIBLE ships, and is carried"   0 "PLAUSIBLE: src/a.ts:4 — might race under retry" 1 "might race"
verdict "several PLAUSIBLE all ship"        0 "PLAUSIBLE: a.ts:1 — one
PLAUSIBLE: b.ts:2 — two" 1 "b.ts"

echo
echo "a reproduced defect blocks, exactly like a red test"
verdict "CONFIRMED blocks"                  0 "CONFIRMED: src/a.ts:9 — null slug throws on /view" 0
verdict "CONFIRMED wins over PLAUSIBLE"     0 "PLAUSIBLE: a.ts:1 — hmm
CONFIRMED: b.ts:2 — crashes on empty input" 0

echo
echo "a line that reports NOTHING is not a finding"
# 2026-09-11, verbatim: Brad asked Moses to match Atlas's bot caps, Knight built it green, and
# Zryachiy passed it — "CONFIRMED: none — no defects found in the diff." then CLEAN, and settled all
# six criteria. The parser saw a line starting CONFIRMED and blocked it. Nothing was pushed, and the
# Slack post read "blocked — review CONFIRMED: CONFIRMED: none". A gate that fires on correct work
# is the one that gets switched off.
verdict "the 2026-09-11 answer ships"       0 "CONFIRMED: none — no defects found in the diff.
CLEAN
SETTLED: 1 — pacing.py:54 sets MAX_OWN_REPLIES to 12" 1
verdict "an empty MISSED ships beside CLEAN" 0 "MISSED: none
CLEAN" 1
verdict "'no defects' spelled out ships"    0 "CONFIRMED: No defects found.
MISSED: nothing.
CLEAN" 1
# And the other direction, which matters more: dropping a REAL finding because it begins with a
# word that also means nothing would turn this fix into a hole.
verdict "a null line alone is NOT a pass"   0 "CONFIRMED: none" 0
verdict "'none of the callers…' is a finding" 0 "CONFIRMED: none of the callers handle an empty slug
CLEAN" 0
verdict "'nothing guards a.ts:12' is a finding" 0 "CONFIRMED: nothing guards src/a.ts:12 against null
CLEAN" 0
verdict "a null that names a file:line is a finding" 0 "CONFIRMED: no defects found, but src/a.ts:3 throws on empty input
CLEAN" 0
verdict "a real MISSED beside a null CONFIRMED blocks" 0 "CONFIRMED: none
MISSED: the daily cap is still 3" 0
printf '%s\n' "CONFIRMED: src/a.ts:9 — null slug throws on /view" > "$TMP/r.txt"
review_verdict 0 "$TMP/r.txt"
case "$review_why" in
  *"CONFIRMED: CONFIRMED"*) bad "the reason repeats the verdict word: $review_why" ;;
  *"src/a.ts:9"*)           ok "a block names the finding once, not 'CONFIRMED: CONFIRMED'" ;;
  *)                        bad "a block does not name the finding: ${review_why:-none}" ;;
esac

echo
echo "could not look is NEVER a pass"
verdict "a crashed reviewer blocks"         1 "CLEAN" 0
verdict "a killed reviewer blocks"        124 "" 0
verdict "an unparseable answer blocks"      0 "Looks fine to me overall, nice work!" 0
verdict "an empty answer blocks"            0 "" 0
verdict "a verdict word mid-line does not count" 0 "I would say this is CONFIRMED fine" 0

echo
echo "the wiring, not just the logic"
grep -q 'review_verdict "\$rrc"' bin/knight-run \
  && ok "the runner calls it" || bad "the runner no longer calls review_verdict"
# `... | read -r var` runs the read in a SUBSHELL, so the variable is gone by the next line — the
# same trap that once reported Brad's correct Slack config as broken. Command substitution instead.
rline=$(grep -n 'review_verdict "\$rrc"' bin/knight-run | head -1 | cut -d: -f1)
pline=$(grep -n 'remote add push-target' bin/knight-run | head -1 | cut -d: -f1)
if [ -n "$rline" ] && [ -n "$pline" ] && [ "$rline" -lt "$pline" ]; then
    ok "and calls it BEFORE the push, not after"
else
    bad "the review does not run before the push — it is decorative there"
fi
# M20: the reviewer's restrictions live in the one runner's zryachiy-reviewer profile, built from
# guards.env. So this asks the runner for the command the reviewer actually gets, rather than looking
# for a variable name in knight-run.
grep -q -- '--app zryachiy-reviewer' bin/knight-run && ok "the reviewer runs through the runner's reviewer profile" \
  || bad "the reviewer is not restricted — a reviewer that can edit is an author"
rargv=$("$MOSES_ROOT/agent/claude-run" --app zryachiy-reviewer --prompt x --add-dir /tmp --print-argv 2>/dev/null)
for t in Edit Write; do
  python3 -c '
import json, sys
a, t = json.loads(sys.argv[1] or "[]"), sys.argv[2]
def after(f):
    if f not in a: return []
    i, out = a.index(f) + 1, []
    while i < len(a) and not a[i].startswith("--"): out.append(a[i]); i += 1
    return out
offered = (after("--tools") or [""])[0].split(",")
sys.exit(0 if a and t not in offered and t not in after("--allowedTools") and t in after("--disallowedTools") else 1)' "$rargv" "$t" \
    && ok "the reviewer's command neither offers nor approves $t, and refuses it" \
    || bad "the reviewer's command could $t"
done
for t in Edit Write Task Agent; do
  grep -qE "KNIGHT_REVIEW_DENY=\(.*\"$t\"" guards.env \
    && ok "reviewer cannot $t" || bad "reviewer is allowed to $t"
done


# ── MISSED: correct code that is not what was asked for ────────────────────────────────────────
# The gap Brad named on 2026-09-04: every check in this pipeline asked "is this code correct" and
# none asked "is this what was asked for", so a flawless diff solving the wrong problem shipped
# CLEAN. MISSED blocks like CONFIRMED but must READ differently — one says the code is broken, the
# other says the code may be perfect and is not what was requested.
echo
echo "acceptance criteria gate the push, not just defects"
verdict "MISSED blocks"                     0 "MISSED: the Labs host is still there — renamed, not removed" 0
verdict "MISSED among PLAUSIBLE still blocks" 0 "PLAUSIBLE: a.ts:2 — maybe
MISSED: no help content was added for the new screen" 0
verdict "CONFIRMED and MISSED together block" 0 "CONFIRMED: a.ts:9 — null deref
MISSED: the second criterion is untouched" 0

printf '%s\n' "MISSED: renamed instead of removed" > "$TMP/r.txt"
review_verdict 0 "$TMP/r.txt"
case "$review_why" in
  *"MISSED the request"*) ok "and it says the REQUEST was missed, not that the code is broken" ;;
  *) bad "MISSED reported as: ${review_why:-none}" ;;
esac

echo
echo "the criteria actually reach the reviewer, and a job cannot start without them"
grep -q 'ACCEPTANCE CRITERIA' bin/knight \
  && ok "the review brief carries the acceptance criteria" \
  || bad "the reviewer is never told what should have been built"
grep -q 'every job needs --acceptance' bin/knight \
  && ok "a job with no criteria is refused at task time" \
  || bad "a job can still start with no acceptance criteria"
grep -qE '^\s*MISSED: <criterion>' bin/knight \
  && ok "the reviewer is given MISSED as an output shape" \
  || bad "the reviewer has no way to say it missed the mark"
grep -q 'MISSED BLOCKS THE PUSH' bin/knight \
  && ok "and is told MISSED blocks" \
  || bad "the reviewer is not told MISSED blocks"

printf '\n  %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
