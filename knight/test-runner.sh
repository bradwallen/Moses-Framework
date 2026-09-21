#!/usr/bin/env bash
# test-runner.sh — pins the two bugs that wedged Knight on 2026-08-08.
#
#   /home/brad/Projects/moses/knight/test-runner.sh        (free: no model calls, no pushes, seconds to run)
#
# These are regression tests for failures that had ALREADY shipped, so each asserts the BEHAVIOR,
# never the declaration. That distinction is the whole point:
#
#   BUG 1 — the brief was built as a double-quoted bash string. Prose containing quotes ("Write
#           None." …) terminated it early, so words from the spec ran as COMMANDS, $brief came out
#           unbound, and set -u killed the launch mid-flight. Content that looks like syntax must
#           never reach a parser. The test hands Knight a task full of shell metacharacters and
#           demands it come back byte-for-byte.
#
#   BUG 2 — the job record said "running" for 66 minutes with no process behind it: the record was
#           written before the worker existed, and nothing ever checked liveness. `doctor` printed
#           "wall-clock ceiling 60 min" the whole time, a claim nobody had exercised. The tests
#           below actually kill a job and actually reconcile a phantom.
#
# test-guards.sh covers the permission boundary; this covers the runner mechanics.

set -uo pipefail
. "$(dirname "$(readlink -f "$0")")/../agent/lib/moses-env.sh"   # whose home, where Moses lives, the operator's settings

KNIGHT=$MOSES_ROOT/knight/bin/knight
# ABSOLUTE, because this script does not cd to its own directory and the headline checks below
# read the runner from disk. They used a relative "bin/knight-run", so run from anywhere but
# knight/ they read NOTHING — two of them failed with an empty string, and the third PASSED,
# because `grep -q` on a missing file returns non-zero and fell into its own else branch. A
# check that reports ok when it could not open the file is the portability checker again.
RUN="${KNIGHT}-run"
# THIS SUITE GETS ITS OWN JOB RECORD, and nothing it does is visible in the real one.
#
# These jobs run a FAKE agent — `sleep`, not the model — so they cost nothing. They were still being
# written to knight/jobs, where they counted against the real daily ceiling and sat in the history
# Brad reads: six runs left 40 entries against a limit of 8, and a genuine start would have been
# refused. The suite's answer had been to raise its own ceiling to 200, which got the suite green and
# left the record dirty — treating the symptom.
#
# Now the record itself is redirected, so the ceiling can stay at its real value and be exercised
# honestly. REAL_JOBS is kept only so the check at the end of this file can prove we never touched it.
REAL_JOBS=$MOSES_ROOT/knight/jobs
JOBS=$(mktemp -d /tmp/knight-test-jobs-XXXXXX)
export KNIGHT_JOBS="$JOBS"
# AND IT POSTS NOWHERE. The suite was putting real Knight and Zryachiy messages into #viatica-dev —
# "sleep past the ceiling on purpose", zzz-landing reviews — in the channel Brad reads, looking
# exactly like work. Empty means the existing `-n` guard skips the post; see bin/knight.
export KNIGHT_SLACK_CHANNEL=""
_real_before=$(ls -1 "$REAL_JOBS" 2>/dev/null | wc -l)
TARGETS=$MOSES_ROOT/knight/targets

# ONE cleanup, run once. This file used to set a new `trap … EXIT` per section, and each one REPLACED
# the last — so the job directory and earlier scratch trees were never removed once a later section
# had set its own. Everything a section creates is registered here instead.
cleanup=(); scratch_dirs=("$JOBS")
on_exit() {
    local j d
    for j in "${cleanup[@]:-}"; do [ -n "$j" ] && rm -rf "$JOBS/$j"; done
    rm -f "$TARGETS"/zzz-*.env
    for d in "${scratch_dirs[@]:-}"; do [ -n "$d" ] && rm -rf "$d"; done
}
trap on_exit EXIT

# THE SUITE'S OWN TARGET. The job-mechanics checks below (a brief survives the shell, a timeout is
# recorded) do not depend on which repository a job is for, so they run against a throwaway one here
# instead of the operator's real product — which also means they run on a fresh checkout that has no
# product at all. KNIGHT_DEFAULT_TARGET points `knight start` at it. The REAL remote is still checked,
# on purpose, in the push-target section.
SUITE=$(mktemp -d /tmp/knight-test-suite-XXXXXX); scratch_dirs+=("$SUITE")
git init -q --bare "$SUITE/remote.git"
git init -q -b main "$SUITE/src"
git -C "$SUITE/src" -c user.email=t@t.test -c user.name=t commit -q --allow-empty -m "suite root"
git -C "$SUITE/src" remote add origin "$SUITE/remote.git"
git -C "$SUITE/src" push -q origin main 2>/dev/null
cat > "$TARGETS/zzz-suite.env" <<SUITE_ENV
KT_DESC="written by test-runner.sh — a throwaway repository, removed on exit"
KT_SOURCE=$SUITE/src
KT_BRANCH=main
KT_PUSH_MODE=branch
KT_GATE=( 'true' )
SUITE_ENV
export KNIGHT_DEFAULT_TARGET=zzz-suite
pass=0; fail=0

hdr() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$*"; pass=$((pass+1)); }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; fail=$((fail+1)); }

# Take the job id from the command's OWN OUTPUT, never from "newest directory on disk".
#
# The first version guessed with `ls -1dr | head -1`, and it silently lied: the previous test's
# worker was still alive, so `knight start` refused on the 1/1 concurrency limit, created nothing,
# and the guess returned the PREVIOUS job — which the test then happily asserted against and
# reported a failure that did not exist. A test that cannot tell which object it is examining is
# worse than no test. start_job fails loudly if no job was created.
start_job() {
    local out id
    out=$("$@" 2>&1)
    id=$(printf '%s\n' "$out" | awk '/^started/ {print $2}')
    if [ -z "$id" ]; then
        printf '%s\n' "$out" | sed 's/^/        /' >&2
        return 1
    fi
    printf '%s' "$id"
}

# Block until a job stops running, so the next start is not refused by the concurrency guard.
wait_done() {
    local id="$1" i
    for i in $(seq 1 30); do
        [ "$(cat "$JOBS/$id/status" 2>/dev/null)" = "running" ] || return 0
        sleep 2
    done
    return 1
}

# ── BUG 1: the brief must survive the shell ─────────────────────────────────
hdr "Brief fidelity — a task full of shell metacharacters must arrive intact"

# Every construct that has ever broken a shell string: backticks, command substitution, double and
# single quotes, an apostrophe, a dollar variable, a semicolon, and newlines.
NASTY='Fix the "None." handling; run `date` and $(whoami) safely.
Brad'"'"'s note: $HOME must not expand, and *asterisks* stay literal.
Second line with a trailing quote: "'

job=$(start_job env KNIGHT_FAKE_AGENT=1 "$KNIGHT" start --acceptance "the job record exists and reaches a terminal status" "$NASTY") || job=""
[ -n "$job" ] && cleanup+=("$job")

if [ -z "$job" ] || [ ! -d "$JOBS/$job" ]; then
    bad "knight start did not create a job at all (output above)"
else
    # task.txt is what was recorded; brief.txt is what the agent actually receives. Both must match
    # the input exactly — comparing only one would miss a corruption introduced by the other.
    if diff -q <(printf '%s\n' "$NASTY") "$JOBS/$job/task.txt" >/dev/null 2>&1; then
        ok "task.txt matches the input byte-for-byte"
    else
        bad "task.txt was mangled:"; diff <(printf '%s\n' "$NASTY") "$JOBS/$job/task.txt" | head -5 | sed 's/^/        /'
    fi

    if [ ! -f "$JOBS/$job/brief.txt" ]; then
        bad "brief.txt was never written — the launch aborted, which is the original bug"
    elif [ "$(sed -n '/^TASK:$/,$p' "$JOBS/$job/brief.txt" | tail -n +2)" = "$NASTY" ]; then
        ok "the TASK section of brief.txt matches byte-for-byte"
    else
        bad "brief.txt's TASK section does not match the input"
        sed -n '/^TASK:$/,$p' "$JOBS/$job/brief.txt" | tail -n +2 | head -5 | sed 's/^/        got: /'
    fi

    # The specific symptom: fragments of the spec executed as commands and landed in stderr.
    if grep -qE "command not found|unbound variable" "$JOBS/$job/stderr.log" 2>/dev/null; then
        bad "shell errors in stderr — something in the brief is still being expanded:"
        grep -E "command not found|unbound variable" "$JOBS/$job/stderr.log" | head -3 | sed 's/^/        /'
    else
        ok "no 'command not found' or 'unbound variable' — nothing was expanded"
    fi
fi

# ── BUG 2a: the wall-clock ceiling must actually kill ───────────────────────
hdr "Wall-clock ceiling — the backstop the unattended design leans on"

wait_done "$job" || bad "the fidelity job never finished, so the next start will be refused"

job=$(start_job env KNIGHT_FAKE_AGENT=120 KNIGHT_TIMEOUT_SEC=5 "$KNIGHT" start --acceptance "the job record exists and reaches a terminal status" "sleep past the ceiling on purpose") || job=""
if [ -z "$job" ]; then
    bad "knight start did not create a job (output above)"; st=""; pid=""
else
    cleanup+=("$job")
    pid=$(cat "$JOBS/$job/pid" 2>/dev/null)
    wait_done "$job"
    st=$(cat "$JOBS/$job/status" 2>/dev/null)
fi

if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    bad "the worker is STILL ALIVE past its ceiling — the SIGKILL did not fire"
else
    ok "the worker process is dead"
fi
if [ "$st" = "timeout" ]; then
    ok "the record says 'timeout' — the outcome was written, not just enacted"
else
    bad "the record says '$st', not 'timeout' — a kill nobody records is a kill nobody sees"
fi

# ── BUG 2b: a phantom job must not hold the slot ────────────────────────────
hdr "Liveness — a record claiming 'running' with no process is reconciled, not believed"

phantom="19700101-000000-999999"
mkdir -p "$JOBS/$phantom"; cleanup+=("$phantom")
printf 'running\n'      > "$JOBS/$phantom/status"
printf 'phantom\n'      > "$JOBS/$phantom/task.txt"
printf 'x\n'            > "$JOBS/$phantom/branch"
date +%Y-%m-%dT%H:%M:%S%z > "$JOBS/$phantom/started"
# No pid file at all — exactly the state job 20260808-141955-3205674 was left in.

"$KNIGHT" doctor >/dev/null 2>&1     # any command reconciles
st=$(cat "$JOBS/$phantom/status")
if [ "$st" = "orphaned" ]; then
    ok "reconciled to 'orphaned' instead of being trusted"
else
    bad "still '$st' — a phantom job would keep holding the 1/1 concurrency slot"
fi

# NOT `doctor | grep -q`. Under `set -o pipefail`, grep -q exits the moment it matches, closing the
# pipe; doctor then dies of SIGPIPE (141) and the PIPELINE reports failure even though the pattern
# matched perfectly. That produced a red "the slot is still held" for a slot that was free — a test
# failing for a reason with nothing to do with what it tests. Match against a captured string.
doc=$("$KNIGHT" doctor 2>/dev/null)
if [[ "$doc" =~ jobs\ running[[:space:]]+0\ / ]]; then
    ok "the concurrency slot is free, so Knight is not wedged"
else
    bad "the slot is still held by a job with no process behind it"
fi

# ── BUG 3: the push target must be REACHABLE, not merely configured ─────────
hdr "Push target — the remote must authenticate before an hour of work depends on it"

# Knight built the /features page, went green on 231 tests, and only THEN failed with "could not
# read Username for 'https://github.com'". guards.env had rewritten Brad's SSH origin into an https
# URL, and nothing on Reserve can authenticate over https — no gh, no credential helper, no token.
# The end-to-end proof missed it completely because it pushed to a LOCAL BARE REPO, which needs no
# credentials at all. Proving the sequence is not proving the connection.
# REAL targets only: the suite's own throwaway targets (zzz-*) would pass without any credentials,
# which is exactly the blindness described above.
real_targets=$(ls "$TARGETS"/*.env 2>/dev/null | xargs -r -n1 basename | sed 's/\.env$//' | grep -v '^zzz-' || true)
if [ -z "$real_targets" ]; then
    bad "COULD NOT CHECK a real push target — none is registered in $TARGETS (see its README.md)"
else
    unreachable=""
    for t in $real_targets; do
        sect=$(printf '%s\n' "$doc" | awk -v t="$t" '$0 == "  target: "t {on=1; next} /^  target: / {on=0} on')
        [[ "$sect" =~ push\ target.*reachable ]] || unreachable="$unreachable $t"
    done
    if [ -z "$unreachable" ]; then
        ok "doctor reaches every real target's remote (a live ls-remote, not a printed URL): $(echo $real_targets)"
    else
        bad "doctor cannot reach the push target of:$unreachable — a green build would build, then fail to land:"
        printf '%s\n' "$doc" | grep -A2 "push target" | sed 's/^/        /'
    fi
fi

# ── The daily ceiling must actually refuse ──────────────────────────────────
hdr "Daily ceiling — the limit that keeps Knight from spending the day's allowance"

# Tested by setting it, not by trusting the number that `doctor` prints. Raising the ceiling for the
# rest of this suite is what makes these tests free; that is only safe if the ceiling still works.
out=$(KNIGHT_MAX_PER_DAY=0 "$KNIGHT" start --acceptance "the job record exists and reaches a terminal status" "build something reasonable and testable" 2>&1)
if [[ "$out" == *"jobs already today"* ]] || [[ "$out" == *"max 0"* ]]; then
    ok "a start is refused once the daily ceiling is reached"
else
    bad "the daily ceiling did not refuse a start: $out"
fi

# ── The target registry: refuse by name, and land where the registry says ───
hdr "Target registry — Knight works on repositories that are written down, and only those"

# An unknown name must be refused AND must say what the alternatives are. A bare "unknown target" is
# how an agent ends up guessing at a name, and Moses reaches Knight through a tool call where a
# guess costs a whole job.
out=$("$KNIGHT" start --target nope --acceptance "the job record exists and reaches a terminal status" "build something reasonable and testable" 2>&1)
if [[ "$out" == *"no such target 'nope'"* ]] && [[ "$out" == *zzz-suite* ]]; then
    ok "an unregistered target is refused by name, and the refusal lists the real ones"
else
    bad "an unregistered target was not cleanly refused: $out"
fi

# A target file that is present but wrong must fail at load, not at push. The push is an hour of
# Brad's Pro allowance later, and by then the work exists and cannot land.
cat > "$TARGETS/zzz-broken.env" <<'BROKEN'
KT_DESC="deliberately malformed, written by test-runner.sh"
KT_SOURCE="$MOSES_ROOT"   # any real checkout; the malformed line below is what is under test
KT_BRANCH=master
KT_PUSH_MODE=whatever
KT_GATE=( 'true' )
BROKEN
out=$("$KNIGHT" start --target zzz-broken --acceptance "the job record exists and reaches a terminal status" "build something reasonable and testable" 2>&1)
if [[ "$out" == *"KT_PUSH_MODE must be"* ]]; then
    ok "a malformed target is refused at load, before any work is done"
else
    bad "a target with an invalid landing mode was accepted: $out"
fi

# Same for a target with no objective check. "No gate" must never quietly mean "everything ships".
cat > "$TARGETS/zzz-broken.env" <<'BROKEN'
KT_DESC="deliberately gateless, written by test-runner.sh"
KT_SOURCE="$MOSES_ROOT"   # any real checkout; the malformed line below is what is under test
KT_BRANCH=master
KT_PUSH_MODE=direct
BROKEN
out=$("$KNIGHT" start --target zzz-broken --acceptance "the job record exists and reaches a terminal status" "build something reasonable and testable" 2>&1)
if [[ "$out" == *"no KT_GATE"* ]]; then
    ok "a target with no gate is refused — no gate never means everything ships"
else
    bad "a gateless target was accepted: $out"
fi
rm -f "$TARGETS/zzz-broken.env"

# ── The demarcation, end to end ─────────────────────────────────────────────
hdr "Landing mode — branch-mode work must NOT reach the mainline"

# This is the whole reason the registry exists rather than a flag, so it is proven against real git
# rather than by reading the script. A scratch repo stands in for Moses: green work must arrive as a
# branch, and the mainline must be byte-for-byte where it was.
scratch=$(mktemp -d /tmp/knight-landing-XXXXXX)
(
  set -e
  git -C "$scratch" init -q -b main src
  cd "$scratch/src"
  git config user.email t@t; git config user.name t
  echo one > file.txt; git add file.txt; git commit -qm one
) || bad "could not build the scratch repo"
before=$(git -C "$scratch/src" rev-parse main)

cat > "$TARGETS/zzz-landing.env" <<LANDING
KT_DESC="scratch target, written by test-runner.sh"
KT_SOURCE=$scratch/src
KT_BRANCH=main
KT_PUSH_MODE=branch
KT_PUSH_REMOTE=$scratch/src
KT_PREP=()
KT_GATE=( 'true' )
LANDING
scratch_dirs+=("$scratch")

# Provision the clone the way `knight start` does, then put a commit on a work branch and hand it
# straight to the runner with the fake agent — no model, no spend, real git.
clone="$scratch/clone"
git clone -q --no-hardlinks "$scratch/src" "$clone"
git -C "$clone" remote remove origin
git -C "$clone" fetch -q "$scratch/src" main
git -C "$clone" checkout -q --detach FETCH_HEAD
git -C "$clone" checkout -q -B knight/scratch-test
git -C "$clone" config user.email t@t; git -C "$clone" config user.name t
echo two >> "$clone/file.txt"
git -C "$clone" commit -qam "work from a fake agent"

jd=$(mktemp -d /tmp/knight-landing-job-XXXXXX)
KNIGHT_FAKE_AGENT=1 "$MOSES_ROOT/knight/bin/knight-run" "$jd" "$clone" knight/scratch-test zzz-landing >/dev/null 2>&1

after=$(git -C "$scratch/src" rev-parse main)
landed=$(git -C "$scratch/src" rev-parse --verify -q refs/heads/knight/scratch-test || true)
if [ -n "$landed" ] && [ "$before" = "$after" ]; then
    ok "green branch-mode work arrived as knight/scratch-test and main did not move"
elif [ "$before" != "$after" ]; then
    bad "BRANCH-MODE WORK LANDED ON THE MAINLINE — the demarcation is not holding"
else
    bad "nothing was pushed at all; the gate or the push failed: $(cat "$jd/summary" 2>/dev/null)"
fi
rm -rf "$jd"
rm -f "$TARGETS/zzz-landing.env"; rm -rf "$scratch"

# ── An incident is closed only by work that actually landed ─────────────────
hdr "Incident close — nothing shipped means nothing is fixed"

# The dangerous direction is the false close. An incident marked investigated against work that never
# reached anybody is worse than one left open, because the queue then tells everyone it is handled and
# nobody looks again. Green gate is not the test; a completed PUSH is.
scratch2=$(mktemp -d /tmp/knight-incident-XXXXXX)
(
  set -e
  git -C "$scratch2" init -q -b main src
  cd "$scratch2/src"; git config user.email t@t; git config user.name t
  echo one > file.txt; git add file.txt; git commit -qm one
) || bad "could not build the scratch repo"

cat > "$TARGETS/zzz-incident.env" <<INC
KT_DESC="scratch target, written by test-runner.sh"
KT_SOURCE=$scratch2/src
KT_BRANCH=main
KT_PUSH_MODE=branch
KT_PUSH_REMOTE=$scratch2/src
KT_PREP=()
KT_GATE=( 'false' )
INC
scratch_dirs+=("$scratch2")

clone2="$scratch2/clone"
git clone -q --no-hardlinks "$scratch2/src" "$clone2"
git -C "$clone2" remote remove origin
git -C "$clone2" fetch -q "$scratch2/src" main
git -C "$clone2" checkout -q --detach FETCH_HEAD
git -C "$clone2" checkout -q -B knight/incident-test
git -C "$clone2" config user.email t@t; git -C "$clone2" config user.name t
echo two >> "$clone2/file.txt"
git -C "$clone2" commit -qam "work that will not pass the gate"

jd2=$(mktemp -d /tmp/knight-incident-job-XXXXXX)
KNIGHT_FAKE_AGENT=1 "$MOSES_ROOT/knight/bin/knight-run" "$jd2" "$clone2" knight/incident-test zzz-incident "inc_fake_12345" >/dev/null 2>&1

summary=$(cat "$jd2/summary" 2>/dev/null)
if printf '%s' "$summary" | grep -q "left OPEN"; then
    ok "a red gate leaves the incident open, and the job says so"
else
    bad "a job that shipped nothing did not report the incident as left open: $summary"
fi
rm -rf "$jd2"

# THE STRUCTURAL GUARANTEE: the agent never holds the credential. Asserted against a brief a real
# `knight start` produced, not against the runner invoked by hand — the runner has the secret by
# design, and checking the wrong artifact would have proved nothing while looking thorough.
if bid=$(KNIGHT_FAKE_AGENT=1 start_job "$KNIGHT" start --target zzz-incident --acceptance "the job record exists and reaches a terminal status" --incident inc_fake_12345 \
            "fix the thing the incident describes, with a test"); then
    brief="$JOBS/$bid/brief.txt"
    if [ -f "$brief" ] && grep -q "inc_fake_12345" "$brief" \
       && ! grep -qiE 'CRON_SECRET|Authorization|api/incidents/agent|Bearer' "$brief"; then
        ok "the brief names the incident and carries no credential or endpoint"
    else
        bad "the brief either lost the incident id or leaked a way to reach the incident API"
    fi
    if [ "$(cat "$JOBS/$bid/incident" 2>/dev/null)" = "inc_fake_12345" ]; then
        ok "the job records which incident it came from, so the runner can act on it"
    else
        bad "the incident id did not reach the job record"
    fi
    "$KNIGHT" cancel "$bid" >/dev/null 2>&1
else
    bad "could not start a job with --incident"
fi
rm -f "$TARGETS/zzz-incident.env"; rm -rf "$scratch2"

# ── The report must say where the work went ─────────────────────────────────
hdr "Slack headline — a branch-mode job must not announce a deploy"

# #viatica-dev carried a column of "✅ Pushed to `master` → Customs deploying" for jobs that pushed a
# branch on another target, and for scratch runs that never touched Viatica. The headline was written
# when Viatica was the only target and never learned about landing modes — so the one distinction the
# registry exists to record was thrown away in the one place Brad reads.
#
# Asserted against the script's own text because the alternative is posting to a real channel to find
# out. The shapes are what matter: a direct-mode line may promise a deploy, a branch-mode line must
# say the opposite, and neither may be hardcoded.
[ -f "$RUN" ] || { bad "cannot read $RUN — the three headline checks below are watching nothing"; RUN=/dev/null; }
if grep -q 'Pushed to \\`master\\`\* → Customs deploying' "$RUN"; then
    bad "the success headline is still hardcoded to master/Customs regardless of target"
else
    ok "the success headline is no longer hardcoded to master/Customs"
fi

direct_line=$(grep -n 'KT_PUSH_MODE" = direct \]; then' -A 1 "$RUN" | grep 'head=' || true)
branch_line=$(grep -n 'elif \[ "\$pushed" -eq 1 \]; then' -A 1 "$RUN" | grep 'head=' || true)

if printf '%s' "$direct_line" | grep -q 'KT_BRANCH' && printf '%s' "$direct_line" | grep -q 'TARGET'; then
    ok "a direct-mode job names its target and the branch it landed on"
else
    bad "the direct-mode headline does not name the target and branch: $direct_line"
fi

if printf '%s' "$branch_line" | grep -qi 'NOT merged' && printf '%s' "$branch_line" | grep -q 'TARGET'; then
    ok "a branch-mode job says plainly that nothing is live"
else
    bad "the branch-mode headline does not say the work is unmerged: $branch_line"
fi

if printf '%s' "$branch_line" | grep -qi 'deploying'; then
    bad "the branch-mode headline still claims a deploy"
else
    ok "and it does not claim a deploy"
fi

# ── This suite must leave no trace in the real record ───────────────────────
# The isolation above is the fix; this is the thing that watches it. A redirect that silently stops
# working would put fake jobs back into Brad's history and back onto the daily ceiling, and the only
# symptom would be a start refused days later for no visible reason.
hdr "The real job record is untouched"
_real_after=$(ls -1 "$REAL_JOBS" 2>/dev/null | wc -l)
if [ "$_real_before" -eq "$_real_after" ]; then
    ok "no test job was written to $REAL_JOBS ($_real_after entries, unchanged)"
else
    bad "this suite wrote $((_real_after - _real_before)) job(s) into the REAL record — \
KNIGHT_JOBS is not being honored"
fi
if [ "$(ls -1 "$JOBS" 2>/dev/null | wc -l)" -gt 0 ]; then
    ok "its jobs went to a temporary record instead"
else
    bad "no jobs landed anywhere — this suite is no longer exercising job creation at all"
fi
# A post is only attempted inside the `-n $chan` guard, and that is the branch that writes
# slack.json. So the absence of that file across every job this suite ran IS the evidence that
# nothing reached the channel — behavior, not a promise in a comment.
_posts=$(find "$JOBS" -name slack.json 2>/dev/null | wc -l)
if [ "$_posts" -eq 0 ]; then
    ok "and nothing was posted to Slack"
else
    bad "$_posts Slack post(s) were made by this suite — KNIGHT_SLACK_CHANNEL is not being honored"
fi

# ── Verdict ─────────────────────────────────────────────────────────────────
hdr "Result"
printf '  %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -gt 0 ] && { echo "  ❌ A regression is live. Leave Knight disabled." >&2; exit 1; }
echo "  ✅ The 2026-08-08 failures are pinned."
