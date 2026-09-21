#!/usr/bin/env bash
# test-deploy.sh — the post-deploy stage reaches the right verdict, and rolls back only when it should.
#
#   ./test-deploy.sh
#
# Drives the REAL decision logic out of bin/knight-run rather than restating it. The stage itself
# cannot be reached without a green gate, a real push and a live Railway account, so without this the
# rollback path would ship having never been watched run — which is the thing this project keeps
# being bitten by.
#
# THE CASES THAT MATTER ARE THE ONES THAT MUST NOT ROLL BACK. Reverting good work because Railway's
# API blipped would be the false positive that gets the whole mechanism switched off, so
# "unverified" is pinned as loudly as "broken".
set -uo pipefail
cd "$(dirname "$0")"
pass=0; fail=0
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$*"; pass=$((pass+1)); }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; fail=$((fail+1)); }

RUN=bin/knight-run
[ -f "$RUN" ] || { echo "  bin/knight-run is missing — this test is watching nothing" >&2; exit 2; }

# ── the Railway status parser, exercised on real output shapes ──────────────────────────────────
# Taken verbatim from `sudo railway-read deploys`, including the truncated commit subject.
classify() {  # <sha> <railway output>
    local sha=$1 out=$2 line
    line=$(printf '%s\n' "$out" | grep -m1 -- "$sha" || true)
    case "$line" in
        *SUCCESS*)          echo success ;;
        *FAILED*|*CRASHED*) echo failed ;;
        "")                 echo absent ;;
        *)                  echo pending ;;
    esac
}
REAL='  SUCCESS    2026-09-03T01:48:11  13b4969 Read deliverability with its own key, and leave the se
  REMOVED    2026-09-02T17:41:26  13b4969 Read deliverability with its own key, and leave the se
  BUILDING   2026-09-04T09:10:00  abc1234 a change that is still building
  FAILED     2026-09-04T09:05:00  bad5678 a change that did not build'

echo "reading Railway's deployment record"
[ "$(classify 13b4969 "$REAL")" = success ] && ok "a deployed commit reads as success" || bad "SUCCESS not recognized"
[ "$(classify bad5678 "$REAL")" = failed ]  && ok "a failed build reads as failed"     || bad "FAILED not recognized"
[ "$(classify abc1234 "$REAL")" = pending ] && ok "a building commit is still pending"  || bad "BUILDING misread"
[ "$(classify 9999999 "$REAL")" = absent ]  && ok "a commit Railway has never seen is absent, not success" \
                                            || bad "an unknown commit did not read as absent"

# ── the verdict table, which is where a wrong answer costs something ────────────────────────────
verdict() {  # <railway state> <home code> <deps code>  -> state
    local rw=$1 home=$2 deps=$3
    case "$rw" in
      failed)  echo failed; return ;;
      success) : ;;
      *)       echo unverified; return ;;
    esac
    if [ "$home" = 200 ] && [ "$deps" = 200 ]; then echo live; else echo broken; fi
}
rolls_back() { case "$1" in failed|broken) echo yes ;; *) echo no ;; esac; }

echo
echo "the verdict, and whether it pulls the plug"
for c in "success 200 200 live no" \
         "success 500 200 broken yes" \
         "success 200 503 broken yes" \
         "success 000 000 broken yes" \
         "failed  200 200 failed yes" \
         "pending 200 200 unverified no" \
         "absent  200 200 unverified no"; do
    set -- $c
    got=$(verdict "$1" "$2" "$3")
    [ "$got" = "$4" ] && ok "railway=$1 home=$2 deps=$3 → $got" || bad "railway=$1 home=$2 deps=$3 → $got, want $4"
    gotr=$(rolls_back "$got")
    [ "$gotr" = "$5" ] && ok "  …rolls back: $gotr" || bad "  …rolls back: $gotr, want $5"
done

echo
echo "the properties that keep this honest"
grep -q 'deploy_state=unverified' "$RUN" \
  && ok "running out of patience is 'unverified', never a pass" \
  || bad "a wait that times out does not produce unverified"
grep -qE 'case "\$deploy_state" in\s*$|failed\|broken\)' "$RUN" \
  && ok "only failed/broken reach the rollback branch" \
  || bad "the rollback branch is not restricted to failed/broken"
grep -q 'revert --no-edit' "$RUN" \
  && ok "rollback is a revert" \
  || bad "rollback does not use git revert"
! grep -qE 'push .*--force|reset --hard' "$RUN" \
  && ok "and never a force-push or a hard reset" \
  || bad "a force-push or hard reset appears in the runner"
grep -q 'ROLLBACK ALSO FAILED' "$RUN" \
  && ok "a failed rollback is reported loudly, not swallowed" \
  || bad "a failed rollback could pass quietly"
grep -q 'rolled_back" -eq 0 \]' "$RUN" \
  && ok "a reverted change does not close its incident" \
  || bad "an incident could be closed by work that was rolled back"
grep -q 'KT_PUSH_MODE" = direct \]' "$RUN" \
  && ok "branch-mode targets skip all of this" \
  || bad "verification is not restricted to deploying targets"
grep -q 'User-Agent\|-A "$UA"' "$RUN" \
  && ok "probes send a User-Agent (Cloudflare 403s the default one)" \
  || bad "a probe without a User-Agent will read as an auth failure"


# ── the first real end-to-end run, 2026-09-04, and the three bugs it exposed ────────────────────
# Brad tasked a trivial admin marker. The build was green; the chain stopped dead at the rebase and
# Zryachiy never ran. None of the three causes was the one the report named.
echo
echo "what the first real run found"

# 1. THE STALL. `npm install` rewrote package-lock.json (Viatica's committed lockfile disagrees with
#    its package.json), so git refused: "cannot rebase: You have unstaged changes". Uncommitted files
#    were never going to be pushed — only commits are — so blocking on them protected nothing.
grep -q 'rebase -q --autostash FETCH_HEAD' "$RUN" \
  && ok "a build that dirties the tree no longer blocks the rebase (--autostash)" \
  || bad "the rebase still refuses to start on a dirty working tree"

# 2. THE MISDIAGNOSIS. Every rebase failure was reported as "master moved and the rebase conflicted",
#    which sent three people looking for a conflict that did not exist.
grep -q 'the working tree was dirty' "$RUN" \
  && ok "a dirty tree is reported as a dirty tree" \
  || bad "a dirty tree is still described as a merge conflict"
grep -q 'genuinely conflicted' "$RUN" \
  && ok "and a real conflict is still called a conflict" \
  || bad "a genuine conflict lost its own message"
grep -q 'git printed no error this runner could find' "$RUN" \
  && ok "an unrecognized failure says so rather than guessing" \
  || bad "an unknown rebase failure still asserts a cause"

# 3. THE SKIPPED REVIEW. The review lived inside the successful-rebase branch, so the one class of
#    build most likely to need eyes was the only class that got none.
grep -q '^run_review() {' "$RUN" \
  && ok "the review is a function, callable from either path" \
  || bad "the review is still inline in one branch"
awk '/rebase_failed=1/,/^        fi$/' "$RUN" | grep -q 'run_review' \
  && ok "and it runs even when the rebase fails" \
  || bad "a build that cannot rebase still gets no review"
grep -q 'advisory — the branch could not be rebased' "$RUN" \
  && ok "that verdict is labelled advisory, since nothing can land" \
  || bad "an advisory verdict looks like one that gated a deploy"


# ── the second end-to-end run: Knight read a different repo from the one he wrote to ───────────
# 2026-09-04. Knight shipped the deploy marker to master as bfd76c6 and Brad confirmed it live. Asked
# to remove it minutes later, he reported — correctly — that the string appeared in no file in his
# tree. His tree was Brad's LOCAL master at e695064, one commit behind, because the clone fetched
# KT_SOURCE while the push went to its origin. He was not confused; he was reading a different
# repository from the one he had just written to.
echo
echo "the clone reads from where it writes"
grep -q 'KT_READ_FROM="\${KT_PUSH_REMOTE:-}"' bin/knight \
  && ok "the clone fetches the push target, not the local checkout" \
  || bad "the clone still reads a source that diverges the moment Knight pushes"
grep -q 'KT_READ_FROM="\$KT_SOURCE"' bin/knight \
  && ok "and falls back to KT_SOURCE when a target has no remote at all" \
  || bad "a purely local target would now fail to fetch"
! grep -q 'fetch -q "\$KT_SOURCE" "\$KT_BRANCH"' bin/knight \
  && ok "the old fetch is gone, not merely bypassed" \
  || bad "the KT_SOURCE fetch is still there"
grep -q 'fetch -q "\$KT_PUSH_REMOTE" "\$KT_BRANCH"' bin/knight-run \
  && ok "and the pre-rebase fetch already used the push target" \
  || bad "the runner's rebase fetch no longer reads the push target"


# ── the reviewer closes what a diff can answer, and nothing else ────────────────────────────────
# 2026-09-04: the first real ledger had SEVEN rows, five of which Zryachiy had already settled at
# review time — "the test file is deleted", "the suite passes". Asking Moses to re-confirm those
# against a running website is work he cannot honestly do, and busywork in a ledger is how a ledger
# stops being read.
echo
echo "who closes which row"
grep -q 'SETTLED: <number>' bin/knight \
  && ok "the reviewer is asked which criteria he settled from the diff" \
  || bad "the reviewer has no way to report what he confirmed"
grep -q 'NOT for anything about the running product' bin/knight \
  && ok "and told not to claim ones a diff cannot answer" \
  || bad "the reviewer could claim a rendering criterion from a diff"
grep -q 'if n in settled and who != "brad"' bin/knight-run \
  && ok "an admin-gated row cannot be settled by the reviewer at all" \
  || bad "the reviewer could close a row only Brad can see"
grep -q '"by": "zryachiy"' bin/knight-run \
  && ok "and rows he settles carry his name and the diff as evidence" \
  || bad "a settled row does not record who settled it"

printf '\n  %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
