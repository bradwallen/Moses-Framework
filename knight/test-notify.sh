#!/usr/bin/env bash
# test-notify.sh — a job started from a conversation reports back to that conversation.
#
#   ./test-notify.sh
#
# WHY THIS EXISTS. Brad, 2026-09-11, in #the_4_horsemen: "Ping me here when Knight is done." Moses
# promised it twice; the runner only ever posted to #viatica-dev, so the promise had nothing behind
# it and nobody in that channel was told. Drives the REAL notify_payload lifted out of bin/knight-run,
# never a restatement. The listener's half (writing notify.json) is pinned in agent/listener_test.py.
set -uo pipefail
cd "$(dirname "$0")"
pass=0; fail=0
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$*"; pass=$((pass+1)); }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; fail=$((fail+1)); }

RUN=bin/knight-run
fn=$(sed -n '/^notify_payload() {/,/^}/p' "$RUN")
[ -n "$fn" ] || { echo "  notify_payload is GONE from $RUN — this test is watching nothing" >&2; exit 2; }
eval "$fn"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

H='⚠️ *moses → built but NOT pushed* — the gate failed'
echo "the conversation that asked is told"
printf '%s' '{"channel":"C_HORSEMEN","thread_ts":"","user":"U_BRAD"}' > "$TMP/n.json"
p=$(notify_payload "$TMP/n.json" "$H" "C_DEV" "JOB1")
[ "$(jq -r .channel <<<"$p" 2>/dev/null)" = C_HORSEMEN ] && ok "posts to the channel that asked" \
    || bad "posted to: $(jq -r .channel <<<"$p" 2>/dev/null) (payload: ${p:-none})"
jq -r .text <<<"$p" 2>/dev/null | head -1 | grep -q '^<@U_BRAD> ' && ok "mentions who asked" \
    || bad "does not mention who asked"
jq -r .text <<<"$p" 2>/dev/null | grep -qF "$H" \
    && ok "carries the runner's own headline, so the two can never disagree" \
    || bad "the headline is not the runner's"
jq -r .text <<<"$p" 2>/dev/null | grep -q 'JOB1' && ok "names the job" || bad "does not name the job"
jq -r .text <<<"$p" 2>/dev/null | grep -q '<#C_DEV>' && ok "points to the full report" \
    || bad "no pointer to the full report"
jq -e 'has("thread_ts") | not' <<<"$p" >/dev/null 2>&1 && ok "asked at top level → answered at top level" \
    || bad "invented a thread"

printf '%s' '{"channel":"C_HORSEMEN","thread_ts":"123.456","user":""}' > "$TMP/t.json"
p=$(notify_payload "$TMP/t.json" "$H" "C_DEV" "JOB2")
[ "$(jq -r .thread_ts <<<"$p" 2>/dev/null)" = 123.456 ] && ok "asked in a thread → answered in that thread" \
    || bad "lost the thread (payload: ${p:-none})"
jq -r .text <<<"$p" 2>/dev/null | grep -q '^<@' && bad "mentioned someone when nobody was recorded" \
    || ok "no one recorded → no mention"

printf '%s' '{"channel":"C_DEV","thread_ts":"9.9","user":"U_BRAD"}' > "$TMP/d.json"
p=$(notify_payload "$TMP/d.json" "$H" "C_DEV" "JOB3")
[ "$(jq -r .thread_ts <<<"$p" 2>/dev/null)" = 9.9 ] && ok "a thread in the report channel still hears it" \
    || bad "a thread in the report channel was skipped"
jq -r .text <<<"$p" 2>/dev/null | grep -q '<#C_DEV>' && bad "pointed to the channel it is already in" \
    || ok "and does not point to the channel it is in"

echo
echo "nobody to tell means no post"
for c in \
  "missing|" \
  "malformed|{not json" \
  "no channel|{\"channel\":\"\",\"thread_ts\":\"\",\"user\":\"U_BRAD\"}" \
  "the report channel's top level — the report is already there|{\"channel\":\"C_DEV\",\"thread_ts\":\"\",\"user\":\"U_BRAD\"}" \
; do
    label=${c%%|*}; body=${c#*|}
    rm -f "$TMP/x.json"; [ "$label" = missing ] || printf '%s' "$body" > "$TMP/x.json"
    p=$(notify_payload "$TMP/x.json" "$H" "C_DEV" "JOBX")
    [ -z "$p" ] && ok "$label → nothing posted" || bad "$label → posted: $p"
done

echo
echo "the wiring, not just the logic"
call=$(grep -n 'notify_payload "\$DIR/notify.json" "\$head"' "$RUN" | head -1 | cut -d: -f1)
main=$(grep -n 'slack_report=\$(python3' "$RUN" | head -1 | cut -d: -f1)
[ -n "$call" ] && ok "the runner calls it with its own headline" \
    || bad "the runner never calls notify_payload with \$head"
[ -n "$call" ] && [ -n "$main" ] && [ "$call" -gt "$main" ] \
    && ok "after the headline is decided, not before" || bad "called before \$head exists"
# Inside the tok/chan guard, so a suite that empties the channel posts nowhere at all.
guard=$(grep -n 'if \[ -n "\$tok" \] && \[ -n "\$chan" \]; then' "$RUN" | tail -1 | cut -d: -f1)
[ -n "$guard" ] && [ -n "$call" ] && [ "$call" -gt "$guard" ] \
    && ok "inside the post guard, so the suites stay silent" || bad "outside the post guard"
[ -f ../agent/knight_notify.py ] && grep -q 'notify.json' ../agent/knight_notify.py \
    && ok "the listener writes the file this reads" || bad "nothing writes notify.json"

printf '\n  %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
