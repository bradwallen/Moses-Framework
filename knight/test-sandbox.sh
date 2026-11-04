#!/usr/bin/env bash
# test-sandbox.sh — knight/lib/sandbox.sh, the box Knight's prep and gate steps run in.
#
#   bash knight/test-sandbox.sh
#
# Every confinement check runs a REAL step through the REAL sandbox_run and looks at what the step
# could do: read a planted secret, reach the network, write .git, see the runner's environment. Then
# the whole set runs again with sandbox_run replaced by the plain `bash -c` the runner used before
# 2026-11-04, and every confinement check must go RED. A check that stays green without the sandbox
# was never testing the sandbox (#8).
set -uo pipefail
export KNIGHT_SLACK_CHANNEL="" KNIGHT_SLACK_ENV=/nonexistent

HERE=$(cd "$(dirname "$0")" && pwd)
pass=0; fail=0
ok()  { printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL  %s\n     %s\n' "$1" "${2:-}"; fail=$((fail+1)); }

# INSIDE THE RUNNER'S GATE. A sandbox cannot start inside another on Reserve (bwrap: "No permissions to
# create a new namespace"), and this suite runs in the moses gate, which is itself sandboxed. There it
# checks the box it is in, directly. The full suite, with its watch-it-fail half, runs daily from
# moses-selftest, outside any sandbox.
if [ -n "${KNIGHT_SANDBOX:-}" ]; then
    echo
    echo "inside the runner's $KNIGHT_SANDBOX sandbox — checking the box this suite is running in"
    # The only things allowed in HOME: the package cache, and the first folder on the way to this clone
    # (bwrap makes the parents of what it binds). Nothing of brad's: no .claude, .config, .ssh, .env.
    # A target's declared extras may sit under HOME (the Moses targets expose two settings files in
    # ~/.config/moses), so this looks for what must NEVER be there: the logins and the credential files.
    seen=""
    for p in .claude .ssh .gnupg .local .aws .docker moses-venv; do [ -e "$HOME/$p" ] && seen="$seen ~/$p"; done
    envs=$(find "$HOME/.config" -name '*.env*' ! -name moses.env 2>/dev/null | head -3)   # moses.env is settings, declared credential-free
    if [ -z "$seen" ] && [ -z "$envs" ]; then
        ok "the home directory holds none of brad's logins or credential files"
    else
        bad "the home directory holds none of brad's logins or credential files" "$seen $envs"
    fi
    [ ! -e "/run/user/$(id -u)/bus" ] && ok "the user's systemd bus is not reachable" || bad "the user's systemd bus is not reachable"
    leaked=$(env | grep -iE '^[^=]*(TOKEN|SECRET|KEY|PASSWORD)[^=]*=' | cut -d= -f1)
    [ -z "$leaked" ] && ok "no credential-named variable is in the environment" || bad "no credential-named variable is in the environment" "$leaked"
    if [ "$KNIGHT_SANDBOX" = gate ]; then
        top=$(git rev-parse --show-toplevel 2>/dev/null)
        if [ -n "$top" ] && [ -d "$top/.git" ]; then
            ( : > "$top/.git/knight-sandbox-probe" ) 2>/dev/null && { rm -f "$top/.git/knight-sandbox-probe"; bad ".git is read-only in the gate" "a probe file was written"; } || ok ".git is read-only in the gate"
        else
            bad ".git is read-only in the gate" "could not find this clone's .git, so it could not be checked"
        fi
        ( exec 3<>/dev/tcp/1.1.1.1/53 ) 2>/dev/null && bad "the gate has no network" "a connection opened" || ok "the gate has no network"
    fi
    printf '\n%d ok, %d failed\n' "$pass" "$fail"
    [ "$fail" -eq 0 ]; exit
fi

if ! command -v bwrap >/dev/null 2>&1; then
    echo "  FAIL  bwrap is not installed, so the sandbox could not be tested (and every Knight gate refuses to run)"
    exit 1
fi

TMP=$(mktemp -d "${TMPDIR:-/tmp}/knight-sandbox-test.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

# A world: knight/repo/<clone> with a sibling, a planted secret outside it, a read-only extra.
W="$TMP/knight"; REPO="$W/repo/t1"; SIB="$W/repo/sib"; EXTRA="$TMP/extra"; SECRET="$TMP/secret.env"
mkdir -p "$REPO" "$SIB" "$EXTRA"
git -C "$REPO" init -q -b main
echo 'SLACK_TOKEN=xoxb-canary' > "$SECRET"
echo hello > "$EXTRA/readme"
export SANDBOX_CACHE_ROOT="$W/cache"
export RUNNER_SECRET_CANARY=leaked-from-the-runner

KT_SANDBOX_RW='../sib'; KT_SANDBOX_RO="$EXTRA"; KT_SANDBOX_ENV='FOO=bar'
. "$HERE/lib/sandbox.sh"

# A second clone for the runner's-own-git checks: one commit, a bare push target, and a pre-push hook
# in an in-repo hooks folder (what Viatica's `npm install` sets up) that tries to read the secret.
G="$W/repo/g"; BARE="$TMP/bare.git"
git init -q -b main "$G"; git init -q --bare "$BARE"
git -C "$G" -c user.name=t -c user.email=t@t commit -q --allow-empty -m one
mkdir -p "$G/.githooks"
printf '#!/bin/sh\ncat %q >/dev/null\n' "$SECRET" > "$G/.githooks/pre-push"; chmod +x "$G/.githooks/pre-push"
git -C "$G" config core.hooksPath .githooks
git -C "$G" remote add push-target "$BARE"

# The sandbox's own startup must work here, or every "refused" below proves nothing.
if ! out=$(sandbox_run gate "$REPO" 'echo alive' 2>&1) || [ "$out" != alive ]; then
    # Knight's own knight-gate runs inside Claude Code's sandbox, where no second one can start. Said, not
    # hidden: the runner's gate and the daily moses-selftest are where this suite actually runs.
    if [ -n "${KNIGHT_GATE_LOCAL:-}" ] && grep -q "create a new namespace" <<<"$out"; then
        echo "  skip  a sandbox cannot start inside Claude Code's, so this suite could not run here. The runner's gate checks its own box, and moses-selftest runs it in full daily."
        exit 0
    fi
    echo "  FAIL  the sandbox could not start here, so nothing below would mean anything — and every Knight gate refuses to run: $out"
    exit 1
fi

# check <label> <mode> <step> <expect: ok|refused> — "refused" means the step must exit non-zero.
confinement() {  # confinement checks: each must be refused in the sandbox and allowed without it
    check "a gate step cannot read a secret outside the clone"   gate "cat '$SECRET'"
    check "a prep step cannot read it either"                     prep "cat '$SECRET'"
    check "the home directory holds nothing of brad's"            gate "[ -n \"\$(ls -A \"\$HOME\" | grep -vx .cache)\" ]"
    check "the runner's environment does not leak in"             gate "[ -n \"\${RUNNER_SECRET_CANARY:-}\" ]"
    check "the gate has no network"                               gate "exec 3<>/dev/tcp/1.1.1.1/53"
    check "a gate step cannot write .git (no hook for the runner to run later)" gate "echo x > .git/hooks/pre-push"
    check "a gate step cannot write the declared sibling"         gate "echo x > ../sib/planted"
    check "a prep step cannot write a read-only extra"            prep "echo x > '$EXTRA/planted'"
    check "a step cannot reach the user's systemd bus"            gate "[ -e /run/user/$(id -u)/bus ] || [ -e /run/user/$(id -u)/systemd ]"
    check "a step cannot see the runner's processes"              gate "kill -0 $$"
    # Sockets a read-only file system does not stop (review, 2026-11-04): Tailscale's control socket
    # and sshd's are mode 666. Checked only where they exist; elsewhere this machine cannot show it.
    for sock in /run/tailscale/tailscaled.sock /run/ssh-unix-local/socket; do
        [ -S "$sock" ] || continue
        check "a step cannot connect to $sock" prep "python3 -c 'import socket,sys; s=socket.socket(socket.AF_UNIX); s.connect(sys.argv[1])' $sock"
    done
    if pre_push_in_sandbox "$G" push-target "$BARE" HEAD:refs/heads/x >/dev/null 2>&1; then
        echo "LEAK|a target's pre-push hook runs in the sandbox (it cannot read the secret)"
    else
        echo "HELD|a target's pre-push hook runs in the sandbox (it cannot read the secret)"
    fi
}

check() {
    local label=$1 mode=$2 step=$3
    if sandbox_run "$mode" "$REPO" "$step" >/dev/null 2>&1; then
        echo "LEAK|$label"
    else
        echo "HELD|$label"
    fi
}

echo
echo "knight/lib/sandbox.sh — what a prep or gate step can and cannot reach"
while IFS='|' read -r v label; do
    [ "$v" = HELD ] && ok "$label" || bad "$label" "the step succeeded"
done < <(confinement)

# What a step MUST still be able to do, or the sandbox gets switched off (#8, the other half).
can() {
    local label=$1 mode=$2 step=$3 out
    if out=$(sandbox_run "$mode" "$REPO" "$step" 2>&1); then ok "$label"; else bad "$label" "$out"; fi
}
can "a step writes inside the clone"                     gate "echo x > built.txt && [ -s built.txt ]"
can "a step reads a read-only extra"                     gate "grep -q hello '$EXTRA/readme'"
can "a gate step reads the declared sibling"             gate "[ -d ../sib ]"
can "a prep step writes the declared sibling"            prep "echo x > ../sib/fetched && [ -s ../sib/fetched ]"
can "a prep step writes .git (hooks the target installs)" prep "echo '#!/bin/sh' > .git/hooks/pre-commit"
can "a prep step keeps a package cache between runs"     prep "echo x > \"\$HOME/.cache/kept\""
[ -f "$W/cache/t1/kept" ] && ok "and the cache is outside the clone, per target" || bad "and the cache is outside the clone, per target" "no $W/cache/t1/kept"
can "the target's declared env is set"                   gate "[ \"\$FOO\" = bar ]"
can "git, python3 and node are on the path"              gate "git --version && python3 --version && node --version"
can "a step can make and commit to a scratch repo"       gate "d=\$(mktemp -d) && git -C \$d init -q && git -C \$d commit -q --allow-empty -m t"
GATE_ONLY=one/test.py can "GATE_ONLY set by the caller reaches the step" gate "[ \"\$GATE_ONLY\" = one/test.py ]"

# Red stays red, with its own exit code — the runner names the failing step from it.
sandbox_run gate "$REPO" 'exit 3' >/dev/null 2>&1; rc=$?
[ "$rc" = 3 ] && ok "a red step's exit code comes back unchanged" || bad "a red step's exit code comes back unchanged" "rc=$rc"

# The ways it must refuse to run at all, and say so. Never a quiet unsandboxed run.
out=$(SANDBOX_BWRAP=/nonexistent/bwrap sandbox_run gate "$REPO" "touch '$TMP/ran-anyway'" 2>&1); rc=$?
[ "$rc" = 125 ] && [ ! -e "$TMP/ran-anyway" ] && grep -q "NOT run" <<<"$out" \
    && ok "with bwrap missing, the step does not run, and says so" \
    || bad "with bwrap missing, the step does not run, and says so" "rc=$rc $out"
out=$(KT_SANDBOX_RW='../nowhere' sandbox_run gate "$REPO" "touch '$TMP/ran-anyway'" 2>&1); rc=$?
[ "$rc" != 0 ] && [ ! -e "$TMP/ran-anyway" ] && ok "a declared path that does not exist refuses the step" \
    || bad "a declared path that does not exist refuses the step" "rc=$rc $out"
out=$(KT_SANDBOX_RO='relative/path' sandbox_run gate "$REPO" "touch '$TMP/ran-anyway'" 2>&1); rc=$?
[ "$rc" != 0 ] && [ ! -e "$TMP/ran-anyway" ] && ok "a relative read-only path refuses the step" \
    || bad "a relative read-only path refuses the step" "rc=$rc $out"
out=$(KT_SANDBOX_ENV='not a pair' sandbox_run gate "$REPO" "touch '$TMP/ran-anyway'" 2>&1); rc=$?
[ "$rc" != 0 ] && [ ! -e "$TMP/ran-anyway" ] && ok "an env entry that is not NAME=value refuses the step" \
    || bad "an env entry that is not NAME=value refuses the step" "rc=$rc $out"

# ── The runner's own git ─────────────────────────────────────────────────────
echo
echo "the runner's own git, in a clone Knight has worked in"
printf '#!/bin/sh\ntouch %q\n' "$TMP/hook-ran" > "$G/.githooks/pre-commit"; chmod +x "$G/.githooks/pre-commit"
( runner_git_hardening; git -C "$G" -c user.name=t -c user.email=t@t commit -q --allow-empty -m two ) >/dev/null 2>&1
[ ! -e "$TMP/hook-ran" ] && ok "with the hardening on, git runs no hook from the clone" || bad "with the hardening on, git runs no hook from the clone" "the pre-commit hook ran"
git -C "$G" -c user.name=t -c user.email=t@t commit -q --allow-empty -m three >/dev/null 2>&1
[ -e "$TMP/hook-ran" ] && ok "watched fail: without it, the same commit runs the hook" || bad "watched fail: without it, the same commit runs the hook" "the hook did not run even unhardened, so the check above proves nothing"
rm -f "$G/.githooks/pre-commit"

cfg0=$(git_config_fingerprint "$G")
[ -n "$cfg0" ] && git_config_check "$G" "$cfg0" >/dev/null && ok "an untouched .git/config passes" || bad "an untouched .git/config passes" "cfg0=$cfg0"
cp "$G/.git/config" "$TMP/config.saved"
git -C "$G" config core.sshCommand "touch $TMP/ssh-ran"
out=$(git_config_check "$G" "$cfg0"); [ $? -ne 0 ] && grep -q "changed" <<<"$out" \
    && ok "a job that adds a config line (core.sshCommand) is refused, and says why" || bad "a job that adds a config line (core.sshCommand) is refused, and says why" "$out"
cp "$TMP/config.saved" "$G/.git/config"
printf '[include]\n\tpath = ../planted.cfg\n' >> "$G/.git/config"; cfgi=$(git_config_fingerprint "$G")
out=$(git_config_check "$G" "$cfgi"); [ $? -ne 0 ] && grep -q "includes" <<<"$out" \
    && ok "a config that includes another file is refused even when unchanged" || bad "a config that includes another file is refused even when unchanged" "$out"
cp "$TMP/config.saved" "$G/.git/config"
out=$(git_config_check "$G" ""); [ $? -ne 0 ] && grep -q "could not be read before" <<<"$out" \
    && ok "with no fingerprint taken before the agent, nothing is pushed" || bad "with no fingerprint taken before the agent, nothing is pushed" "$out"

[ -z "$(git_config_unexpected "$G")" ] && ok "a clean clone's config passes the job-start check" || bad "a clean clone's config passes the job-start check" "$(git_config_unexpected "$G")"
git -C "$G" config filter.x.smudge "touch $TMP/smudge-ran"
grep -q "filter.x.smudge" <<<"$(git_config_unexpected "$G")" \
    && ok "a setting left by an earlier job (a filter) stops the next job before git reads it" || bad "a setting left by an earlier job (a filter) stops the next job before git reads it" "$(git_config_unexpected "$G")"
( GIT_CONFIG_ALLOWED='.*'; [ -z "$(git_config_unexpected "$G")" ] ) \
    && ok "watched fail: with everything allowed, the planted filter is not seen" || bad "watched fail: with everything allowed, the planted filter is not seen"
cp "$TMP/config.saved" "$G/.git/config"

printf '#!/bin/sh\nread l; echo "$l" > ../prepush.stdin; exit 0\n' > "$G/.githooks/pre-push"
pre_push_in_sandbox "$G" push-target "$BARE" HEAD:refs/heads/x >/dev/null 2>&1
# Inside, the parent folder is the sandbox's own scratch space, so the write may "succeed" there; what
# matters is that nothing reaches the real disk beside the clone.
[ ! -e "$W/repo/prepush.stdin" ] && ok "a pre-push hook's write outside the clone never reaches the disk" || bad "a pre-push hook's write outside the clone never reaches the disk" "$W/repo/prepush.stdin exists"
printf '#!/bin/sh\nread l r ref rs; [ "$ref" = refs/heads/x ] && [ "$rs" = 0000000000000000000000000000000000000000 ] && [ "$1" = push-target ]\n' > "$G/.githooks/pre-push"
pre_push_in_sandbox "$G" push-target "$BARE" HEAD:refs/heads/x >/dev/null 2>&1 \
    && ok "a passing pre-push hook lets the push go, and gets git's arguments and ref line" || bad "a passing pre-push hook lets the push go, and gets git's arguments and ref line"
git -C "$G" config core.hooksPath /etc
out=$(pre_push_in_sandbox "$G" push-target "$BARE" HEAD:refs/heads/x 2>&1); [ $? -ne 0 ] && grep -q "outside the clone" <<<"$out" \
    && ok "a hooks path outside the clone is refused" || bad "a hooks path outside the clone is refused" "$out"
git -C "$G" config core.hooksPath .githooks

# A .git that points elsewhere is refused before its config is trusted (review, 2026-11-04).
echo "$TMP/elsewhere" > "$G/.git/commondir"
out=$(git_config_check "$G" "$cfg0"); r1=$?; u=$(git_config_unexpected "$G"); r2=$?
[ "$r1" != 0 ] && [ "$r2" != 0 ] && grep -q commondir <<<"$out" \
    && ok "a .git/commondir (config read from elsewhere) is refused at the start and before the push" || bad "a .git/commondir (config read from elsewhere) is refused at the start and before the push" "$out / $u"
rm -f "$G/.git/commondir"

# A declared read-only path that is missing right now is said, and the step still runs.
out=$(KT_SANDBOX_RO="$TMP/not-there" sandbox_run gate "$REPO" 'echo ran' 2>&1)
grep -q "missing right now" <<<"$out" && grep -qx ran <<<"$out" \
    && ok "a missing read-only extra is named, and the step still runs (no gate red for it)" || bad "a missing read-only extra is named, and the step still runs (no gate red for it)" "$out"

# The pre-push guard is the MAINLINE's copy: a branch that deletes or weakens it does not skip it.
git -C "$G" -c user.name=t -c user.email=t@t checkout -q -b mainline
printf '#!/bin/sh\necho mainline-guard-ran >&2; exit 1\n' > "$G/.githooks/pre-push"; chmod +x "$G/.githooks/pre-push"
git -C "$G" add .githooks/pre-push; git -C "$G" -c user.name=t -c user.email=t@t commit -qm "guard"
mainsha=$(git -C "$G" rev-parse HEAD)
git -C "$G" checkout -q -b work; git -C "$G" rm -q .githooks/pre-push; git -C "$G" -c user.name=t -c user.email=t@t commit -qm "drop the guard"
out=$(pre_push_in_sandbox "$G" push-target "$BARE" HEAD:refs/heads/work "$mainsha" 2>&1); rc=$?
[ "$rc" != 0 ] && grep -q mainline-guard-ran <<<"$out" \
    && ok "a branch that deletes the pre-push guard still meets the mainline's copy" || bad "a branch that deletes the pre-push guard still meets the mainline's copy" "rc=$rc $out"
out=$(pre_push_in_sandbox "$G" push-target "$BARE" HEAD:refs/heads/work 2>&1); rc=$?
[ "$rc" = 0 ] && ok "watched fail: with no mainline given, the deleted guard is skipped" || bad "watched fail: with no mainline given, the deleted guard is skipped" "rc=$rc"
git -C "$G" checkout -q mainline 2>/dev/null; rm -f "$G/.git/knight-pre-push"
printf '#!/bin/sh\ncat %q >/dev/null\n' "$SECRET" > "$G/.githooks/pre-push"   # back to the secret-reader for the watch-fail pass

# The stand-in for nested runs: never on a bare host, only inside a sandbox, and it says so.
( unset SANDBOX_BWRAP KNIGHT_SANDBOX KNIGHT_GATE_LOCAL; sandbox_use_stand_in_if_nested >/dev/null; [ -z "${SANDBOX_BWRAP:-}" ] ) \
    && ok "outside any sandbox, the runner never swaps in the stand-in" || bad "outside any sandbox, the runner never swaps in the stand-in"
( unset SANDBOX_BWRAP; KNIGHT_SANDBOX=gate; sandbox_use_stand_in_if_nested >/dev/null; [ -z "${SANDBOX_BWRAP:-}" ] ) \
    && ok "where a real sandbox CAN start, it is used even when asked from inside one" || bad "where a real sandbox CAN start, it is used even when asked from inside one"
out=$(SANDBOX_BWRAP="$HERE/lib/fake-bwrap" sandbox_run gate "$REPO" "touch '$TMP/stand-in-ran'" 2>&1); rc=$?
[ "$rc" = 125 ] && [ ! -e "$TMP/stand-in-ran" ] && grep -q "refused" <<<"$out" \
    && ok "a stand-in named outside any sandbox is refused, and the step does not run" || bad "a stand-in named outside any sandbox is refused, and the step does not run" "rc=$rc $out"
out=$(KNIGHT_SANDBOX=gate SANDBOX_BWRAP="$HERE/lib/fake-bwrap" sandbox_run gate "$REPO" 'echo "$FOO"' 2>&1)
grep -q "stand-in" <<<"$out" && grep -qx bar <<<"$out" \
    && ok "the stand-in says it is in use, and still sets the step's environment" || bad "the stand-in says it is in use, and still sets the step's environment" "$out"

# EVERY RUN SITE USES IT. A runner that sandboxes the gate but not the re-prep is the hole again.
sites=$(grep -nE 'bash -c "\$step"' "$HERE/bin/knight" "$HERE/bin/knight-run" 2>/dev/null)
[ -z "$sites" ] && ok "no prep or gate step in knight or knight-run runs outside sandbox_run" \
    || bad "no prep or gate step in knight or knight-run runs outside sandbox_run" "$sites"
# AFTER the target file is sourced, or a line in it could undo the hardening (review, 2026-11-04).
grep -A1 '^    source "\$f"$' "$HERE/bin/knight" | grep -q runner_git_hardening \
    && ok "knight turns hooks off for its own git, right after loading the target" || bad "knight turns hooks off for its own git, right after loading the target"
awk '/source "\$MOSES_ROOT\/knight\/targets\/\$TARGET.env"/{t=NR} /^runner_git_hardening/{h=NR} END{exit !(t && h > t)}' "$HERE/bin/knight-run" \
    && ok "knight-run turns hooks off for its own git, after loading the target" || bad "knight-run turns hooks off for its own git, after loading the target"
n=$(grep -c 'git_config_check "\$REPO" "\$git_cfg0"' "$HERE/bin/knight-run")
[ "$n" -ge 3 ] && ok "knight-run checks .git/config after the agent, after the gate and right before the push" \
    || bad "knight-run checks .git/config after the agent, after the gate and right before the push" "found $n checks"
grep -q 'pre_push_in_sandbox "\$REPO"' "$HERE/bin/knight-run" && ok "knight-run runs the target's pre-push guard in the sandbox" || bad "knight-run runs the target's pre-push guard in the sandbox"
grep -q 'git_config_unexpected "\$REPO"' "$HERE/bin/knight" && ok "knight checks the clone's config before a job touches it" || bad "knight checks the clone's config before a job touches it"

# WATCH IT FAIL: the same confinement checks with the pre-2026-11-04 runner (plain bash -c). Each one
# must leak, or that check cannot tell a sandbox from none.
sandbox_run() { ( cd "$2" && bash -c "$3" ); }
echo
echo "watch it fail — the same checks with no sandbox (the old runner)"
while IFS='|' read -r v label; do
    case "$label" in
        "the gate has no network") [ "$v" = LEAK ] && ok "watched fail: $label" \
            || echo "  skip  watched fail: $label — this machine has no route out right now, so it could not be shown" ;;
        *) [ "$v" = LEAK ] && ok "watched fail: $label" || bad "watched fail: $label" "it held with no sandbox, so it tests nothing" ;;
    esac
done < <(confinement)

# And the inside-the-box checks the runner's gate relies on: claimed to be inside, but run with no box,
# they must go red, or the moses gate's own check of its sandbox is a green that means nothing.
if ( cd "$REPO" && KNIGHT_SANDBOX=gate bash "$HERE/test-sandbox.sh" >/dev/null 2>&1 ); then
    bad "watched fail: the inside-the-box checks, run with no box" "they passed with no sandbox around them"
else
    ok "watched fail: the inside-the-box checks, run with no box"
fi

printf '\n%d ok, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
