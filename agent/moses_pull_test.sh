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
        GIT_COMMITTER_NAME=m GIT_COMMITTER_EMAIL=m@x bash "${1:-$HERE/moses-pull}" >/dev/null 2>>"$W/said"
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

    # units.json: what this account runs, for the operator's drift check (it cannot read this home).
    setup; mkdir -p "$HOME/.config/systemd/user"; printf 'x\n' > "$HOME/.config/systemd/user/moses-x.timer"
    pull "$S"   # nothing new from brad: it must still be written
    python3 -c 'import hashlib,json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d["units"]=={"moses-x.timer":hashlib.sha256(b"x\n").hexdigest()} else 1)' "$W/state/units.json" 2>/dev/null
    t "every run reports this account's units by content, even with nothing to take" $?
    rm -rf "$W"

    # pyenvs.json: the packages installed in this account's venvs, for the operator's vulnerability check.
    setup; V="$HOME/moses-venv"; mkdir -p "$V/lib/python3.14/site-packages/Star_lette-1.0.0.dist-info"; : > "$V/pyvenv.cfg"
    printf 'Name: Star_lette\nVersion: 1.0.0\n\nName: not-this\n' > "$V/lib/python3.14/site-packages/Star_lette-1.0.0.dist-info/METADATA"
    pull "$S"
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d["venvs"].get(sys.argv[2])=={"star-lette":"1.0.0"} else 1)' "$W/state/pyenvs.json" "$V" 2>/dev/null
    t "every run reports the packages installed in this account's venvs" $?
    rm -rf "$W"

    # Pinned packages: a changed list is installed as this account BEFORE the restart; a failure restarts nothing.
    lockworld() {   # a venv whose python records each call, and fails while $W/PIPFAIL exists
        mkdir -p "$W/venv/bin"; printf '#!/bin/sh\n[ -e "%s/PIPFAIL" ] && { echo "ERROR: no matching distribution" >&2; exit 1; }\necho "$*" >> "%s/pip"\necho pip >> "%s/restarts"\n' "$W" "$W" "$W" > "$W/venv/bin/python"
        chmod +x "$W/venv/bin/python"; export MOSES_PULL_LOCKS="mcp/requirements.lock:$W/venv"
    }
    setup; lockworld; brad_commits mcp_lock_dir_placeholder; pull "$S"   # the first pull: no list yet
    mkdir -p "$W/brad/mcp"; brad_commits mcp/requirements.lock "pillow==13.0.0"; : > "$W/restarts"; pull "$S"
    grep -q -- "-m pip install" "$W/pip" 2>/dev/null && [ "$(head -1 "$W/restarts")" = pip ] && [ -f "$W/live/mcp/requirements.lock" ]
    t "a changed package list is installed as this account, before anything restarts" $?
    n=$(wc -l < "$W/pip"); brad_commits other.txt; pull "$S"
    [ "$(wc -l < "$W/pip")" = "$n" ]; t "an unchanged package list is not installed again" $?
    unset MOSES_PULL_LOCKS; rm -rf "$W"

    # The bootstrap: a list that went live under an older moses-pull is installed on the next pull.
    setup; lockworld; MOSES_PULL_LOCKS="none:$W/nowhere"; mkdir -p "$W/brad/mcp"; brad_commits mcp/requirements.lock "pillow==13.0.0"; pull "$S"
    export MOSES_PULL_LOCKS="mcp/requirements.lock:$W/venv"; brad_commits other.txt; pull "$S"
    grep -q -- "-m pip install" "$W/pip" 2>/dev/null; t "a list that went live without being installed is installed on the next pull" $?
    unset MOSES_PULL_LOCKS; rm -rf "$W"

    setup; lockworld; brad_commits a.txt; pull "$S"; : > "$W/restarts"; : > "$W/PIPFAIL"
    mkdir -p "$W/brad/mcp"; brad_commits mcp/requirements.lock "pillow==99"; pull "$S"
    [ ! -f "$W/live/mcp/requirements.lock" ] && ! grep -q restart "$W/restarts" && grep -q "pinned packages" "$W/state/pull.json"
    t "a failed install puts the tree back, restarts nothing, and says so" $?
    unset MOSES_PULL_LOCKS; rm -rf "$W"

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

    # The unrelated stop is a failed fetch: a dirty tree no longer stops anything once the commit is rejected.
    setup; brad_commits BREAK; pull "$S"; git -C "$W/live" remote set-url origin "$W/gone.git"; pull "$S"
    git -C "$W/live" remote set-url origin "$W/hub.git"; : > "$W/restarts"; pull "$S"
    [ ! -s "$W/restarts" ]; t "a rejected commit stays rejected across an unrelated stop" $?
    rm -rf "$W"

    setup; echo dirty > "$W/live/scratch"; pull "$S"; pull "$S"
    ! grep -q ":warning:" "$W/said" 2>/dev/null; t "a dirty tree with nothing new to take says nothing" $?
    rm -rf "$W"

    setup; brad_commits f.txt; echo dirty > "$W/live/scratch"; pull "$S"; pull "$S"; pull "$S"
    [ "$(grep -c ":warning:" "$W/said")" = 1 ] && grep -q "scratch" "$W/said"; t "the same stop is said once, and names the dirty files" $?
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
mutate "no dirty-tree check" '[ -z "$dirty" ] || stop' 'true || stop' "a dirty live tree is left alone"
mutate "rejected commit retried" '[ "$rejected" = "$target" ] &&' '[ "$rejected" = "never" ] &&' "a commit that was rolled back is not retried until Brad pushes again"
mutate "restarts mid-job" 'if busy >/dev/null; then' 'if false; then' "while a Claude session runs (named after its version), nothing restarts"
mutate "Claude found by name only" 'exe.startswith(claude_dir) or ' '' "while a Claude session runs (named after its version), nothing restarts"
mutate "rejected forgotten on any stop" 'if not rejected and status != "ok":' 'if False:' "a rejected commit stays rejected across an unrelated stop"
mutate "dirty check before the up-to-date check" 'git fetch -q origin || stop' '[ -z "$(git status --porcelain)" ] || stop "dirty"; git fetch -q origin || stop' "a dirty tree with nothing new to take says nothing"
mutate "same stop said every run" 'if [ "$said" = "stopped|$1" ]; then' 'if false; then' "the same stop is said once, and names the dirty files"
mutate "packages never installed" 'if ! installed=$(install_locks "$after"); then' 'if false; then' "a changed package list is installed as this account, before anything restarts"
mutate "only a list changed in this pull" '[ "$want" = "$(cat "$venv/.moses-lock" 2>/dev/null)" ] && continue' 'git diff --quiet "$before" "$rev" -- "$lock" 2>/dev/null && continue' "a list that went live without being installed is installed on the next pull"
mutate "installed every time" '[ "$want" = "$(cat "$venv/.moses-lock" 2>/dev/null)" ] && continue' 'true' "an unchanged package list is not installed again"
mutate "a failed install ignored" 'if ! installed=$(install_locks "$after"); then' 'if ! installed=$(install_locks "$after"; true); then' "a failed install puts the tree back, restarts nothing, and says so"
mutate "no venv report" '"$STATE/pyenvs.json"' '"/dev/null"' "every run reports the packages installed in this account's venvs"
mutate "no units report" '"$STATE/units.json"' '"/dev/null"' "every run reports this account's units by content, even with nothing to take"
mutate "no lock" 'flock -n 9 ||' 'true ||' "while knight-land holds the tree, nothing is merged"

echo "$good_pass passed, $good_fail failed"
[ "$good_fail" = 0 ]
