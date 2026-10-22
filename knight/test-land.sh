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
# NO TEST POSTS TO SLACK. Everything this suite runs — and anything those scripts hand off to, like
# knight-next after a landing — inherits this. test-land once did not, and its scratch target "t1"
# posted "Stopped" cards into Brad's channel on every run (2026-10-18). test-notify.sh checks every suite.
export KNIGHT_SLACK_CHANNEL="" KNIGHT_SLACK_ENV=/nonexistent

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

# ── A branch Knight pushed to the target's REMOTE, not this checkout (2026-10-14) ──────────────
# moses-framework pushes to GitHub, and knight-land only ever looked locally, so nothing there could
# land. Here the branch exists only in a bare "origin"; landing must fetch it and merge it.
w=$(new_world remote-only)
git init -q --bare "$w/origin.git"
git -C "$w/repo" remote add origin "$w/origin.git"
git -C "$w/repo" push -q origin feature/x 2>/dev/null
git -C "$w/repo" branch -q -D feature/x
out=$(land "$w" job1)
merged "$w" && ok "a branch that exists only on the push remote is fetched and lands" \
             || bad "a branch that exists only on the push remote is fetched and lands" "$out"

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
# The history must not call that landing clean (2026-10-14: every merge said "reviewed clean").
msg=$(git -C "$w/repo" log -1 --format=%s main)
grep -q "over a review note" <<<"$msg" && ! grep -q "reviewed clean" <<<"$msg" \
    && ok "a landing over a note says so in the merge message" \
    || bad "a landing over a note says so in the merge message" "$msg"

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

# ── #14: a branch that ADDS a Knight target needs Brad's one-time code ──────────────────────────
# Here the registry lives INSIDE the repository being merged, as it does for `moses`: that is the
# case where a Knight job could add a target in a branch and have this script merge it, skipping
# the code. The real helper runs unprivileged against a scratch seed (it ignores those overrides as
# root). Each refusal is then watched failing against a copy of the lander with that check removed.
MFA_HELPER="$HERE/../agent/moses-mfa"
MFA_SECRET=$(printf 'land-test-seed-01234' | base32 | tr -d '=')
mfa_world() {  # mfa_world <name> add|edit|rename
    local name=$1 change=$2 w="$TMP/$1"
    mkdir -p "$w/repo/knight/targets" "$w/jobs/job1" "$w/mfa"
    printf '%s\n' "$MFA_SECRET" > "$w/seed"
    git -C "$w/repo" init -q -b main
    git -C "$w/repo" config user.email t@t.test; git -C "$w/repo" config user.name t
    printf 'KT_SOURCE=%s\nKT_BRANCH=main\nKT_PUSH_MODE=branch\nKT_LIVE_CMD=\nKT_LIVE_CHECK=\n' "$w/repo" \
        > "$w/repo/knight/targets/t1.env"
    echo one > "$w/repo/file.txt"
    git -C "$w/repo" add -A; git -C "$w/repo" commit -qm first
    git -C "$w/repo" checkout -q -b feature/x
    echo two > "$w/repo/file.txt"
    case "$change" in
        add)    printf 'KT_SOURCE=/somewhere/new\nKT_BRANCH=main\nKT_PUSH_MODE=direct\n' > "$w/repo/knight/targets/newproj.env" ;;
        edit)   printf '# a comment, edited — it even mentions KT_SOURCE\nKT_PREP=(\x27true\x27)\n' >> "$w/repo/knight/targets/t1.env" ;;
        repoint) sed -i "s|^KT_SOURCE=.*|KT_SOURCE=/somewhere/else|" "$w/repo/knight/targets/t1.env" ;;
        evil)   printf 'eval "KT_SOU""RCE=/somewhere/else"\n' >> "$w/repo/knight/targets/t1.env" ;;
        autoflag) printf 'KT_AUTO_LAND=1\nKT_CONTINUE=1\n' >> "$w/repo/knight/targets/t1.env" ;;
        declare)  printf 'declare "KT_AUTO_""LAND=1"\n' >> "$w/repo/knight/targets/t1.env" ;;
        printfv)  printf 'printf -v "KT_CONT""INUE" 1\n' >> "$w/repo/knight/targets/t1.env" ;;
        rename) cp "$w/repo/knight/targets/t1.env" "$w/repo/knight/targets/t1.env.keep"
                git -C "$w/repo" mv knight/targets/t1.env knight/targets/sneaky.env ;;
    esac
    git -C "$w/repo" add -A; git -C "$w/repo" commit -qm second
    git -C "$w/repo" checkout -q main
    rm -f "$w/repo/knight/targets/t1.env.keep"
    printf 'feature/x' > "$w/jobs/job1/branch"; printf 't1' > "$w/jobs/job1/target"
    printf 'pushed' > "$w/jobs/job1/status"; printf 'CLEAN\n' > "$w/jobs/job1/review.txt"
    printf '%s' "$w"
}
mland() {  # mland <world> <lander> [args…]
    local w=$1 lander=$2; shift 2
    KNIGHT_JOBS="$w/jobs" KNIGHT_TARGETS="$w/repo/knight/targets" KNIGHT_LAND_LOG="$w/lands.jsonl" \
        KNIGHT_MFA_CMD="${MFA_CMD_OVERRIDE:-$MFA_HELPER}" MOSES_MFA_SEED="$w/seed" MOSES_MFA_STATE="$w/mfa" \
        "$lander" "$@" 2>&1
}
mfa() { local w=$1; shift; MOSES_MFA_SEED="$w/seed" MOSES_MFA_STATE="$w/mfa" "$MFA_HELPER" "$@"; }
authorize() {  # authorize <world> <target>
    local w=$1 c code
    c=$(mfa "$w" challenge new-build "$2" | python3 -c 'import json,sys; print(json.load(sys.stdin)["challenge"])')
    code=$(python3 -c '
import base64,hashlib,hmac,struct,sys,time
s=sys.argv[1]; k=base64.b32decode(s+"="*(-len(s)%8))
m=hmac.new(k,struct.pack(">Q",int(time.time()//30)),hashlib.sha1).digest(); o=m[-1]&15
print(str((struct.unpack(">I",m[o:o+4])[0]&0x7fffffff)%1000000).zfill(6))' "$MFA_SECRET")
    printf '%s' "$code" | mfa "$w" verify "$c" >/dev/null 2>&1
}

a5_cases() {  # a5_cases <lander> — echoes one "name=merged|refused" line per case
    local L=$1 w
    w=$(mfa_world "a5-noauth-$RANDOM" add); mland "$w" "$L" job1 >/dev/null
    merged "$w" && echo "noauth=merged" || echo "noauth=refused"
    w=$(mfa_world "a5-rename-$RANDOM" rename); mland "$w" "$L" job1 >/dev/null
    merged "$w" && echo "rename=merged" || echo "rename=refused"
    w=$(mfa_world "a5-broken-$RANDOM" add); MFA_CMD_OVERRIDE=/nonexistent/moses-mfa mland "$w" "$L" job1 >/dev/null
    merged "$w" && echo "broken=merged" || echo "broken=refused"
    w=$(mfa_world "a5-repoint-$RANDOM" repoint); mland "$w" "$L" job1 >/dev/null
    merged "$w" && echo "repoint=merged" || echo "repoint=refused"
    w=$(mfa_world "a5-declare-$RANDOM" declare); mland "$w" "$L" job1 >/dev/null
    merged "$w" && echo "declare=merged" || echo "declare=refused"
}

echo
echo "knight-land — a new Knight target needs Brad's one-time code (#14)"
w=$(mfa_world a5-noauth add)
out=$(mland "$w" "$LAND" job1)
! merged "$w" && grep -q "one-time code" <<<"$out" \
    && ok "a branch adding a target with no code waiting is refused, and nothing merges" \
    || bad "a branch adding a target with no code waiting is refused, and nothing merges" "$out"
out=$(mland "$w" "$LAND" --list)
grep -q "BLOCKED.*one-time code" <<<"$out" && ok "--list says why it is blocked" || bad "--list says why it is blocked" "$out"

authorize "$w" newproj
out=$(mland "$w" "$LAND" job1)
merged "$w" && grep -q "Used Brad's one-time code" <<<"$out" \
    && ok "with Brad's code waiting for that target, it merges" \
    || bad "with Brad's code waiting for that target, it merges" "$out"
st=$(mfa "$w" status)
grep -q '"waiting": \[\]' <<<"$st" && ok "and the code is spent by the merge" || bad "and the code is spent by the merge" "$st"

w=$(mfa_world a5-other add)
authorize "$w" someotherproject
out=$(mland "$w" "$LAND" job1)
! merged "$w" && ok "a code for a different project does not land this one" \
    || bad "a code for a different project does not land this one" "$out"

w=$(mfa_world a5-edit edit)
out=$(mland "$w" "$LAND" job1)
merged "$w" && ok "editing an existing target needs no code — even a comment naming KT_SOURCE (Brad, 2026-10-16)" \
    || bad "editing an existing target needs no code — even a comment naming KT_SOURCE (Brad, 2026-10-16)" "$out"

w=$(mfa_world a5-rename rename)
out=$(mland "$w" "$LAND" job1)
! merged "$w" && ok "renaming a target to a new name counts as adding one" \
    || bad "renaming a target to a new name counts as adding one" "$out"

w=$(mfa_world a5-broken add)
out=$(MFA_CMD_OVERRIDE=/nonexistent/moses-mfa mland "$w" "$LAND" job1)
! merged "$w" && grep -q "could not run" <<<"$out" \
    && ok "a code check that cannot run refuses, and says it could not check" \
    || bad "a code check that cannot run refuses, and says it could not check" "$out"

# BRAD, 2026-10-17: pointing an existing target at another folder is starting a new project, so a
# branch may never do it — refused outright, with no code that unlocks it.
w=$(mfa_world a5-repoint repoint)
out=$(mland "$w" "$LAND" job1)
! merged "$w" && grep -q "starting a new project" <<<"$out" \
    && ok "a branch that repoints an existing target is refused, and says why" \
    || bad "a branch that repoints an existing target is refused, and says why" "$out"
authorize "$w" t1
out=$(mland "$w" "$LAND" job1)
! merged "$w" && ok "and no code unlocks it — a new folder starts from the tracker" \
    || bad "and no code unlocks it — a new folder starts from the tracker" "$out"
w=$(mfa_world a5-evil evil)
out=$(mland "$w" "$LAND" job1)
! merged "$w" && ok "an eval that assembles KT_SOURCE is treated as a repoint" \
    || bad "an eval that assembles KT_SOURCE is treated as a repoint" "$out"

# A branch may not switch on "build away" for an existing target either: KT_AUTO_LAND and KT_CONTINUE
# are what Brad's code grants, so they are guarded like the folder lines.
w=$(mfa_world a5-autoflag autoflag)
out=$(mland "$w" "$LAND" job1)
! merged "$w" && ok "a branch that turns on auto-land or keep-building for a target is refused" \
    || bad "a branch that turns on auto-land or keep-building for a target is refused" "$out"

# REVIEW 2026-10-17: a setting can be assembled so no line names it. A changed target file may hold
# plain settings only, so these are refused for their shape, before any text comparison.
for kind in declare printfv; do
    w=$(mfa_world "a5-$kind" "$kind")
    out=$(mland "$w" "$LAND" job1)
    ! merged "$w" && grep -q "not a plain setting" <<<"$out" \
        && ok "a target line that assembles a setting ($kind) is refused" \
        || bad "a target line that assembles a setting ($kind) is refused" "$out"
done

# After a merge on a keep-building target, knight-next runs (detached) and records what it decided.
# With no tracker entry it stops for want of a scope — which proves it ran, and that it says so.
w=$(mfa_world a5-continue plain)   # the branch changes only file.txt
printf 'KT_CONTINUE=1\n' >> "$w/repo/knight/targets/t1.env"
git -C "$w/repo" commit -qam "keep building"
# A stand-in for `knight start`, so nothing here can start a real job even if a registry were found.
printf '#!/bin/sh\necho "$@" > %s/started-by-test\n' "$w" > "$w/fake-start"; chmod +x "$w/fake-start"
out=$(MOSES_REGISTRY="$w/no-registry.json" KNIGHT_START_BIN="$w/fake-start" mland "$w" "$LAND" job1)
for _ in $(seq 1 20); do grep -q '"outcome":"chain-' "$w/lands.jsonl" 2>/dev/null && break; sleep 0.5; done
merged "$w" && grep -q "Handed to knight-next" <<<"$out" && grep -q '"outcome":"chain-stopped"' "$w/lands.jsonl" \
    && ok "a merge on a keep-building target hands over to knight-next, which says what it decided" \
    || bad "a merge on a keep-building target hands over to knight-next, which says what it decided" "$out
$(cat "$w/lands.jsonl" 2>/dev/null)"

# REVIEW FINDING 5: a branch git cannot diff against main (no merge base) is refused, not waved on.
w=$(mfa_world a5-orphan edit)
git -C "$w/repo" checkout -q --orphan feature/x-orphan
git -C "$w/repo" commit -qm "second, from nowhere" --allow-empty
git -C "$w/repo" checkout -q main
printf 'feature/x-orphan' > "$w/jobs/job1/branch"
out=$(mland "$w" "$LAND" --list)
grep -qE "BLOCKED.*could not (work out|read)" <<<"$out" \
    && ok "a branch with no merge base is blocked because it could not be checked" \
    || bad "a branch with no merge base is blocked because it could not be checked" "$out"

# Watch each one fail: a copy of the lander with that check removed must merge. Two barriers stand in
# front of a new target — landable()'s check, and spending the code before the merge — so the mutants
# remove both (SPEND below). Removing one and staying refused is defense in depth, not blindness.
SPEND='s/|| die "the one-time code for new target/|| true; : "/'

MUT="$TMP/mut"; mkdir -p "$MUT"
cp -r "$HERE/bin" "$HERE/lib" "$MUT/"
watch_fail() {  # watch_fail <label> <case> <sed expression>
    rm -rf "$MUT/bin"; cp -r "$HERE/bin" "$MUT/bin"
    sed -i "$3" "$MUT/bin/knight-land"
    cmp -s "$HERE/bin/knight-land" "$MUT/bin/knight-land" && { bad "$1" "the sabotage no longer applies"; return; }
    grep -q "^$2=merged$" <<<"$(a5_cases "$MUT/bin/knight-land")" \
        && ok "watched fail: $1" || bad "watched fail: $1" "the suite did not see it"
}
watch_fail "with the check removed, a new target lands with no code" noauth 's/if \[ "\$rc" = 1 \]; then/if false; then/; s/elif \[ "\$rc" != 0 \]; then/elif false; then/; '"$SPEND"
watch_fail "with the repoint check removed, a branch moves a target to a new folder" repoint 's/\[ -n "\$moved" \]/false/'
watch_fail "with the shape check removed, an assembled setting lands" declare 's/if \[ -n "\$odd" \]; then/if false; then/'
watch_fail "with rename detection on, a renamed target slips through" rename 's/diff --no-renames/diff/'
watch_fail "with 'could not check' read as fine, a broken helper waves it through" broken 's/elif \[ "\$rc" != 0 \]; then/elif false; then/; '"$SPEND"

printf '\n%d ok, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
