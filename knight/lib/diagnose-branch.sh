#!/usr/bin/env bash
# diagnose-branch.sh — when a Knight job stalls, say what is actually true before anyone asks Brad.
#
#   source knight/lib/diagnose-branch.sh
#   diagnose_branch <repo> <mainline-sha>       one line, measured against the REAL mainline
#
# WHY THIS EXISTS (2026-10-01). A two-commit fix — "Copy link" said Copied and copied nothing — sat
# unlanded for four days across five blocked runs. Every agent in the chain was told "master is 55
# commits behind" and believed it; the runner said "the rebase genuinely conflicted — needs a human
# merge"; Moses asked Brad to hand-merge from his phone and to triage 52 commits that were already
# on master. One measurement against the real mainline settled it: the branch added two commits and
# they applied with no conflict. Nobody in the chain took that measurement, because nothing made
# them. Retrying with better wording is not diagnosis.
#
# So the runner takes it, every time a rebase fails, deterministically and for free — no model, no
# tokens — and the report carries a verdict instead of a guess. Three questions, each answered by
# git rather than inferred:
#
#   1. How many commits does this branch really add to the mainline?
#   2. Are some of them ALREADY on the mainline under another hash? (`git cherry` — an agent that
#      "lands a prerequisite" by replaying an old commit produces exactly this, and a replayed commit
#      is what makes a rebase conflict when the end state would not.)
#   3. Would the END STATE merge cleanly? (`git merge-tree`, which touches neither the worktree nor
#      any ref.) A rebase replays commit by commit and can conflict where the finished change does
#      not — and that distinction decides whether this is a re-run or a real human merge.
#
# It reads only. It never checks out, never writes a ref, never touches the working tree — so it is
# safe to call from inside a failure path that has just aborted a rebase.

diagnose_branch() {
    local repo="$1" main="$2"
    local head adds already moved base conflicts files

    head=$(git -C "$repo" rev-parse --verify -q HEAD) || { echo "could not diagnose: no HEAD in $repo"; return 0; }
    git -C "$repo" cat-file -e "$main^{commit}" 2>/dev/null || { echo "could not diagnose: mainline $main is not in $repo"; return 0; }

    adds=$(git -C "$repo" rev-list --count "$main..$head")
    # `git cherry` prints "-" for a commit whose patch is already upstream under another hash.
    already=$(git -C "$repo" cherry "$main" "$head" 2>/dev/null | grep -c '^-' || true)
    base=$(git -C "$repo" merge-base "$main" "$head" 2>/dev/null)
    moved=$( [ -n "$base" ] && git -C "$repo" rev-list --count "$base..$main" || echo "?")

    local summary="against the real mainline this branch adds $adds commit(s)"
    [ "$already" -gt 0 ] && summary="$summary, $already of them ALREADY on the mainline as an equivalent patch"
    summary="$summary; the mainline moved $moved since this branch started"

    if [ "$adds" -eq 0 ]; then
        echo "$summary — there is nothing to land."
        return 0
    fi

    # Exit 0 = clean, 1 = conflicts; with --name-only the conflicted paths follow the tree id.
    files=$(git -C "$repo" merge-tree --write-tree --name-only --no-messages "$main" "$head" 2>/dev/null)
    conflicts=$?
    if [ "$conflicts" -eq 0 ]; then
        if [ "$already" -gt 0 ]; then
            echo "$summary. The finished change merges CLEANLY — the conflict came from replaying commits the mainline already has. Drop those and re-run; this is not a human merge."
        else
            echo "$summary. The finished change merges CLEANLY against the real mainline — re-run the job; this is not a human merge."
        fi
    elif [ "$conflicts" -eq 1 ]; then
        files=$(printf '%s\n' "$files" | sed -n '2,$p' | sed '/^$/,$d' | tr '\n' ' ' | sed 's/ $//')
        echo "$summary. A REAL conflict in: ${files:-(paths not reported)} — that is a genuine human merge, and these are the files."
    else
        echo "$summary. Could not test whether it merges (git merge-tree exited $conflicts) — treat as unknown, not as clean."
    fi
}
