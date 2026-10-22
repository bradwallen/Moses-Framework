#!/usr/bin/env bash
# test-provision.sh — `knight provision` and Knight's jobs never share the clone at the same time.
#
#   bash knight/test-provision.sh
#
# Since 2026-10-18 knight-next runs provision unattended, to prove a finished project's checks pass on
# its main branch. Provision resets and cleans the target's clone, so it must not run under a job, and
# a job must not start under it. Drives the REAL bin/knight against a scratch target (zzz-prov-*) and a
# scratch jobs directory. Then each guard is removed from a copy of bin/knight and must go wrong.
#
# Written after the first live provision of this code printed "glog: unbound variable" at exit and
# left its marker behind: no suite ran provision itself.
set -uo pipefail
. "$(dirname "$(readlink -f "$0")")/../agent/lib/moses-env.sh"
HERE=$MOSES_ROOT/knight
pass=0; fail=0
ok()  { printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL  %s\n     %s\n' "$1" "${2:-}"; fail=$((fail+1)); }
TMP=$(mktemp -d /tmp/knight-provision-test.XXXXXX)
T="zzz-prov-$$"
export KNIGHT_JOBS="$TMP/jobs" KNIGHT_SLACK_CHANNEL=""
mkdir -p "$KNIGHT_JOBS"
SLEEPERS=()
cleanup() {
    local p; for p in "${SLEEPERS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null; done
    rm -f "$HERE/targets/$T.env"; rm -rf "$HERE/repo/$T" "$TMP"
    rm -f "$HERE/.provisioning.test-$$"
}
trap cleanup EXIT

SRC="$TMP/src"
mkdir -p "$SRC"; git -C "$SRC" init -q -b main; git -C "$SRC" config user.email t@t.test; git -C "$SRC" config user.name t
printf '#!/bin/sh\nexit 0\n' > "$SRC/gate.sh"; git -C "$SRC" add -A; git -C "$SRC" commit -qm first
printf 'KT_DESC="provision test"\nKT_SOURCE=%s\nKT_BRANCH=main\nKT_PUSH_MODE=branch\nKT_PUSH_REMOTE=%s\nKT_GATE=(%s)\n' \
    "$SRC" "$SRC" "'true'" > "$HERE/targets/$T.env"

# provisioned <knight-bin> — exit status, what it printed, and whether the marker was left behind.
prov() { "$1" provision "$T" > "$TMP/out" 2>&1; echo $?; }

echo "knight provision"
rc=$(prov "$HERE/bin/knight")
[ "$rc" = 0 ] && grep -q green "$TMP/out" && ok "provision runs the target's gate on a fresh clone" || bad "provision runs the target's gate on a fresh clone" "$(cat "$TMP/out")"
! grep -qiE 'unbound|line [0-9]+:' "$TMP/out" && ok "and exits cleanly (no shell errors at exit)" || bad "and exits cleanly (no shell errors at exit)" "$(grep -iE 'unbound|line' "$TMP/out")"
[ ! -e "$HERE/.provisioning" ] && ok "and leaves no marker behind" || bad "and leaves no marker behind" "$(cat "$HERE/.provisioning" 2>/dev/null)"

# A job holding the clone: provision waits for it.
sleep 300 & SLEEPERS+=($!)
J="$KNIGHT_JOBS/20991231-000000-9"; mkdir -p "$J"
printf 'running\n' > "$J/status"; printf '%s\n' "$!" > "$J/pid"; printf '%s\n' "$T" > "$J/target"
printf '%s\n' "$(date -Is)" > "$J/started"
rc=$(prov "$HERE/bin/knight")
[ "$rc" != 0 ] && grep -q 'already running' "$TMP/out" && ok "with a job running, provision refuses — it would reset the clone under it" \
    || bad "with a job running, provision refuses — it would reset the clone under it" "rc=$rc $(cat "$TMP/out")"
rm -rf "$J"

# A provision in progress: a start waits for it, in words knight-next's busy loop already waits on.
sleep 300 & SLEEPERS+=($!)
printf '%s %s\n' "$!" "$T" > "$HERE/.provisioning"
"$HERE/bin/knight" start --target "$T" --acceptance "The page works." "Build a thing" > "$TMP/start" 2>&1; rc=$?
rm -f "$HERE/.provisioning"
[ "$rc" != 0 ] && grep -q 'already running' "$TMP/start" && grep -q 'provision' "$TMP/start" \
    && ok "while a provision runs, a job does not start — and says why" || bad "while a provision runs, a job does not start — and says why" "rc=$rc $(cat "$TMP/start")"
ls "$KNIGHT_JOBS" | grep -q . && bad "and no job record was made" "$(ls "$KNIGHT_JOBS")" || ok "and no job record was made"

# A marker whose process is gone does not block anything.
printf '999999 %s\n' "$T" > "$HERE/.provisioning"
rc=$(prov "$HERE/bin/knight")
[ "$rc" = 0 ] && ok "a marker left by a provision that died blocks nothing" || bad "a marker left by a provision that died blocks nothing" "$(cat "$TMP/out")"
rm -f "$HERE/.provisioning"

# Watch each guard fail on a copy of bin/knight.
MUT="$HERE/bin/.knight-mut-$$"
watch_fail() {  # watch_fail <label> <sed expression> <check: a command that must FAIL on the mutant>
    cp "$HERE/bin/knight" "$MUT"; sed -i "$2" "$MUT"
    if cmp -s "$HERE/bin/knight" "$MUT"; then bad "$1" "the sabotage no longer applies"
    elif eval "$3"; then bad "watched fail: $1" "the suite did not see it"
    else ok "watched fail: $1"; fi
    rm -f "$MUT"
}
watch_fail "with the trap quoted late, provision errors at exit" \
    "s/trap \"rm -f '\$PROVISIONING' '\$glog'\" EXIT/trap 'rm -f \"\$PROVISIONING\" \"\$glog\"' EXIT/" \
    '[ "$(prov "$MUT")" = 0 ] && ! grep -qiE "unbound|line [0-9]+:" "$TMP/out"'
watch_fail "with the running-job check removed, provision resets a working clone" \
    's/\[ "\$r" -gt 0 \] && die "\$r job(s) already running — provision/false \&\& die "/' \
    'sleep 300 & SLEEPERS+=($!); mkdir -p "$J"; printf "running\n" > "$J/status"; echo $! > "$J/pid"; echo "$T" > "$J/target"; date -Is > "$J/started"; r=$(prov "$MUT"); rm -rf "$J"; [ "$r" != 0 ]'

echo
echo "$pass ok, $fail failed"
[ "$fail" -eq 0 ]
