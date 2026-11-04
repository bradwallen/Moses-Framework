# knight/lib/sandbox.sh — run one prep or gate step inside a bubblewrap sandbox. Sourced, not run.
#
#   . "$KNIGHT_HOME/lib/sandbox.sh"
#   sandbox_run gate "$REPO" "$step"      # no network, .git read-only, empty home
#   sandbox_run prep "$REPO" "$step"      # network on, writable package cache, empty home
#
# WHY (2026-11-04, #17 MF7). Knight's agent session runs inside Claude Code's sandbox. Then the runner
# ran his WORK — the tests he wrote, his prep.sh, his package scripts — with `bash -c`, as brad, with
# network and the whole home directory: every .env, the memory corpus, the Slack token in the runner's
# own environment, /mnt. A model wrote code, and that code ran unsandboxed. Now every prep and gate
# step runs here instead.
#
# WHAT THE STEP SEES:
#   - The whole filesystem READ-ONLY (so /usr/bin/node, python3, git work as normal),
#   - except /home, /tmp, /mnt, /media, /srv, /opt, /run/user, /var/log and /var/backups, which are
#     EMPTY. HOME is an empty directory; nothing of brad's home is there but what is bound below.
#   - The clone, writable. In gate mode its .git is read-only, so a test cannot plant a hook or a
#     git config that the runner would later execute outside this sandbox when it pushes.
#   - The target's declared extras: KT_SANDBOX_RW (paths relative to the clone, writable in prep and
#     read-only in gate) and KT_SANDBOX_RO (absolute paths, always read-only). Space-separated,
#     single-line values, so knight-land's line check sees any change to them (a branch may not widen
#     its own sandbox).
#   - A clean environment: PATH, HOME, LANG, TMPDIR, a git identity, the package-cache settings,
#     GATE_ONLY when set, and the target's KT_SANDBOX_ENV (space-separated NAME=value). Nothing from
#     the runner's environment leaks in.
#   - Network in prep only (installs need it). The gate gets loopback and nothing else.
#   - Its own pid, ipc and uts namespaces, a new session (no TIOCSTI into the runner's terminal), and
#     it dies with the runner.
#
# NO FALLBACK. If bwrap is missing or cannot start, the step FAILS and says why. A gate that quietly
# runs unsandboxed when the sandbox breaks is the old hole with a comment on it.

sandbox_run() {
    local mode=$1 repo=$2 step=$3
    case "$mode" in prep|gate) ;; *) echo "sandbox: unknown mode '$mode' (prep or gate)" >&2; return 2 ;; esac
    local bw=bwrap
    if [ -n "${SANDBOX_BWRAP:-}" ]; then
        # A stand-in only where a real sandbox already surrounds this process (the nested test case),
        # or a path that does not exist (the "bwrap missing" test). Anywhere else it is refused: a
        # variable that could swap the sandbox for a pass-through is a switch nobody may flip.
        if [ -n "${KNIGHT_SANDBOX:-}${KNIGHT_GATE_LOCAL:-}" ] || [ ! -e "$SANDBOX_BWRAP" ]; then
            bw=$SANDBOX_BWRAP
            echo "sandbox: using $SANDBOX_BWRAP in place of bwrap (a test stand-in) for: ${step:0:80}" >&2
        else
            echo "sandbox: SANDBOX_BWRAP is set outside any sandbox — refused, this step was NOT run" >&2
            return 125
        fi
    fi
    if ! command -v "$bw" >/dev/null 2>&1; then
        echo "sandbox: bwrap is not installed, so this step was NOT run (it never runs unsandboxed)" >&2
        return 125
    fi
    repo=$(realpath -e "$repo" 2>/dev/null) || { echo "sandbox: no clone at $2" >&2; return 2; }
    local home=$HOME    # same path inside, but empty
    local cache="${SANDBOX_CACHE_ROOT:-$(dirname "$(dirname "$repo")")/cache}/$(basename "$repo")"

    local a=(
        --die-with-parent --new-session
        --unshare-pid --unshare-ipc --unshare-uts --unshare-cgroup-try
        --ro-bind / /
        --dev /dev --proc /proc
        --tmpfs /tmp --tmpfs /var/tmp
        --tmpfs /home --tmpfs /mnt --tmpfs /media --tmpfs /srv --tmpfs /opt
        --tmpfs /var/log --tmpfs /var/backups
        # ALL of /run, not just /run/user (review, 2026-11-04): /run/tailscale/tailscaled.sock and the
        # sshd socket are mode 666, and a read-only file system does not stop a connect() — the gate
        # reached the tailnet through them. Only the DNS stub comes back, for prep's installs.
        --tmpfs /run
        --ro-bind-try /run/systemd/resolve /run/systemd/resolve
        --dir "$home"
    )
    if [ "$mode" = gate ]; then
        a+=(--unshare-net --tmpfs "$home/.cache")
    else
        mkdir -p "$cache" || { echo "sandbox: cannot make the package cache $cache" >&2; return 2; }
        a+=(--bind "$cache" "$home/.cache")
    fi
    a+=(--bind "$repo" "$repo")
    [ "$mode" = gate ] && [ -e "$repo/.git" ] && a+=(--ro-bind "$repo/.git" "$repo/.git")

    local p r
    for p in ${KT_SANDBOX_RW:-}; do
        r=$(realpath -e "$repo/$p" 2>/dev/null) || { echo "sandbox: KT_SANDBOX_RW path '$p' does not exist beside the clone" >&2; return 2; }
        if [ "$mode" = prep ]; then a+=(--bind "$r" "$r"); else a+=(--ro-bind "$r" "$r"); fi
    done
    for p in ${KT_SANDBOX_RO:-}; do
        case "$p" in /*) ;; *) echo "sandbox: KT_SANDBOX_RO path '$p' must be absolute" >&2; return 2 ;; esac
        # MISSING IS SAID, NOT FATAL (review, 2026-11-04). These are things a test reads (Playwright's
        # browsers, an operator file). An `npm ci` elsewhere briefly removing one must not turn every
        # gate red as "the gate failed at" — the test that needs it fails by name, after this line.
        r=$(realpath -e "$p" 2>/dev/null) || { echo "sandbox: declared read-only path '$p' is missing right now — the step runs without it" >&2; continue; }
        a+=(--ro-bind "$r" "$r")
    done

    a+=(--clearenv
        --setenv PATH /usr/local/bin:/usr/bin:/bin
        --setenv HOME "$home"
        --setenv LANG C.UTF-8
        --setenv TMPDIR /tmp
        --setenv npm_config_cache "$home/.cache/npm"
        --setenv npm_config_update_notifier false
        --setenv PIP_CACHE_DIR "$home/.cache/pip"
        --setenv PIP_DISABLE_PIP_VERSION_CHECK 1
        --setenv GIT_AUTHOR_NAME "Knight gate" --setenv GIT_AUTHOR_EMAIL knight-gate@localhost
        --setenv GIT_COMMITTER_NAME "Knight gate" --setenv GIT_COMMITTER_EMAIL knight-gate@localhost
        --setenv KNIGHT_SANDBOX "$mode")
    [ -n "${GATE_ONLY:-}" ] && a+=(--setenv GATE_ONLY "$GATE_ONLY")
    local kv
    for kv in ${KT_SANDBOX_ENV:-}; do
        [[ "$kv" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || { echo "sandbox: KT_SANDBOX_ENV entry '$kv' is not NAME=value" >&2; return 2; }
        a+=(--setenv "${kv%%=*}" "${kv#*=}")
    done

    "$bw" "${a[@]}" --chdir "$repo" -- /bin/bash -c "$step"
}

# For the suites that drive the runner (test-runner, test-provision): when they are ALREADY inside a
# sandbox — the moses gate (KNIGHT_SANDBOX) or Knight's knight-gate inside Claude Code's
# (KNIGHT_GATE_LOCAL) — a second bwrap cannot start on Reserve, so they use lib/fake-bwrap. Never on a
# bare host: there a bwrap that cannot start is a broken sandbox, and every step refuses.
sandbox_use_stand_in_if_nested() {
    [ -n "${KNIGHT_SANDBOX:-}${KNIGHT_GATE_LOCAL:-}" ] || return 0
    bwrap --ro-bind / / --dev /dev --proc /proc -- /bin/true 2>/dev/null && return 0
    export SANDBOX_BWRAP="$(dirname "${BASH_SOURCE[0]}")/fake-bwrap"
    echo "  note  already inside a sandbox, where a second cannot start — the runner's steps use lib/fake-bwrap here (test-sandbox.sh covers the real one)"
}

# ── The runner's OWN git, in a clone Knight has had his hands on ───────────────────────────────────
# The gate is sandboxed, but the runner then commits, rebases and pushes in that clone itself, as brad,
# with network. git runs hooks and reads .git/config while it does, and Knight could write both: a
# .git/hooks/post-rewrite, or a config line like core.sshCommand, credential.helper,
# remote.<name>.receivepack, a filter or merge driver, or gpg.program. Each of those is a command git
# would run for him, outside every sandbox. Three things close that (2026-11-04):
#
#   1. runner_git_hardening: every git the runner runs ignores hooks (core.hooksPath=/dev/null) and
#      fsmonitor. Set through GIT_CONFIG_COUNT, which outranks anything in .git/config.
#   2. git_config_check: .git/config must be byte-for-byte what it was before the agent ran, and may
#      not include another file. That one check covers every config-run command, including ones
#      nobody has thought of yet, instead of a list of keys that is always one short.
#   3. pre_push_in_sandbox: a target's own pre-push guard (moses-framework's publish check) still
#      runs, but here, in the gate sandbox, not as a git hook outside it.

runner_git_hardening() {
    export GIT_CONFIG_COUNT=2
    export GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null
    export GIT_CONFIG_KEY_1=core.fsmonitor GIT_CONFIG_VALUE_1=false
}

# What a Knight clone's .git/config may hold when a job STARTS. A job that planted something and was
# refused for it leaves it behind, and the next job's own fetch and checkout would read it before any
# fingerprint was taken — so the start checks content, not change. Anything else is a person's to look at.
GIT_CONFIG_ALLOWED='^(core\.(repositoryformatversion|filemode|bare|logallrefupdates|hookspath)|remote\.push-target\.(url|fetch))$'

# .git must be a plain directory with no pointer elsewhere: a .git FILE (gitdir:), a symlink, or a
# commondir makes git read a config these checks never see (review, 2026-11-04).
git_dir_plain() {    # git_dir_plain REPO — prints why not, and fails
    local g="$1/.git"
    if [ -L "$g" ] || [ ! -d "$g" ]; then echo ".git is not a plain directory"; return 1; fi
    if [ -e "$g/commondir" ]; then echo ".git/commondir points git at another directory"; return 1; fi
    if [ -L "$g/config" ]; then echo ".git/config is a symlink"; return 1; fi
    return 0
}

git_config_unexpected() {    # git_config_unexpected REPO — prints keys outside the list; fails if unreadable
    local c="$1/.git/config"
    [ -e "$1/.git" ] || return 0   # no clone yet
    git_dir_plain "$1" || return 1
    git config --file "$c" --list --name-only 2>/dev/null | tr 'A-Z' 'a-z' | sort -u | grep -vE "$GIT_CONFIG_ALLOWED"
    [ "${PIPESTATUS[0]}" -eq 0 ] || { echo "(unreadable .git/config)"; return 1; }
    return 0
}

git_config_fingerprint() {    # git_config_fingerprint REPO — prints a hash, or fails if unreadable
    local c="$1/.git/config"
    [ -f "$c" ] && [ -r "$c" ] || return 1
    sha256sum < "$c" | cut -d' ' -f1
}

git_config_check() {    # git_config_check REPO WANT — 0 when unchanged; else prints why and fails
    local repo=$1 want=$2 got
    if [ -z "$want" ]; then
        echo "the clone's .git/config could not be read before the agent ran, so it cannot be shown unchanged — nothing is pushed"
        return 1
    fi
    local why
    why=$(git_dir_plain "$repo") || { echo "the clone's $why — refused, nothing is pushed"; return 1; }
    got=$(git_config_fingerprint "$repo") || { echo "the clone's .git/config could not be read, so it cannot be shown unchanged — nothing is pushed"; return 1; }
    if [ "$got" != "$want" ]; then
        echo "the job changed the clone's .git/config, which can make git run a command for it outside the sandbox — refused, nothing is pushed"
        return 1
    fi
    if grep -qiE '^[[:space:]]*\[[[:space:]]*include' "$repo/.git/config"; then
        echo "the clone's .git/config includes another file, whose contents a job could change — refused, nothing is pushed"
        return 1
    fi
    if [ -e "$repo/.git/config.worktree" ]; then
        echo "the clone has a .git/config.worktree, which git reads too — refused, nothing is pushed"
        return 1
    fi
    return 0
}

pre_push_in_sandbox() {    # pre_push_in_sandbox REPO REMOTE_NAME REMOTE_URL REFSPEC [MAINLINE_SHA] — 0 if no hook or it passes
    local repo=$1 name=$2 url=$3 refspec=$4 main=${5:-} lsha rref rsha hooks hook real rel run
    # Where git itself would look: core.hooksPath from the clone's own config (Viatica's `npm install`
    # sets .githooks), else .git/hooks. Only a path inside the clone is run.
    hooks=$(git config --file "$repo/.git/config" core.hooksPath 2>/dev/null) || hooks=.git/hooks
    case "$hooks" in /*|*..*) echo "pre-push: core.hooksPath '$hooks' points outside the clone — refused"; return 1 ;; esac
    hook="$hooks/pre-push"
    # THE MAINLINE'S COPY, when the hook is a tracked file (review, 2026-11-04). The branch's copy is
    # Knight's to edit or delete, and a deleted hook used to mean a skipped guard. moses-framework's is
    # a symlink to tools/pre-push.sh; Viatica's is .githooks/pre-push. Honest limit: what the hook calls
    # (tools/scan-secrets.sh) is still the branch's copy — the gate's own scan is from the same tree.
    real=$(realpath -m "$repo/$hook"); rel=${real#"$repo"/}
    if [ -n "$main" ] && [ "$rel" != "$real" ] && git -C "$repo" cat-file -e "$main:$rel" 2>/dev/null; then
        git -C "$repo" show "$main:$rel" > "$repo/.git/knight-pre-push" && chmod +x "$repo/.git/knight-pre-push" \
            || { echo "pre-push: could not write the mainline's copy of $rel"; return 1; }
        run=./.git/knight-pre-push
    elif [ -e "$repo/$hook" ]; then
        run="./$hook"
    else
        return 0
    fi
    lsha=$(git -C "$repo" rev-parse --verify -q "${refspec%%:*}^{commit}") || { echo "pre-push: cannot resolve ${refspec%%:*}"; return 1; }
    rref=${refspec#*:}; case "$rref" in refs/*) ;; *) rref="refs/heads/$rref" ;; esac
    rsha=$(git -C "$repo" ls-remote "$name" "$rref" 2>/dev/null | awk 'NR==1{print $1}')
    [ -n "$rsha" ] || rsha=0000000000000000000000000000000000000000
    printf '%s %s %s %s\n' "${refspec%%:*}" "$lsha" "$rref" "$rsha" \
        | sandbox_run gate "$repo" "$(printf '%q %q %q' "$run" "$name" "$url")"
}
