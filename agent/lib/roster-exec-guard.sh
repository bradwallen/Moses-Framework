# roster-exec-guard.sh — refuse to run anything the roster names that a non-root user could rewrite.
#
# Sourced by the two root-run scripts that execute a persona's declared check: the `moses` CLI (from
# the standup, as root) and `moses-remediate` (via sudo). ONE implementation, because two copies of a
# privilege check drift and only one of them gets fixed.
#
# WHY IT EXISTS. The roster declares `check.argv` and both callers execute it AS ROOT. That was safe
# only because /etc/moses/roster.json is root-owned. Moving the roster into the operator's home — so
# it can be edited without sudo, which is the whole point of Moses no longer running as root — would
# have turned "declare a persona" into "run anything as root".
#
# So the trust moves off the FILE and onto the TARGET: the roster may still choose which probe runs,
# but every candidate must already be a root-owned executable that no one else can rewrite. Naming
# /usr/local/bin/birdeye-report is allowed; naming a script in a home directory is not.
#
# This is strictly safer than before even without the move — a root-owned roster was trusted
# absolutely, and nothing checked what it pointed at.

# roster_argv_ok <path> — 0 if root may execute it, 1 otherwise (reason on stderr).
roster_argv_ok() {
    local p=$1 owner perms
    [ -n "$p" ] || { echo "roster: empty command" >&2; return 1; }
    case "$p" in
        /*) ;;
        *)  echo "roster: '$p' is not an absolute path — refusing to search PATH as root" >&2; return 1 ;;
    esac
    # A symlink can point anywhere, so judge what it RESOLVES to, not the name given.
    local real; real=$(readlink -f -- "$p" 2>/dev/null) || real=""
    [ -n "$real" ] && [ -f "$real" ] || { echo "roster: '$p' is not a regular file" >&2; return 1; }
    [ -x "$real" ] || { echo "roster: '$real' is not executable" >&2; return 1; }
    owner=$(stat -c '%U' "$real" 2>/dev/null)
    [ "$owner" = root ] || { echo "roster: '$real' is owned by $owner, not root — refusing to run it as root" >&2; return 1; }
    perms=$(stat -c '%a' "$real" 2>/dev/null)
    # Group- or other-writable means somebody who is not root can change what root runs.
    case "$perms" in *[2367]) echo "roster: '$real' is writable by others ($perms) — refusing" >&2; return 1 ;; esac
    # A writable DIRECTORY lets someone replace the file wholesale, so the parent matters too.
    local dir; dir=$(dirname -- "$real")
    local dperms; dperms=$(stat -c '%a' "$dir" 2>/dev/null)
    local downer; downer=$(stat -c '%U' "$dir" 2>/dev/null)
    [ "$downer" = root ] || { echo "roster: '$dir' is owned by $downer — its contents can be replaced" >&2; return 1; }
    case "$dperms" in *[2367]) echo "roster: '$dir' is writable by others ($dperms) — refusing" >&2; return 1 ;; esac
    return 0
}
