#!/usr/bin/env bash
# moses_pull_test.sh — MF8: moses-pull takes Brad's main live, and fails safe (commandment 8).
# A scratch "from-brad" repo, a scratch live tree, and a fake systemctl: nothing real restarts or posts.
# Each guard is then removed from a copy of the script and the case it protects must fail.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
PASS=0; FAIL=0
t() { if [ "$2" = 0 ]; then echo "  PASS  $1"; PASS=$((PASS+1)); else echo "  FAIL  $1"; FAIL=$((FAIL+1)); fi; }

setup() {   # a fresh world in $W; prints nothing
    W=$(mktemp -d); export HOME=$W/home; mkdir -p "$HOME" "$W/bin" "$W/state"
    git init -q --bare "$W/hub.git"
    git clone -q "$W/hub.git" "$W/brad" 2>/dev/null
    git -C "$W/brad" -c user.email=b@x -c user.name=b commit -q --allow-empty -m one
    git -C "$W/brad" push -q origin HEAD:main 2>/dev/null
    git clone -q -b main "$W/hub.git" "$W/live" 2>/dev/null
    # fake systemctl: a unit is "down" while the live tree contains a file named BREAK
    cat > "$W/bin/systemctl" <<EOF
#!/bin/sh
case "\$*" in *is-active*) [ ! -e "$W/live/BREAK" ] ;; *) echo "\$*" >> "$W/restarts" ;; esac
EOF
    chmod +x "$W/bin/systemctl"
    # A "Claude" for this world: a copy of sleep where moses's Claude lives, found by what it runs (the
    # real busy check, not a fake: this test's own Claude lives in another home and must not count).
    mkdir -p "$HOME/.local/share/claude/versions"; cp "$(command -v sleep)" "$HOME/.local/share/claude/versions/sleep"
}
pull() {   # pull [script]: run moses-pull against the world
    PATH="$W/bin:$PATH" MOSES_PULL_TREE="$W/live" MOSES_STATE="$W/state" MOSES_ALARM_CHANNEL= \
        MOSES_PULL_SETTLE=0 MOSES_CONFIG_DIR="$W/none" GIT_AUTHOR_NAME=m GIT_AUTHOR_EMAIL=m@x \
        GIT_COMMITTER_NAME=m GIT_COMMITTER_EMAIL=m@x bash "${1:-$HERE/moses-pull}" >/dev/null 2>&1
}
brad_commits() {   # brad_commits <file> [content]
    printf '%s\n' "${2:-x}" > "$W/brad/$1"
    git -C "$W/brad" add "$1" && git -C "$W/brad" -c user.email=b@x -c user.name=b commit -q -m "$1"
    git -C "$W/brad" push -q origin HEAD:main 2>/dev/null
}

run_cases() {   # run_cases <script>: prints PASS/FAIL per case
    local S=$1
    setup; brad_commits a.txt; pull "$S"
    [ -f "$W/live/a.txt" ] && grep -q restart "$W/restarts" 2>/dev/null; t "brad's new commit goes live and the units restart" $?
    rm -rf "$W"

    setup; brad_commits shared.txt brad; echo knight > "$W/live/shared.txt"
    git -C "$W/live" add shared.txt && git -C "$W/live" -c user.email=m@x -c user.name=m commit -q -m knight
    head=$(git -C "$W/live" rev-parse HEAD); pull "$S"; rc=$?
    [ $rc != 0 ] && [ "$(git -C "$W/live" rev-parse HEAD)" = "$head" ] && [ -z "$(git -C "$W/live" status --porcelain)" ] \
        && grep -q '"stopped"' "$W/state/pull.json"; t "a conflict with Knight's land leaves the live tree exactly as it was" $?
    rm -rf "$W"

    setup; head=$(git -C "$W/live" rev-parse HEAD); brad_commits BREAK; pull "$S"; rc=$?
    [ $rc != 0 ] && [ "$(git -C "$W/live" rev-parse HEAD)" = "$head" ] && grep -q rolled-back "$W/state/pull.json"
    t "a change that takes a unit down is rolled back to the commit that was running" $?
    rm -rf "$W"

    setup; brad_commits b.txt; echo dirty > "$W/live/scratch"; pull "$S"; rc=$?
    [ $rc != 0 ] && [ ! -f "$W/live/b.txt" ]; t "a dirty live tree is left alone" $?
    rm -rf "$W"

    setup; brad_commits BREAK; pull "$S"; : > "$W/restarts"; pull "$S"
    [ ! -s "$W/restarts" ]; t "a commit that was rolled back is not retried until Brad pushes again" $?
    rm -f "$W/brad/BREAK"; git -C "$W/brad" rm -q BREAK; git -C "$W/brad" -c user.email=b@x -c user.name=b commit -q -m fix
    git -C "$W/brad" push -q origin HEAD:main 2>/dev/null; pull "$S"
    [ ! -e "$W/live/BREAK" ] && [ -s "$W/restarts" ]; t "Brad's next push is taken" $?
    rm -rf "$W"

    setup; brad_commits d.txt; "$HOME/.local/share/claude/versions/sleep" 30 & cl=$!; sleep 0.3; pull "$S"; kill $cl 2>/dev/null; wait $cl 2>/dev/null
    [ ! -f "$W/live/d.txt" ] && [ ! -s "$W/restarts" ] && grep -q deferred "$W/state/pull.json"
    t "while a Claude session runs (named after its version), nothing restarts" $?
    pull "$S"; [ -f "$W/live/d.txt" ]; t "once it is quiet, the pull goes ahead" $?
    rm -rf "$W"

    setup; brad_commits e.txt; (exec -a knight-run sleep 30) & kn=$!; sleep 0.3; pull "$S"; kill $kn 2>/dev/null; wait $kn 2>/dev/null
    [ ! -f "$W/live/e.txt" ]; t "while Knight runs, nothing restarts" $?
    rm -rf "$W"

    setup; brad_commits BREAK; pull "$S"; echo dirty > "$W/live/scratch"; pull "$S"; rm "$W/live/scratch"; : > "$W/restarts"; pull "$S"
    [ ! -s "$W/restarts" ]; t "a rejected commit stays rejected across an unrelated stop" $?
    rm -rf "$W"

    setup; brad_commits c.txt; exec 7>"$W/live/.git/live-tree.lock"; flock -n 7; pull "$S"; rc=$?; exec 7>&-
    [ ! -f "$W/live/c.txt" ]; t "while knight-land holds the tree, nothing is merged" $?
    rm -rf "$W"
}

echo "moses-pull:"
run_cases "$HERE/moses-pull"
good_pass=$PASS; good_fail=$FAIL

mutate() {   # mutate <name> <python-replacement-old> <new> <case that must fail>
    local M; M=$(mktemp); python3 - "$HERE/moses-pull" "$M" "$2" "$3" <<'PY'
import sys
src, dst, old, new = sys.argv[1:]
s = open(src).read(); assert old in s, old; open(dst, "w").write(s.replace(old, new, 1))
PY
    PASS=0; FAIL=0; out=$(run_cases "$M"); rm -f "$M"
    if printf '%s' "$out" | grep -qF "FAIL  $4"; then echo "  watched failing: $1"; else echo "  FAIL guard not watched failing: $1"; good_fail=$((good_fail+1)); fi
}
mutate "no merge --abort on conflict" 'git merge --abort >/dev/null 2>&1' 'true' "a conflict with Knight's land leaves the live tree exactly as it was"
mutate "no rollback after a failed restart" 'git reset -q --hard "$before"' 'true' "a change that takes a unit down is rolled back to the commit that was running"
mutate "no dirty-tree check" '[ -z "$(git status --porcelain)" ] || stop' 'true || stop' "a dirty live tree is left alone"
mutate "rejected commit retried" '[ "$rejected" = "$target" ] &&' '[ "$rejected" = "never" ] &&' "a commit that was rolled back is not retried until Brad pushes again"
mutate "restarts mid-job" 'if busy >/dev/null; then' 'if false; then' "while a Claude session runs (named after its version), nothing restarts"
mutate "Claude found by name only" 'exe.startswith(claude_dir) or ' '' "while a Claude session runs (named after its version), nothing restarts"
mutate "rejected forgotten on any stop" 'if not rejected and status != "ok":' 'if False:' "a rejected commit stays rejected across an unrelated stop"
mutate "no lock" 'flock -n 9 ||' 'true ||' "while knight-land holds the tree, nothing is merged"

echo "$good_pass passed, $good_fail failed"
[ "$good_fail" = 0 ]
