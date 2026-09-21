#!/usr/bin/env bash
# test-zryachiy.sh — handing Zryachiy an exploration: refused without criteria, one at a time, the
# verdict taken from the runner's exit code, and the report reaching the conversation that asked.
#
#   ./test-zryachiy.sh        (no model, no sandbox, no Slack: ZRYACHIY_FAKE_EXPLORE stands in)
set -uo pipefail
cd "$(dirname "$0")"
pass=0; fail=0
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$*"; pass=$((pass+1)); }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; fail=$((fail+1)); }

JOBS=$(mktemp -d); PRODUCT=$(mktemp -d); trap 'rm -rf "$JOBS" "$PRODUCT"' EXIT
# A stand-in product checkout (all Zryachiy asks of one is an e2e/ directory) — the fake explorer never
# runs in it, and borrowing the operator's real product would make this pass only on their machine.
mkdir -p "$PRODUCT/e2e"
export ZRYACHIY_JOBS="$JOBS" ZRYACHIY_SLACK_CHANNEL="" ZRYACHIY_FAKE_EXPLORE=0 ZRYACHIY_VIATICA="$PRODUCT"
Z=bin/zryachiy

id_of() { grep -oE '[0-9]{8}-[0-9]{6}-[0-9]+' <<<"$1" | head -1; }
wait_done() { local d="$JOBS/$1" i; for i in $(seq 1 60); do [ -f "$d/ended" ] && return 0; sleep 0.2; done; return 1; }

echo "an exploration needs to say what must be true"
out=$($Z explore --feature "Group trips" 2>&1) && bad "started with no criteria" || { grep -q "needs --criteria" <<<"$out" && ok "refused without criteria, and says why" || bad "refused, but not for the criteria: $out"; }
out=$($Z explore --criteria "x must be true" 2>&1) && bad "started with no feature" || ok "refused without a feature"
out=$($Z explore --feature F --criteria "x must be true" --minutes 500 2>&1) && bad "accepted 500 minutes" || ok "refuses a run longer than an hour"
out=$($Z explore --feature F --criteria "   " 2>&1) && bad "accepted blank criteria" || ok "refuses criteria that are only whitespace"

echo
echo "the verdict is the runner's exit code, never re-judged"
for c in "0|clean" "1|findings" "2|could-not-finish"; do
    code=${c%%|*}; want=${c#*|}
    out=$(ZRYACHIY_FAKE_EXPLORE=$code ZRYACHIY_FAKE_REPORT="# Zryachiy explored: F
- verdict: test $want
\`\`\`text
CONFIRMED: the roster shows on the public link — steps: visitor
CONFIRMED: none — nothing else
CRITERION 5: NOT MET — the names are visible
\`\`\`" $Z explore --feature "Group trips" --criteria "the roster stays private" 2>&1)
    id=$(id_of "$out")
    if [ -z "$id" ] || ! wait_done "$id"; then bad "exit $code: the job never finished ($out)"; continue; fi
    got=$(cat "$JOBS/$id/status")
    [ "$got" = "$want" ] && ok "runner exit $code → $want" || bad "runner exit $code → $got, wanted $want"
done

last=$(ls -1d "$JOBS"/*/ | tail -1)
[ -f "$last/report.md" ] && ok "the runner's report is kept with the job" || bad "no report.md in the job"
grep -q "could not finish" "$last/slack-text.txt" && grep -q "Not a pass" "$last/slack-text.txt" \
    && ok "could-not-finish is said as NOT a pass" || bad "the could-not-finish message: $(head -1 "$last/slack-text.txt")"
grep -q "roster shows on the public link" "$last/slack-text.txt" && ok "a confirmed finding reaches the message" || bad "the finding is missing from the message"
grep -q "CRITERION 5: NOT MET" "$last/slack-text.txt" && ok "an unmet criterion reaches the message" || bad "the unmet criterion is missing"
grep -q "CONFIRMED: none" "$last/slack-text.txt" && bad "a null 'CONFIRMED: none' line was reported as a finding" || ok "a line that reports nothing is left out"
[ -f "$last/slack.json" ] && bad "posted to Slack under the suite" || ok "the suite posts nowhere"

echo
echo "one sandbox at a time, and a daily ceiling"
out=$(ZRYACHIY_FAKE_SLEEP=3 $Z explore --feature "Slow" --criteria "x must be true" 2>&1); slow=$(id_of "$out")
out2=$($Z explore --feature "Second" --criteria "x must be true" 2>&1) && bad "a second exploration started while one ran" \
    || { grep -q "already running" <<<"$out2" && ok "a second one is refused while one runs" || bad "refused for the wrong reason: $out2"; }
wait_done "$slow" || bad "the slow job never finished"
out=$(ZRYACHIY_MAX_PER_DAY=1 $Z explore --feature F --criteria "x must be true" 2>&1) && bad "the daily ceiling did not hold" \
    || { grep -q "already today" <<<"$out" && ok "the daily ceiling holds" || bad "refused for the wrong reason: $out"; }

echo
echo "status reads the record, and a dead worker is not 'running'"
# Captured first, never piped into `grep -q`: under pipefail, grep leaving early SIGPIPEs the writer
# and a MATCH reads as a failure (the trap that once called Brad's correct Slack config broken).
one=$($Z status "$slow"); list=$($Z status)
grep -q "status   clean" <<<"$one" && ok "status shows one exploration" || bad "status did not show the job: $one"
grep -q "Slow" <<<"$list" && ok "the list shows recent ones" || bad "the list is missing jobs: $list"
ghost="$JOBS/$(date +%Y%m%d)-000000-999999"; mkdir -p "$ghost"; echo running > "$ghost/status"; echo 999999 > "$ghost/pid"; echo G > "$ghost/feature.txt"
$Z status >/dev/null; [ "$(cat "$ghost/status")" = orphaned ] && ok "a job whose worker is gone is reconciled to orphaned" || bad "a dead job still claims to run"
$Z status "../../etc" >/dev/null 2>&1 && bad "status accepted a path as an id" || ok "status refuses anything that is not an id"

echo
echo "the conversation that asked is told (the real payload, lifted from the worker)"
fn=$(sed -n '/^zry_notify_payload() {/,/^}/p' bin/zryachiy-run)
[ -n "$fn" ] || { echo "  zry_notify_payload is GONE from bin/zryachiy-run — this test is watching nothing" >&2; exit 2; }
eval "$fn"
T=$(mktemp -d)
printf '%s' '{"channel":"C_HORSEMEN","thread_ts":"","user":"U_BRAD"}' > "$T/n.json"
p=$(zry_notify_payload "$T/n.json" "done" "C_DEV")
[ "$(jq -r .channel <<<"$p")" = C_HORSEMEN ] && jq -r .text <<<"$p" | head -1 | grep -q '^<@U_BRAD> done' \
    && ok "posts to the channel that asked, mentioning who asked" || bad "payload: $p"
jq -r .text <<<"$p" | grep -q '<#C_DEV>' && ok "and points to the full report" || bad "no pointer to the report"
printf '%s' '{"channel":"C_DEV","thread_ts":"","user":"U_BRAD"}' > "$T/d.json"
[ -z "$(zry_notify_payload "$T/d.json" "done" "C_DEV")" ] && ok "the report channel's top level is not posted twice" || bad "double-posted to the report channel"
[ -z "$(zry_notify_payload "$T/missing.json" "done" "C_DEV")" ] && ok "no record means no post" || bad "posted with no record"
rm -rf "$T"

printf '\n  %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
