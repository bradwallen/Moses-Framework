#!/usr/bin/env bash
# test-diagnose.sh — the stalled-job diagnosis must tell a re-run from a real human merge.
#
#   /home/brad/Projects/moses/knight/test-diagnose.sh        (free: no model calls, no network, seconds)
#
# Builds throwaway repositories for the three shapes that matter, including the exact one that cost
# four days on 2026-09-27: a branch carrying a replayed commit the mainline already had. Each case
# asserts the VERDICT, because the verdict is what Brad would be asked to act on.
#
#   --prove-red   run against a sabotaged copy (merge test disabled) and require at least one failure
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
LIB="$HERE/lib/diagnose-branch.sh"
if [ "${1:-}" = "--prove-red" ]; then
    LIB=$(mktemp); sed 's/conflicts=\$?/conflicts=0/' "$HERE/lib/diagnose-branch.sh" > "$LIB"
fi
# shellcheck source=lib/diagnose-branch.sh
source "$LIB"

T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0
ok()  { echo "  PASS  $1"; }
bad() { echo "  FAIL  $1"; echo "        got: $2"; fails=$((fails+1)); }
g()   { git -C "$T/r" -c user.email=t@t -c user.name=t "$@" >/dev/null 2>&1; }

new_repo() {
    rm -rf "$T/r"; git init -q -b main "$T/r"
    printf 'a\nb\nc\n' > "$T/r/shared.txt"; g add -A; g commit -qm base
}

echo "diagnose-branch"

# 1. The 09-27 shape: the mainline moved a lot, the branch adds a little, nothing truly conflicts.
new_repo
g checkout -qb work; echo fix > "$T/r/clip.ts"; g add -A; g commit -qm "the fix"
g checkout -q main
for i in 1 2 3 4 5; do echo "$i" > "$T/r/m$i.txt"; g add -A; g commit -qm "mainline $i"; done
g checkout -q work
out=$(diagnose_branch "$T/r" "$(git -C "$T/r" rev-parse main)")
case "$out" in *"adds 1 commit"*"moved 5"*"merges CLEANLY"*"not a human merge"*) ok "behind but clean is a re-run, not a human merge" ;;
               *) bad "behind but clean is a re-run, not a human merge" "$out" ;; esac

# 2. A replayed "prerequisite" the mainline already has — what the agent did on 2026-09-27.
new_repo
echo pre > "$T/r/pre.txt"; g add -A; g commit -qm "prerequisite"
pre=$(git -C "$T/r" rev-parse HEAD)
# -x: a REAL replay has a new hash, as the agent's did. Without it, a same-second cherry-pick can
# reproduce the original commit byte for byte and the branch would not carry a copy at all.
g checkout -qb work HEAD~1; g cherry-pick -x "$pre"
echo fix > "$T/r/clip.ts"; g add -A; g commit -qm "the fix"
out=$(diagnose_branch "$T/r" "$(git -C "$T/r" rev-parse main)")
case "$out" in *"adds 2 commit"*"1 of them ALREADY on the mainline"*"merges CLEANLY"*) ok "names a replayed commit the mainline already has" ;;
               *) bad "names a replayed commit the mainline already has" "$out" ;; esac

# 3. A genuine conflict must still be called one, and name the file.
new_repo
g checkout -qb work; printf 'a\nWORK\nc\n' > "$T/r/shared.txt"; g commit -qam "work edits b"
g checkout -q main;  printf 'a\nMAIN\nc\n' > "$T/r/shared.txt"; g commit -qam "main edits b"
g checkout -q work
out=$(diagnose_branch "$T/r" "$(git -C "$T/r" rev-parse main)")
case "$out" in *"REAL conflict in: shared.txt"*"genuine human merge"*) ok "a real conflict is still a human merge, with the file named" ;;
               *) bad "a real conflict is still a human merge, with the file named" "$out" ;; esac

# 4. A branch already fully landed has nothing to do.
new_repo
out=$(diagnose_branch "$T/r" "$(git -C "$T/r" rev-parse HEAD)")
case "$out" in *"adds 0 commit"*"nothing to land"*) ok "an already-landed branch says there is nothing to land" ;;
               *) bad "an already-landed branch says there is nothing to land" "$out" ;; esac

# 5. It must say when it could not look, rather than produce a verdict.
out=$(diagnose_branch "$T/r" "0000000000000000000000000000000000000000")
case "$out" in "could not diagnose"*) ok "an unknown mainline is reported as could-not-diagnose" ;;
               *) bad "an unknown mainline is reported as could-not-diagnose" "$out" ;; esac

if [ "${1:-}" = "--prove-red" ]; then
    rm -f "$LIB"
    [ "$fails" -gt 0 ] && { echo "  red as required: the sabotaged copy failed $fails check(s)"; exit 0; }
    echo "  NOT RED: the sabotage went unnoticed — this test proves nothing"; exit 1
fi
echo; [ "$fails" -eq 0 ] && echo "  all good" || echo "  $fails failed"
exit $(( fails > 0 ))
