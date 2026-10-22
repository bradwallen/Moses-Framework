#!/usr/bin/env bash
# test-next.sh — knight-next: when a job lands by itself, and when Knight briefs the next one.
#
#   bash knight/test-next.sh
#
# Real git repositories and real job records; knight-land and `knight start` are stand-ins that record
# what they were asked, because what is under test is the DECISION — land or hold, continue or stop —
# not the landing or the starting, which have their own suites. The tracker is a scratch registry read
# through the real mcp/projects.py. Then each hold is removed from a copy of knight-next and the case
# it protects must go wrong.
set -uo pipefail
# NO TEST POSTS TO SLACK. Everything this suite runs — and anything those scripts hand off to, like
# knight-next after a landing — inherits this. test-land once did not, and its scratch target "t1"
# posted "Stopped" cards into Brad's channel on every run (2026-10-18). test-notify.sh checks every suite.
export KNIGHT_SLACK_CHANNEL="" KNIGHT_SLACK_ENV=/nonexistent
HERE=$(cd "$(dirname "$0")" && pwd)
NEXT="$HERE/bin/knight-next"
pass=0; fail=0
ok()  { printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL  %s\n     %s\n' "$1" "${2:-}"; fail=$((fail+1)); }
TMP=$(mktemp -d /tmp/knight-next-test.XXXXXX)
trap 'rm -rf "$TMP"' EXIT
PLACEHOLDER='echo "no checks yet: the first build job replaces this gate"; exit 0'

# world <name> <kind> <gate: real|placeholder> <branch touches gate: yes|no> [status] [report]
world() {
    local w="$TMP/$1" kind=$2 gate=$3 touch=$4 status=${5:-pushed} report=${6:-"*What shipped* — the search page."}
    mkdir -p "$w/src" "$w/targets" "$w/jobs/20991231-000000-1"
    git -C "$w/src" init -q -b main
    git -C "$w/src" config user.email t@t.test; git -C "$w/src" config user.name t
    if [ "$gate" = real ]; then printf '#!/bin/sh\nnpm test\n' > "$w/src/gate.sh"
    else printf '#!/bin/sh\n%s\n' "$PLACEHOLDER" > "$w/src/gate.sh"; fi
    echo one > "$w/src/app.txt"
    # Every project carries docs/ARCHITECTURE.md (commandment V); W_NODOC=1 makes one that does not.
    [ -n "${W_NODOC:-}" ] || { mkdir -p "$w/src/docs"; echo "components and flows" > "$w/src/docs/ARCHITECTURE.md"; }
    printf '{"scripts":{"test":"vitest"},"dependencies":{"a":"1"}}\n' > "$w/src/package.json"
    git -C "$w/src" add -A; git -C "$w/src" commit -qm first
    git -C "$w/src" checkout -q -b knight/x
    echo two > "$w/src/app.txt"
    case "$touch" in
        yes)      printf '#!/bin/sh\nexit 0\n' > "$w/src/gate.sh" ;;
        realgate) printf '#!/bin/sh\ntest -f app.txt\n' > "$w/src/gate.sh" ;;                 # depends on the code
        fakegate) printf '#!/bin/sh\necho "all checks pass"\nexit 0\n' > "$w/src/gate.sh" ;;  # checks nothing
        scripts)  printf '{"scripts":{"test":"exit 0"},"dependencies":{"a":"1"}}\n' > "$w/src/package.json" ;;
        deps)     printf '{"scripts":{"test":"vitest"},"dependencies":{"a":"1","b":"2"}}\n' > "$w/src/package.json" ;;
        conftest) printf 'import pytest\n' > "$w/src/conftest.py" ;;
    esac
    git -C "$w/src" add -A; git -C "$w/src" commit -qm second
    git -C "$w/src" checkout -q main
    printf 'KT_SOURCE=%s\nKT_BRANCH=main\nKT_PUSH_MODE=branch\nKT_AUTO_LAND=1\nKT_CONTINUE=1\n' "$w/src" > "$w/targets/proj.env"
    local j="$w/jobs/20991231-000000-1"
    printf 'proj' > "$j/target"; printf 'knight/x' > "$j/branch"; printf '%s' "$status" > "$j/status"
    printf '%s\n' "$report" > "$j/report.txt"
    printf '%s\n' "${W_SUMMARY:-commits=1}" > "$j/summary"
    printf '%s\n' "${W_REVIEW:-CLEAN}" > "$j/review.txt"
    printf 'Build the thing.\n' > "$j/task.txt"; printf 'The thing works.\n' > "$j/acceptance.txt"
    [ -n "${W_ROUND:-}" ] && printf '%s\n' "$W_ROUND" > "$j/fixup-round"
    [ -n "${W_QUIET:-}" ] && : > "$j/quiet"
    printf '{"channel":"C_THREAD","thread_ts":"1.0"}\n' > "$j/notify.json"
    python3 - "$w/projects.json" "$kind" <<'PY'
import json, sys
import os
json.dump({"projects": [{"id": "proj", "name": "Proj", "rank": 1, "status": os.environ.get("W_STATUS", "active"),
           "stage": os.environ.get("W_STAGE", "building"),
           "kind": sys.argv[2], "milestones": [], "ideas": [],
           # A business with its demand answered, so it is the KIND that holds it, not the build rule.
           "who_pays": "a named school", "evidence": "two asked", "kill_test": "nobody signs up in a month",
           "scope": "Aggregate internships, filter by skill.",
           "publish_path": os.environ.get("W_PUBLISH", ""), "deploy": ""}]}, open(sys.argv[1], "w"))
PY
    # Stand-ins: knight-land records the job; `knight start` records its argv and names a new job.
    printf '#!/bin/sh\necho "$@" >> %s/landed\necho "Merged"\n' "$w" > "$w/fake-land"
    # BUSY=n makes the first n starts answer "already running", as a job in flight would.
    printf '#!/bin/sh\nif [ "$1" = provision ]; then echo "$2" >> %s/provisioned; if [ "$(wc -l < %s/provisioned)" -le "${PROV_BUSY:-0}" ]; then echo "knight: 1 job(s) already running — provision resets the clone"; exit 1; fi; if [ "${W_PROV:-green}" = green ]; then echo "      green"; exit 0; fi; echo "      RED — this target cannot ship work until this passes:"; echo "      gate.sh: not ok 3 - keeps 2027"; exit 1; fi\nc=$(cat %s/busy-count 2>/dev/null || echo 0); if [ "$c" -lt "${BUSY:-0}" ]; then echo $((c+1)) > %s/busy-count; echo "knight: 1 job(s) already running (max 1)"; exit 1; fi\nfor a in "$@"; do printf "%%s\\n" "$a"; done > %s/started\nmkdir -p %s/jobs/20991231-000001-2\necho "started  20991231-000001-2"\n' "$w" "$w" "$w" "$w" "$w" "$w" > "$w/fake-start"
    printf '#!/usr/bin/env python3\nimport sys\nopen("%s", "a").write(" ".join(sys.argv[1:]) + "\\n")\n' "$w/glance" > "$w/fake-glance"
    printf '#!/bin/sh\necho "$@" > %s/published\necho "Live: https://example/$2/"\n' "$w" > "$w/fake-publish"
    chmod +x "$w/fake-land" "$w/fake-start" "$w/fake-publish"
    printf '%s' "$w"
}
run() {  # run <world> <knight-next> [args…]
    local w=$1 bin=$2; shift 2
    KNIGHT_JOBS="$w/jobs" KNIGHT_TARGETS="$w/targets" KNIGHT_LAND_LOG="$w/lands.jsonl" \
        KNIGHT_SLACK_ENV=/nonexistent KNIGHT_LAND_BIN="$w/fake-land" KNIGHT_START_BIN="$w/fake-start" \
        MOSES_REGISTRY="$w/projects.json" MOSES_MCP_DIR="$HERE/../mcp" KNIGHT_NEXT_WAIT_SEC=0 \
        MOSES_PUBLISH_BIN="$w/fake-publish" KNIGHT_NOTES_DIR="$w/notes" KNIGHT_GLANCE_BIN="$w/fake-glance" \
        KNIGHT_LIVE_PROBE="${W_PROBE:-true}" "$bin" "$@" 2>&1
}
J=20991231-000000-1
landed()  { [ -s "$1/landed" ]; }
started() { [ -s "$1/started" ]; }

cases() {  # cases <knight-next> — one "name=outcome" line each, for the watched-fail runs
    local B=$1 w
    w=$(world "c-biz-$RANDOM" business real no);           run "$w" "$B" "$J" >/dev/null; landed "$w" && echo business=landed || echo business=held
    w=$(world "c-ph-$RANDOM" internal placeholder no);     run "$w" "$B" "$J" >/dev/null; landed "$w" && echo placeholder=landed || echo placeholder=held
    w=$(world "c-fk-$RANDOM" internal placeholder fakegate); run "$w" "$B" "$J" >/dev/null; landed "$w" && echo fakegate=landed || echo fakegate=held
    w=$(W_REVIEW='UNCHECKED: 2 — live only' world "c-un-$RANDOM" internal real no); run "$w" "$B" "$J" >/dev/null; landed "$w" && echo unchecked=landed || echo unchecked=held
    w=$(W_SUMMARY=$'commits=2\ngate=green\npushed=no — review MISSED the request: 2 — no test' W_REVIEW='MISSED: 2 — no test' \
        world "c-held-$RANDOM" internal real no done-not-pushed); run "$w" "$B" "$J" >/dev/null
    started "$w" && echo held=followed || echo held=waiting
    w=$(world "c-touch-$RANDOM" internal real yes);        run "$w" "$B" "$J" >/dev/null; landed "$w" && echo touch=landed || echo touch=held
    w=$(world "c-done-$RANDOM" internal real no pushed "SCOPE COMPLETE — nothing left."); run "$w" "$B" --after-land "$J" >/dev/null
    started "$w" && echo complete=started || echo complete=stopped
    w=$(W_STATUS=parked world "c-park-$RANDOM" internal real no); run "$w" "$B" --after-land "$J" >/dev/null
    started "$w" && echo parked=started || echo parked=stopped
    w=$(world "c-scr-$RANDOM" internal real scripts);      run "$w" "$B" "$J" >/dev/null; landed "$w" && echo scripts=landed || echo scripts=held
    w=$(W_NODOC=1 world "c-nodoc-$RANDOM" internal real no); run "$w" "$B" "$J" >/dev/null
    landed "$w" && echo nodoc=landed || echo nodoc=held
    w=$(W_REVIEW='PLAUSIBLE: src/a.ts:9 — maybe' world "c-note-$RANDOM" internal real no); run "$w" "$B" "$J" >/dev/null
    started "$w" && echo note=followed || { grep -qs maybe "$w/notes/proj.md" && echo note=recorded || echo note=lost; }
    w=$(world "c-red-$RANDOM" internal real no pushed "SCOPE COMPLETE — nothing left."); W_PROV=red run "$w" "$B" --after-land "$J" >/dev/null
    [ -s "$w/glance" ] && echo red=asked || echo red=not-asked
    w=$(W_PUBLISH=x world "c-down-$RANDOM" internal real no pushed "SCOPE COMPLETE — nothing left."); W_PROBE=false run "$w" "$B" --after-land "$J" >/dev/null
    [ -s "$w/glance" ] && echo down=asked || echo down=not-asked
}

echo "knight-next"
w=$(world land internal real no)
out=$(run "$w" "$NEXT" "$J")
landed "$w" && grep -q "$J" "$w/landed" && ok "an internal project with a real gate lands a green job by itself" \
    || bad "an internal project with a real gate lands a green job by itself" "$out"
grep -q '"outcome":"auto-landed"' "$w/lands.jsonl" && ok "and records that it did" || bad "and records that it did" "$(cat "$w/lands.jsonl" 2>/dev/null)"

w=$(world biz business real no); run "$w" "$NEXT" "$J" >/dev/null
! landed "$w" && grep -q 'not internal' "$w/lands.jsonl" && ok "a business project waits for \"merge it\", and says why" \
    || bad "a business project waits for \"merge it\", and says why" "$(cat "$w/lands.jsonl" 2>/dev/null)"
w=$(world first internal placeholder no); run "$w" "$NEXT" "$J" >/dev/null
! landed "$w" && grep -q 'placeholder' "$w/lands.jsonl" && ok "a first job that leaves the placeholder gate waits for Brad" \
    || bad "a first job that leaves the placeholder gate waits for Brad" "$(cat "$w/lands.jsonl" 2>/dev/null)"
w=$(world first2 internal placeholder realgate); run "$w" "$NEXT" "$J" >/dev/null
landed "$w" && ok "a first job that makes the checks real, reviewed clean, lands by itself" \
    || bad "a first job that makes the checks real, reviewed clean, lands by itself" "$(cat "$w/lands.jsonl" 2>/dev/null)"
w=$(world touch internal real yes); run "$w" "$NEXT" "$J" >/dev/null
! landed "$w" && grep -q 'gate.sh' "$w/lands.jsonl" && ok "a job that touches gate.sh does not land itself" \
    || bad "a job that touches gate.sh does not land itself" "$(cat "$w/lands.jsonl" 2>/dev/null)"
w=$(world failed internal real no done-not-pushed); run "$w" "$NEXT" "$J" >/dev/null
! landed "$w" && ok "a job that did not push lands nothing" || bad "a job that did not push lands nothing"
w=$(world off internal real no); sed -i 's/KT_AUTO_LAND=1/KT_AUTO_LAND=0/' "$w/targets/proj.env"; run "$w" "$NEXT" "$J" >/dev/null
! landed "$w" && ok "without KT_AUTO_LAND nothing lands by itself" || bad "without KT_AUTO_LAND nothing lands by itself"

w=$(world cont internal real no); run "$w" "$NEXT" --after-land "$J" >/dev/null
started "$w" && grep -qx 'proj' "$w/started" && grep -q 'Aggregate internships' "$w/started" \
    && ok "after a landing, the next job is briefed from the tracker scope, on the same target" \
    || bad "after a landing, the next job is briefed from the tracker scope, on the same target" "$(cat "$w/started" 2>/dev/null)"
cmp -s "$w/jobs/$J/notify.json" "$w/jobs/20991231-000001-2/notify.json" \
    && ok "and reports to the same conversation" || bad "and reports to the same conversation"
w=$(world done internal real no pushed "SCOPE COMPLETE — the scope is covered."); run "$w" "$NEXT" --after-land "$J" >/dev/null
! started "$w" && ok "SCOPE COMPLETE ends the run of jobs" || bad "SCOPE COMPLETE ends the run of jobs"
w=$(world cap internal real no); for i in 2 3 4 5 6; do mkdir -p "$w/jobs/$(date +%Y%m%d)-00000$i-$i"; printf proj > "$w/jobs/$(date +%Y%m%d)-00000$i-$i/target"; done
mkdir -p "$w/jobs/$(date +%Y%m%d)-000007-7"; printf proj > "$w/jobs/$(date +%Y%m%d)-000007-7/target"
run "$w" "$NEXT" --after-land "$J" >/dev/null
! started "$w" && grep -q 'chain-paused' "$w/lands.jsonl" && ok "six jobs on one project in a day pauses the run" \
    || bad "six jobs on one project in a day pauses the run" "$(cat "$w/lands.jsonl" 2>/dev/null)"
# A budget reset counts for this cap too: the same six jobs, all before the reset, no longer pause it.
w=$(world capreset internal real no); for i in 2 3 4 5 6 7; do mkdir -p "$w/jobs/$(date +%Y%m%d)-00000$i-$i"; printf proj > "$w/jobs/$(date +%Y%m%d)-00000$i-$i/target"; done
date +%Y%m%d-%H%M%S > "$w/.budget-epoch"
KNIGHT_HOME="$w" run "$w" "$NEXT" --after-land "$J" >/dev/null
started "$w" && ok "after a budget reset, the per-project cap starts counting again" \
    || bad "after a budget reset, the per-project cap starts counting again" "$(cat "$w/lands.jsonl" 2>/dev/null)"
w=$(world nocont internal real no); sed -i 's/KT_CONTINUE=1/KT_CONTINUE=0/' "$w/targets/proj.env"; run "$w" "$NEXT" --after-land "$J" >/dev/null
! started "$w" && ok "without KT_CONTINUE no next job starts" || bad "without KT_CONTINUE no next job starts"

# ── Review of 2026-10-17 ─────────────────────────────────────────────────────
w=$(W_STATUS=parked world parked internal real no); run "$w" "$NEXT" --after-land "$J" >/dev/null
! started "$w" && grep -q 'parked' "$w/lands.jsonl" && ok "a project Brad parks in the tracker stops building" \
    || bad "a project Brad parks in the tracker stops building" "$(cat "$w/lands.jsonl" 2>/dev/null)"
w=$(W_STATUS=parked world parked2 internal real no); run "$w" "$NEXT" "$J" >/dev/null
! landed "$w" && ok "and a parked project's job does not land itself" || bad "and a parked project's job does not land itself"
w=$(W_STAGE=shipped world shipped internal real no); run "$w" "$NEXT" --after-land "$J" >/dev/null
! started "$w" && ok "a project no longer at 'building' stops" || bad "a project no longer at 'building' stops"
w=$(world scripts internal real scripts); run "$w" "$NEXT" "$J" >/dev/null
! landed "$w" && grep -q 'package.json (scripts)' "$w/lands.jsonl" && ok "a job that rewrites package.json's test script does not land itself" \
    || bad "a job that rewrites package.json's test script does not land itself" "$(cat "$w/lands.jsonl" 2>/dev/null)"
w=$(world deps internal real deps); run "$w" "$NEXT" "$J" >/dev/null
landed "$w" && ok "adding a dependency to package.json still lands" || bad "adding a dependency to package.json still lands" "$(cat "$w/lands.jsonl" 2>/dev/null)"
w=$(world conf internal real conftest); run "$w" "$NEXT" "$J" >/dev/null
! landed "$w" && ok "a job that adds a conftest.py does not land itself" || bad "a job that adds a conftest.py does not land itself"
w=$(world fin internal real no done-not-pushed "*What shipped* — SCOPE COMPLETE: search, filters and the map are all in."); run "$w" "$NEXT" "$J" >/dev/null
grep -q '"outcome":"chain-complete"' "$w/lands.jsonl" && ok "a job that changes nothing and says SCOPE COMPLETE ends the run, and says so" \
    || bad "a job that changes nothing and says SCOPE COMPLETE ends the run, and says so" "$(cat "$w/lands.jsonl" 2>/dev/null)"
w=$(world notyet internal real no pushed "*What shipped* — the filters.
*Notes* — not SCOPE COMPLETE yet; the map is next."); run "$w" "$NEXT" --after-land "$J" >/dev/null
started "$w" && ok "SCOPE COMPLETE further down the report is prose, not the signal" || bad "SCOPE COMPLETE further down the report is prose, not the signal"
w=$(world busy internal real no); BUSY=2 run "$w" "$NEXT" --after-land "$J" >/dev/null
started "$w" && ok "a busy Knight is waited for, not given up on" || bad "a busy Knight is waited for, not given up on" "$(cat "$w/lands.jsonl" 2>/dev/null)"

# Brad named a place for it: the first landing publishes it there, and the run carries on.
w=$(W_PUBLISH=listings world pub internal real no); run "$w" "$NEXT" --after-land "$J" >/dev/null
grep -qx 'proj listings' "$w/published" 2>/dev/null && started "$w" \
    && ok "a landing publishes the project where Brad asked, then keeps building" \
    || bad "a landing publishes the project where Brad asked, then keeps building" "$(cat "$w/lands.jsonl" 2>/dev/null)"
w=$(world nopub internal real no); run "$w" "$NEXT" --after-land "$J" >/dev/null
[ ! -e "$w/published" ] && ok "with no place named, nothing is published" || bad "with no place named, nothing is published"

# ── Review of 2026-10-17: a gate that checks nothing, a review that settled nothing ──────────
w=$(world fake1 internal placeholder fakegate); run "$w" "$NEXT" "$J" >/dev/null
! landed "$w" && grep -q 'checks nothing' "$w/lands.jsonl" && ok "a first job whose gate.sh passes with the code deleted does not land itself" \
    || bad "a first job whose gate.sh passes with the code deleted does not land itself" "$(cat "$w/lands.jsonl" 2>/dev/null)"
git -C "$w/src" worktree list | grep -qc . && [ "$(git -C "$w/src" worktree list | wc -l)" = 1 ] \
    && ok "and the scratch checkout it used is gone" || bad "the gate check left a worktree behind" "$(git -C "$w/src" worktree list)"
w=$(W_REVIEW='UNCHECKED: 2 — how it looks on a phone' world unchk internal real no); run "$w" "$NEXT" "$J" >/dev/null
! landed "$w" && ! started "$w" && grep -q 'settled no criterion' "$w/lands.jsonl" \
    && ok "a review that settled nothing (only UNCHECKED) does not land itself, and is Brad's" \
    || bad "a review that settled nothing (only UNCHECKED) does not land itself, and is Brad's" "$(cat "$w/lands.jsonl" 2>/dev/null)"
w=$(W_REVIEW=$'UNCHECKED: 2 — how it looks on a phone\nSETTLED: 1 — the filter test' world unchk2 internal real no); run "$w" "$NEXT" "$J" >/dev/null
landed "$w" && ok "UNCHECKED beside a settled criterion still lands" || bad "UNCHECKED beside a settled criterion still lands"
w=$(W_SUMMARY=$'commits=2\npushed=no — review MISSED the request: x' W_REVIEW='MISSED: x' world direct internal real no done-not-pushed)
sed -i 's/KT_PUSH_MODE=branch/KT_PUSH_MODE=direct/' "$w/targets/proj.env"; run "$w" "$NEXT" "$J" >/dev/null
! started "$w" && grep -q 'fixup-skipped' "$w/lands.jsonl" && ok "a target that deploys itself is never followed up automatically" \
    || bad "a target that deploys itself is never followed up automatically" "$(cat "$w/lands.jsonl" 2>/dev/null)"
w=$(W_QUIET=1 W_SUMMARY=$'commits=0\npushed=no — nothing was committed, so there is nothing to push' world qstop internal real no done-not-pushed)
run "$w" "$NEXT" "$J" >/dev/null
grep -q '"outcome":"chain-stopped"' "$w/lands.jsonl" && ok "a quiet run that ends with nothing to follow up says it stopped" \
    || bad "a quiet run that ends with nothing to follow up says it stopped" "$(cat "$w/lands.jsonl" 2>/dev/null)"

# ── Holds become work (2026-10-17) ───────────────────────────────────────────
w=$(W_SUMMARY=$'commits=2\ngate=green\npushed=no — review CONFIRMED a defect: a.ts:3 — crashes on empty' W_REVIEW='CONFIRMED: a.ts:3 — crashes on empty' \
    world held internal real no done-not-pushed); run "$w" "$NEXT" "$J" >/dev/null
started "$w" && grep -qx -- '--base' "$w/started" && grep -qx 'knight/x' "$w/started" && grep -qx -- '--quiet' "$w/started" \
    && grep -q 'crashes on empty' "$w/started" && [ "$(cat "$w/jobs/20991231-000001-2/fixup-round" 2>/dev/null)" = 1 ] \
    && ok "a job the review held becomes a quiet follow-up on its own branch, told what held it" \
    || bad "a job the review held becomes a quiet follow-up on its own branch, told what held it" "$(cat "$w/started" 2>/dev/null | head -12)"
w=$(W_SUMMARY=$'commits=1\ngate=red: the gate failed at: ./gate.sh\npushed=no — the gate failed at: ./gate.sh' world red internal real no done-not-pushed)
printf 'not ok 3 - the filter keeps 2027\n' > "$w/jobs/$J/gate.log"; W_REVIEW='' run "$w" "$NEXT" "$J" >/dev/null
started "$w" && grep -q 'not ok 3' "$w/started" && ok "a red gate is followed up with the failing output" \
    || bad "a red gate is followed up with the failing output" "$(cat "$w/started" 2>/dev/null | head -8)"
w=$(W_SUMMARY=$'commits=2\npushed=no — review MISSED the request: x' W_REVIEW='MISSED: x' W_ROUND=2 world tired internal real no done-not-pushed)
run "$w" "$NEXT" "$J" >/dev/null
! started "$w" && grep -q 'fixup-exhausted' "$w/lands.jsonl" && ok "after two follow-ups it is Brad's, not a third job" \
    || bad "after two follow-ups it is Brad's, not a third job" "$(cat "$w/lands.jsonl" 2>/dev/null)"
w=$(W_REVIEW='PLAUSIBLE: src/a.ts:9 — dedupe may merge two cities' world note internal real no); run "$w" "$NEXT" "$J" >/dev/null
landed "$w" && ! started "$w" && ok "a review NOTE neither holds a self-landing job nor starts a follow-up: it lands (#4)" \
    || bad "a review NOTE neither holds a self-landing job nor starts a follow-up: it lands (#4)" "$(cat "$w/lands.jsonl" 2>/dev/null)"
grep -q 'two cities' "$w/notes/proj.md" 2>/dev/null && grep -q '"outcome":"review-notes"' "$w/lands.jsonl" \
    && ok "and the note is written in the project's notes ledger" || bad "and the note is written in the project's notes ledger" "$(ls "$w")"
run "$w" "$NEXT" --after-land "$J" >/dev/null
grep -q 'two cities' "$w/started" 2>/dev/null && grep -q 'REVIEW NOTES FROM EARLIER JOBS' "$w/started" \
    && ok "and the next piece is handed it as context, not as a requirement" \
    || bad "and the next piece is handed it as context, not as a requirement" "$(head -c 600 "$w/started" 2>/dev/null)"
w=$(W_SUMMARY=$'commits=2\npushed=no — review MISSED the request: x' W_REVIEW=$'MISSED: x is not filtered\nPLAUSIBLE: maybe rename y' world held2 internal real no done-not-pushed)
run "$w" "$NEXT" "$J" >/dev/null
grep -q 'x is not filtered' "$w/started" 2>/dev/null && ! grep -q 'rename y' "$w/started" \
    && ok "a held job's follow-up is told what held it, not the notes beside it" \
    || bad "a held job's follow-up is told what held it, not the notes beside it" "$(head -c 600 "$w/started" 2>/dev/null)"
w=$(W_SUMMARY=$'commits=0\npushed=no — nothing was committed, so there is nothing to push' world empty internal real no done-not-pushed)
run "$w" "$NEXT" "$J" >/dev/null
! started "$w" && ok "a job that committed nothing is not followed up" || bad "a job that committed nothing is not followed up"
w=$(W_SUMMARY=$'commits=2\npushed=no — review MISSED the request: x' W_REVIEW='MISSED: x' world biz2 business real no done-not-pushed)
run "$w" "$NEXT" "$J" >/dev/null
started "$w" && ok "follow-ups apply to every project, business ones too (landing is what stays Brad's)" \
    || bad "follow-ups apply to every project, business ones too (landing is what stays Brad's)"

# A CLEAR "DONE" (#5): the scope covered, the checks green on main, live where it has an address — and
# only then is Brad asked for his look.
w=$(W_PUBLISH=listings world finished internal real no pushed "SCOPE COMPLETE — nothing left."); run "$w" "$NEXT" --after-land "$J" >/dev/null
grep -q proj "$w/provisioned" 2>/dev/null && grep -q 'request proj https://example.com/listings/' "$w/glance" 2>/dev/null \
    && grep -q '"outcome":"done-check-green"' "$w/lands.jsonl" \
    && ok "a covered scope with green checks on main and a live page asks for Brad's look, and records that it did" \
    || bad "a covered scope with green checks on main and a live page asks for Brad's look, and records that it did" "$(cat "$w/lands.jsonl" "$w/glance" 2>&1)"
grep -q "Waiting for Brad's look at https://example.com/listings/" "$w/projects.json" \
    && ok "and the tracker says it is waiting on his look" || bad "and the tracker says it is waiting on his look" "$(grep -o '"next": "[^"]*"' "$w/projects.json")"
w=$(world done5red internal real no pushed "SCOPE COMPLETE — nothing left."); W_PROV=red run "$w" "$NEXT" --after-land "$J" >/dev/null
[ ! -s "$w/glance" ] && grep -q '"outcome":"done-check-red".*not ok 3' "$w/lands.jsonl" \
    && ok "with the checks red on main it is NOT done, says which check, and does not ask for a look" \
    || bad "with the checks red on main it is NOT done, says which check, and does not ask for a look" "$(cat "$w/lands.jsonl" 2>&1)"
w=$(world done5busy internal real no pushed "SCOPE COMPLETE — nothing left."); PROV_BUSY=2 run "$w" "$NEXT" --after-land "$J" >/dev/null
[ "$(wc -l < "$w/provisioned" 2>/dev/null)" = 3 ] && [ -s "$w/glance" ] \
    && ok "the done check waits for a job holding the clone, then runs" || bad "the done check waits for a job holding the clone, then runs" "$(cat "$w/provisioned" "$w/lands.jsonl" 2>&1)"
w=$(W_SUMMARY=$'commits=2\npushed=no — review MISSED the request: x' W_REVIEW=$'MISSED: x\nPLAUSIBLE: maybe cache the board' world heldnote internal real no done-not-pushed)
run "$w" "$NEXT" "$J" >/dev/null
grep -q 'cache the board' "$w/notes/proj.md" 2>/dev/null && ok "a held job's notes are written down too" || bad "a held job's notes are written down too" "$(ls "$w")"
w=$(W_REVIEW='PLAUSIBLE: maybe y' world bizNote business real no); run "$w" "$NEXT" "$J" >/dev/null
grep -q 'maybe y' "$w/notes/proj.md" 2>/dev/null && ok "and so are the notes on a project that does not land itself" || bad "and so are the notes on a project that does not land itself"
w=$(W_PUBLISH=x world done5down internal real no pushed "SCOPE COMPLETE — nothing left."); W_PROBE=false run "$w" "$NEXT" --after-land "$J" >/dev/null
[ ! -s "$w/glance" ] && grep -q '"outcome":"done-check-down"' "$w/lands.jsonl" \
    && ok "with the live page not answering it is NOT done" || bad "with the live page not answering it is NOT done" "$(cat "$w/lands.jsonl" 2>&1)"

# EVERY PROJECT CARRIES ITS ARCHITECTURE (commandment V, 2026-10-22).
w=$(W_NODOC=1 world nodoc internal real no); run "$w" "$NEXT" "$J" >/dev/null
! landed "$w" && started "$w" && grep -q 'no docs/ARCHITECTURE.md' "$w/started" && grep -q 'every caller of every address' "$w/started" \
    && ok "a job on a project with no architecture doc does not land itself: a follow-up writes the doc" \
    || bad "a job on a project with no architecture doc does not land itself: a follow-up writes the doc" "$(cat "$w/lands.jsonl" 2>/dev/null)"

# Watch each hold fail: a copy of knight-next with it removed must do the wrong thing.
MUT="$TMP/mut"; mkdir -p "$MUT/bin"
watch_fail() {  # watch_fail <label> <case=outcome it must produce> <sed expression>
    cp "$NEXT" "$MUT/bin/knight-next"; sed -i "$3" "$MUT/bin/knight-next"
    mkdir -p "$MUT/agent/lib"; cp -r "$HERE/../agent/lib/." "$MUT/agent/lib/" 2>/dev/null
    cmp -s "$NEXT" "$MUT/bin/knight-next" && { bad "$1" "the sabotage no longer applies"; return; }
    grep -qx "$2" <<<"$(cases "$MUT/bin/knight-next")" && ok "watched fail: $1" || bad "watched fail: $1" "the suite did not see it"
}
watch_fail "with notes followed up again, a note starts a job" note=followed 's/^    record_notes$/    fixup "notes"; exit 0/'
watch_fail "with the ledger write removed, a note is lost" note=lost 's/>> "\$NOTES_DIR\/\$TARGET.md"/> \/dev\/null/'
watch_fail "with the checks on main not trusted, a red project is called done" red=asked 's/^    if \[ "$rc" -ne 0 \]; then$/    if false; then/'
watch_fail "with the live address not probed, a dead page is called done" down=asked 's/if \[ -n "$url" \] \&\& ! $LIVE_PROBE "$url"/if false/'
watch_fail "with the architecture check removed, an undocumented project lands by itself" nodoc=landed 's/   \&\& ! git -C "$KT_SOURCE" cat-file -e "$BRANCH:agent\/docs\/ARCHITECTURE.md" 2>\/dev\/null; then/   \&\& false; then/'
watch_fail "with the kind check removed, a business lands by itself" business=landed 's/if \[ "\$kind" != internal \]; then/if false; then/'
# Two barriers: the placeholder text, and the gate failing with the code deleted. Both go.
watch_fail "with the placeholder check removed, a first job that leaves it lands" placeholder=landed 's/|| git -C "\$KT_SOURCE" show "\$BRANCH:gate.sh" 2>\/dev\/null | grep -qF "\$PLACEHOLDER"; then/; then/; s/elif ! gate_is_real; then/elif false; then/'
watch_fail "with the gate not tested by deleting the code, a gate that checks nothing lands" fakegate=landed 's/elif ! gate_is_real; then/elif false; then/'
watch_fail "with a settle-nothing review allowed, an all-UNCHECKED job lands itself" unchecked=landed "s/&& ! grep -qE '^(SETTLED|CLEAN)\\\\b'/\&\& false \&\& ! grep -qE '^(SETTLED|CLEAN)\\\\b'/"
watch_fail "with holds not followed up, a held job just waits" held=waiting 's/^            fixup "\${heldwhy:-\$gatered}"$/            :/'
watch_fail "with the gate.sh check removed, a job weakening its gate lands by itself" touch=landed 's/\[ -z "\$touched" \] || why=/: || why=/'
watch_fail "with SCOPE COMPLETE ignored, the run never ends" complete=started "s/grep -q 'SCOPE COMPLETE'; }/false; }/"
watch_fail "with the tracker not read, a parked project keeps building" parked=started 's/^stop=\$(still_building)$/stop=""/'
watch_fail "with only gate.sh watched, a rewritten test script lands itself" scripts=landed 's/touched=\$(gate_files_touched) || /touched=""; true || /' 

printf '\n%d ok, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
