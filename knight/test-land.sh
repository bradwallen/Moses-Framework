#!/usr/bin/env bash
# test-land.sh — knight-land, driven against a REAL git repository and REAL job records.
#
#   bash knight/test-land.sh
#
# WHY IT BUILDS A REPOSITORY INSTEAD OF STUBBING GIT. The thing being tested is whether a merge is
# refused in the states where refusing matters — a dirty tree, a branch already in, a review that was
# not clean. Stub git and every one of those becomes a test of the stub. So each case makes a
# throwaway repo in a temp directory, puts it in the state, and runs the real script against it.
#
# The one thing not exercised end to end is the restart, because there is no unit to restart in a
# temp directory. The seam is covered instead: the lander is checked to hand off to knight-land-live
# rather than restart in-line (the failure that would take down the caller mid-answer), and the live
# script is checked to verify the service came back rather than trust the restart's exit code.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
LAND="$HERE/bin/knight-land"
pass=0; fail=0
ok()  { printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL  %s\n     %s\n' "$1" "${2:-}"; fail=$((fail+1)); }

TMP=$(mktemp -d /tmp/knight-land-test.XXXXXX)
trap 'rm -rf "$TMP"' EXIT

# ── A world: one repo, one target registry, one job ──────────────────────────
new_world() {
    local name=$1 verdict=${2:-CLEAN} status=${3:-pushed}
    local w="$TMP/$name"
    mkdir -p "$w/repo" "$w/jobs/job1" "$w/targets"
    git -C "$w/repo" init -q -b main
    git -C "$w/repo" config user.email t@t.test; git -C "$w/repo" config user.name t
    echo one > "$w/repo/file.txt"
    git -C "$w/repo" add -A; git -C "$w/repo" commit -qm first
    git -C "$w/repo" checkout -q -b feature/x
    echo two > "$w/repo/file.txt"
    git -C "$w/repo" commit -qam second
    git -C "$w/repo" checkout -q main
    cat > "$w/targets/t1.env" <<EOF
KT_SOURCE=$w/repo
KT_BRANCH=main
KT_PUSH_MODE=branch
KT_LIVE_CMD=''
KT_LIVE_CHECK=''
EOF
    printf 'feature/x' > "$w/jobs/job1/branch"
    printf 't1'        > "$w/jobs/job1/target"
    printf '%s' "$status" > "$w/jobs/job1/status"
    printf '%s\n' "$verdict" > "$w/jobs/job1/review.txt"
    printf '%s' "$w"
}

land() {  # land <world> [args…]
    local w=$1; shift
    KNIGHT_JOBS="$w/jobs" KNIGHT_TARGETS="$w/targets" KNIGHT_LAND_LOG="$w/lands.jsonl" \
        "$LAND" "$@" 2>&1
}
merged() { git -C "$1/repo" log --oneline main | grep -qF "second"; }

echo "knight-land"

# ── The case it was built for ────────────────────────────────────────────────
w=$(new_world clean)
out=$(land "$w" job1)
if merged "$w"; then ok "a reviewed, clean branch merges on command"
else bad "a reviewed, clean branch merges on command" "$out"; fi
grep -q "Merged" <<<"$out" || bad "it says what it merged" "$out"
grep -q "merged and can be deleted" <<<"$out" && ok "it says the branch can go" || bad "it says the branch can go" "$out"
grep -q "merge is the whole job" <<<"$out" && ok "with no live command, it does not imply a deploy" \
    || bad "with no live command, it does not imply a deploy" "$out"

# It records what it did, where that can be read without asking Moses.
grep -q '"outcome":"merged"' "$w/lands.jsonl" 2>/dev/null && ok "the land is recorded" \
    || bad "the land is recorded" "$(cat "$w/lands.jsonl" 2>/dev/null)"

# ── Every refusal, watched refusing ──────────────────────────────────────────
# Each of these is the whole point of the script: a merge that happens anyway is the failure.

w=$(new_world confirmed "CONFIRMED: diagnose.py:12 — drops a failing check's evidence")
out=$(land "$w" job1)
if merged "$w"; then bad "a CONFIRMED review blocks the merge" "it merged anyway"
else grep -q "REFUSED" <<<"$out" && ok "a CONFIRMED review blocks the merge" || bad "a CONFIRMED review blocks the merge" "$out"; fi

w=$(new_world missed "MISSED: builds the wrong thing entirely")
out=$(land "$w" job1)
merged "$w" && bad "a MISSED review blocks the merge" "it merged anyway" \
             || ok "a MISSED review blocks the merge"

w=$(new_world unreadable "the reviewer crashed and wrote prose")
out=$(land "$w" job1)
merged "$w" && bad "an unreadable review blocks the merge" "it merged anyway" \
             || ok "an unreadable review blocks the merge"

# THE 2026-09-17 CASE. Zryachiy passed a branch and left a PLAUSIBLE note, and the note was the thing
# that mattered — it was the reason not to merge. A lander that treats "passing" as "land it" would
# have merged that branch.
w=$(new_world plausible "PLAUSIBLE: diagnose.py:167 — scope() could suppress a real site-down
CLEAN")
out=$(land "$w" job1)
if merged "$w"; then bad "an unanswered PLAUSIBLE note blocks the merge" "it merged anyway"
else grep -qi "note nobody has answered" <<<"$out" && ok "an unanswered PLAUSIBLE note blocks the merge, and says what the note was" \
     || bad "an unanswered PLAUSIBLE note blocks the merge, and says what the note was" "$out"; fi

# …and Brad can overrule it, because he is the one who decides whether a note is answered.
out=$(land "$w" job1 --anyway)
merged "$w" && ok "--anyway lands it when Brad has answered the note" \
             || bad "--anyway lands it when Brad has answered the note" "$out"

w=$(new_world unfinished CLEAN running)
out=$(land "$w" job1)
merged "$w" && bad "a job still running is refused" "it merged anyway" || ok "a job still running is refused"

w=$(new_world dirty)
echo scratch > "$w/repo/uncommitted.txt"
out=$(land "$w" job1)
if merged "$w"; then bad "a dirty checkout is refused" "it merged anyway"
else grep -q "uncommitted" <<<"$out" && ok "a dirty checkout is refused, and nothing is stashed" \
     || bad "a dirty checkout is refused, and nothing is stashed" "$out"; fi
[ -f "$w/repo/uncommitted.txt" ] && ok "somebody else's work is still there" || bad "somebody else's work is still there"

w=$(new_world twice)
land "$w" job1 >/dev/null
out=$(land "$w" job1)
grep -q "already merged" <<<"$out" && ok "landing twice says so instead of merging again" \
    || bad "landing twice says so instead of merging again" "$out"

w=$(new_world direct)
sed -i "s/KT_PUSH_MODE=branch/KT_PUSH_MODE=direct/" "$w/targets/t1.env"
out=$(land "$w" job1)
grep -q "push mode" <<<"$out" && ok "a target that deploys itself has nothing to land" \
    || bad "a target that deploys itself has nothing to land" "$out"

w=$(new_world unknown)
out=$(land "$w" no-such-branch)
grep -q "no job or branch" <<<"$out" && ok "an unknown name is refused by name" \
    || bad "an unknown name is refused by name" "$out"

# ── Reaching it by branch name, which is what Brad reads in Slack ────────────
w=$(new_world byname)
out=$(land "$w" feature/x)
merged "$w" && ok "a branch name works as well as a job id" || bad "a branch name works as well as a job id" "$out"

# ── --list agrees with what would actually happen ────────────────────────────
w=$(new_world listing)
out=$(land "$w" --list)
grep -q "READY" <<<"$out" && ok "--list shows a clean branch as ready" || bad "--list shows a clean branch as ready" "$out"
w=$(new_world listblocked "CONFIRMED: a.py:1 — broken")
out=$(land "$w" --list)
grep -q "BLOCKED" <<<"$out" && ok "--list shows a blocked branch as blocked, with the reason" \
    || bad "--list shows a blocked branch as blocked, with the reason" "$out"

# ── The ledger records itself without needing root ───────────────────────────
# THE FIRST REAL LAND (2026-09-17) merged perfectly and then could not write its own record: the log
# defaulted under /var/lib/moses, which is root-owned, and the shell's redirection error leaked into
# Moses's answer in Slack. Two separate faults — the path, and a `2>/dev/null` that cannot silence a
# redirection failure because the shell opens the file before the command runs.
grep -qE 'LOG=\$\{KNIGHT_LAND_LOG:-\$KNIGHT/' "$LAND" \
    && ok "the ledger lives beside the jobs, owned by the user that writes it" \
    || bad "the ledger default is back outside the user's own tree — it will need root to write"
w=$(new_world ledger)
out=$(KNIGHT_LAND_LOG=/var/lib/moses/definitely-not-writable.jsonl \
      KNIGHT_JOBS="$w/jobs" KNIGHT_TARGETS="$w/targets" "$LAND" job1 2>&1)
if grep -qiE "permission denied|cannot create|read-only" <<<"$out"; then
    bad "an unwritable ledger stays silent instead of leaking into the answer" "$out"
else
    ok "an unwritable ledger stays silent instead of leaking into the answer"
fi
merged "$w" && ok "and the merge still happens when the ledger cannot be written" \
             || bad "and the merge still happens when the ledger cannot be written" "$out"

# ── The seams that cannot be run in a temp directory ─────────────────────────
# THE LIVE STEP MUST LEAVE THE CALLER'S CGROUP, not merely its session. `setsid` alone was the first
# version and it failed the first time it ran for real: systemd kills a unit's whole cgroup when it
# stops it, and the detached child was still inside the MCP server's — so the restart killed the
# script running it. `moses` and `moses-mcp` came back, `moses-mcp-public` was never restarted, the
# health check never ran, and the Slack report never went. Measured after: a setsid child reads the
# same cgroup as its parent; a systemd-run child gets its own.
grep -q 'systemd-run --user --collect' "$LAND" \
    && ok "the live step is launched as its own unit, so the restart cannot kill it" \
    || bad "the live step is launched as its own unit, so the restart cannot kill it"
grep -q 'setsid "$HERE/knight-land-live"' "$LAND" \
    && ok "with no systemd available it still runs, and says the result may be cut short" \
    || bad "with no systemd available it still runs, and says the result may be cut short"
grep -q 'KT_LIVE_CHECK' "$HERE/bin/knight-land-live" \
    && ok "the live step verifies the service came back rather than trusting the restart" \
    || bad "the live step verifies the service came back rather than trusting the restart"
grep -q 'review-verdict.sh' "$LAND" && grep -q 'review-verdict.sh' "$HERE/bin/knight-run" \
    && ok "the merge gate and the push gate read the verdict from the same file" \
    || bad "the merge gate and the push gate read the verdict from the same file"

# ── END TO END, including the part that kept breaking ────────────────────────
# Both real failures lived AFTER the merge, which every check above would have missed: the first land
# left a service unrestarted and sent no report, and the fix for it then launched a unit that could
# not see its own job and failed silently. Greps confirmed the launch line each time and proved
# nothing. So this runs the whole thing — merge, hand off, restart, verify, record — with the restart
# standing in as a file that has to appear.
if command -v systemd-run >/dev/null 2>&1 && systemctl --user is-system-running >/dev/null 2>&1; then
    w=$(new_world e2e)
    cat > "$w/targets/t1.env" <<EOF
KT_SOURCE=$w/repo
KT_BRANCH=main
KT_PUSH_MODE=branch
KT_LIVE_CMD='touch $w/RESTARTED'
KT_LIVE_CHECK='test -f $w/RESTARTED'
EOF
    KNIGHT_JOBS="$w/jobs" KNIGHT_TARGETS="$w/targets" KNIGHT_LAND_LOG="$w/lands.jsonl" \
        KNIGHT_SLACK_ENV=/nonexistent "$LAND" job1 >/dev/null 2>&1
    # Wait for the RECORD, not the restart: the live step verifies the service came back before it
    # writes "landed", so the file appears seconds before the land is actually finished. Waiting on
    # the wrong one made this fail while everything worked.
    for _ in $(seq 1 25); do grep -q '"outcome":"landed"' "$w/lands.jsonl" 2>/dev/null && break; sleep 1; done
    [ -f "$w/RESTARTED" ] \
        && ok "end to end: the restart really runs, in a unit that outlives the caller" \
        || bad "end to end: the restart really runs, in a unit that outlives the caller" \
               "$(journalctl --user -u knight-land-live-job1 --no-pager --since '-2 minutes' 2>/dev/null | tail -3)"
    grep -q '"outcome":"landed"' "$w/lands.jsonl" 2>/dev/null \
        && ok "end to end: and the finished land records itself as live" \
        || bad "end to end: and the finished land records itself as live" "$(cat "$w/lands.jsonl" 2>/dev/null)"
else
    printf '  skip  end to end (no user systemd here)\n'
fi

printf '\n%d ok, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
