#!/usr/bin/env bash
# install-deliverability-check.sh — teach Birdeye/Therapist to notice when our mail stops arriving.
#
#   sudo ${MOSES_FRAMEWORK:-$OP_HOME/Projects/moses-framework}/install/install-deliverability-check.sh
#
# WHAT CHANGES
#   /usr/local/bin/health-expiry gains check_email: it asks Customs for Resend's own delivered /
#   bounced / complained figures over 24h and reports one of THREE states — mail flowing, NO MAIL
#   SENT, or could not look. Nothing else in the morning report moves.
#
# WHY ROOT: /usr/local/bin is root-owned and the morning job runs as root. That is the mechanism, not
# an inconvenience — a checker brad could rewrite is not a checker root should trust.
#
# It refuses on a red test suite, restores the previous copy on any syntax error, and then RUNS the
# real checker so the result is observed rather than declared.
set -uo pipefail

# THE OPERATOR'S HOME, resolved rather than named — and correct under sudo, where $HOME is root's.
# That distinction is the whole reason this is not just "$HOME": these scripts are run with sudo,
# and a root-relative path would install into /root and appear to work.
OP_HOME=${MOSES_OP_HOME:-$(getent passwd "${SUDO_USER:-$(id -un)}" | cut -d: -f6)}
[ -n "$OP_HOME" ] || { echo "cannot resolve the operator's home; set MOSES_OP_HOME" >&2; exit 1; }
MOSES_ROOT=${MOSES_ROOT:-$OP_HOME/Projects/moses}

[ "$(id -u)" = 0 ] || { echo "install-deliverability-check: run me with sudo." >&2; exit 1; }

HERE=$(cd "$(dirname "$0")/.." && pwd)
SRC=$HERE/personas/health-expiry
DEST=/usr/local/bin/health-expiry
STAMP=$(date +%Y%m%d-%H%M%S)

step() { printf '\n== %s\n' "$*"; }
die()  { echo "install-deliverability-check: $*" >&2; exit 1; }

[ -r "$SRC" ] || die "no source at $SRC"

step "Tests first"
out=$(sudo -u "${SUDO_USER:-$(id -un)}" python3 "$HERE/personas/deliverability_test.py" 2>&1) || {
  echo "$out" | tail -20; die "the probe tests are RED. Nothing was installed."; }
echo "  ok  personas/deliverability_test.py"

step "Backing up what is there now"
[ -f "$DEST" ] && { cp -a "$DEST" "$DEST.bak-$STAMP" || die "could not back up $DEST"
                    echo "  $DEST.bak-$STAMP"; }

step "Installing"
install -m 0755 -o root -g root "$SRC" "$DEST" || die "could not install $DEST"
bash -n "$DEST" || { echo "SYNTAX ERROR — restoring" >&2
                     [ -f "$DEST.bak-$STAMP" ] && cp -a "$DEST.bak-$STAMP" "$DEST"
                     die "restored the previous copy"; }
echo "  $DEST"

step "Verifying the CHANGE LANDED, not that install exited zero"
grep -q "check_email" "$DEST" || die "the installed copy has no check_email — old file still in place?"
echo "  ok  check_email is present in the installed copy"

# THE REAL RUN. A checker that has never been executed against the real Customs is a claim; this is
# the only step that can tell the difference between "installed" and "working".
step "Running the real checker (this makes a live call to Customs)"
"$DEST" --oneline; rc=$?
echo "  (exit $rc — 1 just means something wants a look, which is the point of it)"

cat <<'DONE'

== Done.

The morning report now carries a mail line. Read it like this:

  "mail 412 sent, 99.5% delivered"  — flowing.
  "NO MAIL 24h"                     — nothing left the building in a day. Magic-link login goes out
                                      this way, and a user who never gets one cannot tell you.
  "not checked"                     — the probe could NOT look. This is not an all-clear.

If it says "not checked", the reason is almost certainly the API key: a send-scoped Resend key cannot
read metrics, and Resend does not allow a key's permission to be edited after it is created.
DONE
