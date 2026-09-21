#!/usr/bin/env bash
# test-report-label.sh — the report's first section is named for what actually landed.
#
#   ./test-report-label.sh
#
# WHY THIS EXISTS. Brad asked Knight to drop the retired Pi from the Hardware page. Knight did the
# work correctly on a branch and posted a report whose banner read "NOT merged, nothing is live" —
# directly above a line reading "*What shipped* — The Hardware page now lists Reserve alone". Five
# days later the Pi was still on the page. Nothing in that message was false; the section LABEL was
# hardcoded and asserted an outcome the same message denied, and that is the line a human keeps.
#
# So the label is now decided by the runner from what landed, never by the agent writing the prose.
# Both halves are exercised here AGAINST THE REAL SOURCE — the decision block and the collapser are
# lifted out of bin/knight-run, not restated — because a second copy of this logic in a test is how
# the two drift apart and the test starts guarding a fiction.
set -uo pipefail
cd "$(dirname "$0")"
pass=0; fail=0
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$*"; pass=$((pass+1)); }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; fail=$((fail+1)); }

RUN=bin/knight-run
[ -f "$RUN" ] || { echo "  bin/knight-run is missing — this test is watching nothing" >&2; exit 2; }

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

# ── half one: the decision, lifted verbatim ─────────────────────────────────────────────────────
# Anchored on the ASSIGNMENT, not its value. The first version keyed on the literal default string
# — which is the exact thing under test, so hardcoding the label back to "What shipped" made this
# script abort with "the markers moved" instead of failing. A test that cannot fail on the original
# bug is decoration; caught by running that sabotage.
CHAIN=$(awk '/^        ship_label=/,/^        fi$/' "$RUN")
[ "$(printf '%s\n' "$CHAIN" | wc -l)" -gt 20 ] \
    || { echo "  could not lift the label decision out of $RUN — the markers moved" >&2; exit 2; }

# <pushed> <push_mode> <deploy_state> <rolled_back> <gate_ok>
label_for() {
    pushed=$1 KT_PUSH_MODE=$2 deploy_state=$3 rolled_back=$4 gate_ok=$5 \
    TARGET=t KT_BRANCH=master push_note=n BRANCH=b gate_why=w deploy_why=w DIR=$TMP \
    bash -c "$CHAIN"'; printf "%s" "$ship_label"'
}

echo "the label follows what actually landed"
for c in \
  "1 branch '' 0 1|What this would do|a branch pushed for review, nothing merged" \
  "0 branch '' 0 1|What this would do|built but not pushed" \
  "0 direct '' 0 0|What this would do|gate red, nothing pushed" \
  "1 direct '' 0 1|What shipped|pushed straight to the deploying branch" \
  "1 direct live 0 1|What shipped|deployed and answering" \
  "1 direct broken 0 1|What shipped|deployed, broken, still live" \
  "1 direct broken 1 1|What was tried|deployed and rolled back" \
  "1 direct unverified 0 1|What shipped|pushed, deploy unverified" \
; do
    IFS='|' read -r args want why <<< "$c"
    # shellcheck disable=SC2086
    got=$(eval "label_for $args")
    [ "$got" = "$want" ] && ok "$why → \"$want\"" || bad "$why → got \"$got\", wanted \"$want\""
done

# ── half two: the collapser, also lifted verbatim ───────────────────────────────────────────────
awk "/<<'CONV'/{f=1;next} /^CONV\$/{f=0} f" "$RUN" > "$TMP/collapse.py"
[ -s "$TMP/collapse.py" ] \
    || { echo "  could not lift the collapser out of $RUN — the heredoc moved" >&2; exit 2; }

cat > "$TMP/report.txt" <<'REP'
*What shipped* — The Hardware page now lists Reserve alone.
*Decisions* — Kept the hosts list so a second host needs no dashboard change.
*Stoppers* — None.
*Notes* — Merging changes nothing live until the file is deployed.
REP

echo
echo "the section is renamed on the way out, whatever the agent wrote"
out=$(python3 "$TMP/collapse.py" "$TMP/report.txt" "What this would do")
case "$out" in
  '*What this would do* —'*) ok "a not-merged run is not allowed to say \"What shipped\"" ;;
  *) bad "first line was: $(printf '%s' "$out" | head -1)" ;;
esac
printf '%s' "$out" | grep -q "What shipped" \
    && bad "the words \"What shipped\" survived into a report where nothing shipped" \
    || ok "the misleading label appears nowhere in the output"

out=$(python3 "$TMP/collapse.py" "$TMP/report.txt" "What shipped")
case "$out" in
  '*What shipped* —'*) ok "a run that DID land still says \"What shipped\"" ;;
  *) bad "first line was: $(printf '%s' "$out" | head -1)" ;;
esac

# The other three sections are the agent's and must come through untouched.
for want in Decisions Stoppers Notes; do
    printf '%s' "$out" | grep -q "^\*$want\* —" \
        && ok "$want survived the rename" || bad "$want was lost or renamed"
done

# A report already written under a newer label must still parse into four lines, not fall through
# to the "unrecognized shape" branch that posts the raw text.
sed 's/\*What shipped\*/*What this would do*/' "$TMP/report.txt" > "$TMP/report2.txt"
out=$(python3 "$TMP/collapse.py" "$TMP/report2.txt" "What shipped")
[ "$(printf '%s\n' "$out" | wc -l)" -eq 4 ] \
    && ok "a report written under an alternate label still collapses to four lines" \
    || bad "alternate label fell through to the raw-text branch"

echo
printf 'report label: %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
