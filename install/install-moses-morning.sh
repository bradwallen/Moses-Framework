#!/usr/bin/env bash
# install-moses-morning.sh — install the updated morning briefing orchestrator.
#
#   sudo $OP_HOME/scripts/install-moses-morning.sh
#
# Only change: when Big Pipe or Tagilla fails, the Slack alert now names the REASON, not just the HTTP
# code. The endpoints (deployed to Customs) now answer 502 with an `error` field when their Slack post
# fails — previously they returned 200 no matter what, so either agent could go silent for good while
# this script reported a clean run.
#
# Nothing else is touched, and the briefing is not fired. Next run is tomorrow 08:00; use
# `sudo systemctl start moses-morning` to fire one now (it POSTS to Slack).

set -uo pipefail


# THE OPERATOR'S HOME, resolved rather than named — and correct under sudo, where $HOME is root's.
# That distinction is the whole reason this is not just "$HOME": these scripts are run with sudo,
# and a root-relative path would install into /root and appear to work.
OP_HOME=${MOSES_OP_HOME:-$(getent passwd "${SUDO_USER:-$(id -un)}" | cut -d: -f6)}
[ -n "$OP_HOME" ] || { echo "cannot resolve the operator's home; set MOSES_OP_HOME" >&2; exit 1; }
MOSES_ROOT=${MOSES_ROOT:-$OP_HOME/Projects/moses}

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }

NEW=$MOSES_ROOT/agent-morning.new
LIVE=/usr/local/bin/moses-morning
stamp=$(date +%Y%m%d-%H%M%S)

[ -r "$NEW" ] || { echo "!! $NEW is missing." >&2; exit 1; }
bash -n "$NEW" || { echo "!! $NEW does not parse — NOT installing." >&2; exit 1; }
command -v jq >/dev/null || echo "note: jq not found — the reason text will be blank, HTTP code still shown."

mkdir -p /root/ops-jobs-backups
cp -p "$LIVE" "/root/ops-jobs-backups/moses-morning.$stamp" \
    && echo "backed up  → /root/ops-jobs-backups/moses-morning.$stamp"

# Stage in the SAME directory, then mv — a rename swaps the inode instead of truncating a file that a
# running process may still be reading by byte offset.
cp "$NEW" "$LIVE.staged" && chmod 755 "$LIVE.staged" && chown root:root "$LIVE.staged" \
    && mv "$LIVE.staged" "$LIVE" && echo "installed  → $LIVE"

echo
echo "Timer unchanged:"
systemctl list-timers moses-morning.timer --no-pager 2>/dev/null | sed -n '1,3p'
