#!/usr/bin/env bash
# pre-push.sh — the last thing that runs before this repository becomes public, again.
#
#   ln -sf ../../tools/pre-push.sh .git/hooks/pre-push     (bootstrap.sh does this)
#   tools/pre-push.sh --selftest                           prove it still refuses what it must
#
# WHY THIS EXISTS. Publishing used to be a human step: the operator ran `git push` by hand, and that
# pause was the only thing standing between a mistake and the internet. Handing an agent the ability
# to push removes the pause, so the pause has to be replaced by something mechanical — a rule in a
# settings file grants the ability, and this decides whether any particular push is allowed to happen.
# Prose is the weakest layer (commandment IX); this is not prose.
#
# WHAT IT REFUSES, and why each one is here rather than in somebody's head:
#
#   1. The archived history. `archive/pre-public-2026-*` holds the pre-public commits this repository
#      deliberately left behind. Pushing it would undo the entire reason the repo was deleted and
#      recreated instead of force-pushed.
#   2. A rewrite. If the remote's commit is not an ancestor of what is being pushed, this push
#      replaces published history — a decision a person makes deliberately, never a step in a script.
#   3. Anything that is not the checked-out HEAD. The secret scan below reads the WORKING TREE, so it
#      is only evidence about the commit that tree represents. Pushing some other ref would mean
#      publishing something nobody scanned.
#   4. A tree the secret scanner rejects — and a scanner that cannot pass its own self-test, because
#      a scanner nobody has watched catch something is a decoration (commandment VIII).
#
# HONEST LIMITS, stated rather than discovered later:
#   · It reads the TREE, not every commit in the range. A secret added and later removed is still in
#     the history being pushed and this will not see it. The pre-commit scanner is the layer that
#     stops that, and GitHub's own push protection is the backstop beyond both.
#   · `--no-verify` skips this hook entirely. Nothing local can prevent that. It is refused by policy,
#     not by code, and the only real backstop past it is the server's.
set -uo pipefail

REPO=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$REPO" || exit 0

ARCHIVE_TAG_RE='^refs/tags/archive/'

refuse() {
    echo >&2
    echo "  PUSH REFUSED — $1" >&2
    shift
    for line in "$@"; do echo "    $line" >&2; done
    echo >&2
    exit 1
}

# Each line: <local ref> <local sha> <remote ref> <remote sha>. A deletion has an all-zero local sha.
check_ref() {
    local lref="$1" lsha="$2" rref="$3" rsha="$4"
    local zero="0000000000000000000000000000000000000000"

    if [[ "$rref" =~ $ARCHIVE_TAG_RE || "$lref" =~ $ARCHIVE_TAG_RE ]]; then
        refuse "that is the archived pre-public history ($lref)." \
               "It was left behind on purpose when this repository was recreated." \
               "If it genuinely has to be published, push it by hand and mean it."
    fi

    [ "$lsha" = "$zero" ] && return 0          # deleting a remote ref: nothing of ours is published

    if [ "$rsha" != "$zero" ] && ! git merge-base --is-ancestor "$rsha" "$lsha" 2>/dev/null; then
        refuse "this rewrites published history on $rref." \
               "The remote's commit ${rsha:0:7} is not an ancestor of ${lsha:0:7}." \
               "Rewriting something the public has already seen is a person's decision."
    fi

    local head
    head=$(git rev-parse HEAD 2>/dev/null)
    if [ "$lsha" != "$head" ]; then
        refuse "only the checked-out commit may be pushed." \
               "asked to push ${lsha:0:7} ($lref) while HEAD is ${head:0:7}." \
               "The secret scan reads the working tree, so any other ref goes out unscanned."
    fi
}

scan() {
    [ -x tools/scan-secrets.sh ] || refuse "the secret scanner is missing or not executable." \
        "tools/scan-secrets.sh is what makes this repository safe to publish."
    tools/scan-secrets.sh --selftest >/dev/null 2>&1 || refuse \
        "the secret scanner cannot pass its own self-test." \
        "Until it can prove it still catches a real token, its silence means nothing."
    tools/scan-secrets.sh --all || refuse "the scanner found something credential-shaped." \
        "It is printed above. Nothing has been pushed."
}

if [ "${1:-}" = "--selftest" ]; then
    # Feed the hook the exact stdin git feeds it, because testing the LOGIC would prove nothing about
    # the seam (commandment VIII). Each case must be refused; the last must be allowed.
    here=$(git rev-parse HEAD)
    root=$(git rev-list --max-parents=0 HEAD | tail -1)
    zero="0000000000000000000000000000000000000000"
    pass=0
    try() {  # try "<label>" "<stdin>" <want-exit>
        local out rc
        out=$(printf '%s\n' "$2" | "$0" origin git@example.com:x/y.git 2>&1); rc=$?
        local verdict; [ "$3" = 1 ] && verdict="refused" || verdict="allowed"
        if [ "$rc" = "$3" ]; then printf "  %-8s %s\n" "$verdict" "$1"
        else printf "  WRONG    %s — expected %s, exit %s\n" "$1" "$verdict" "$rc"; pass=1; fi
    }
    echo "── pre-push self-test ──────────────────────────────────"
    try "the archived history"      "refs/tags/archive/x $here refs/tags/archive/x $zero" 1
    try "a rewrite of what is published" "refs/heads/main $root refs/heads/main $here"     1
    try "a ref that is not HEAD"    "refs/heads/main $root refs/heads/main $zero"          1
    try "an ordinary push of HEAD"  "refs/heads/main $here refs/heads/main $zero"          0
    echo
    [ "$pass" = 0 ] && echo "  self-test OK — it refuses the three, and lets an honest push through" \
                    || echo "  SELF-TEST FAILED" >&2
    exit "$pass"
fi

saw=0
while read -r lref lsha rref rsha; do
    [ -n "${lref:-}" ] || continue
    saw=1
    check_ref "$lref" "$lsha" "$rref" "$rsha"
done

[ "$saw" = 1 ] || exit 0                        # nothing to push; nothing to check
scan
echo "  pre-push: history intact, scanner clean — publishing."
exit 0
