#!/usr/bin/env bash
# install-layer4.sh — everything from this build that needs root, in one pass.
#
#   sudo $OP_HOME/scripts/install-layer4.sh
#
# Three things, all of which need privilege for the same reason: they live in root-owned paths or
# touch root-only credentials.
#
#   1. THERAPIST — health persona on the roster; Birdeye hands over the Customs section.
#   2. RAILWAY-READ — root-owned read-only wrapper, so "is that variable set on Customs?" stops
#      being a question for Brad. The token stays in a file the agent cannot read; the wrapper is
#      the only path to it and refuses mutations. That is the boundary — not the script's good
#      intentions.
#   3. SUDOERS — one NOPASSWD line for `railway-read` only, added to the existing allowlist.
#
# Idempotent, backs up everything it touches, and validates the sudoers file with `visudo -c`
# BEFORE installing it. A malformed sudoers file locks you out of sudo entirely, so it is written to
# a temp path, checked, and only then moved into place.

set -euo pipefail

# THE OPERATOR'S HOME, resolved rather than named — and correct under sudo, where $HOME is root's.
# That distinction is the whole reason this is not just "$HOME": these scripts are run with sudo,
# and a root-relative path would install into /root and appear to work.
OP_HOME=${MOSES_OP_HOME:-$(getent passwd "${SUDO_USER:-$(id -un)}" | cut -d: -f6)}
[ -n "$OP_HOME" ] || { echo "cannot resolve the operator's home; set MOSES_OP_HOME" >&2; exit 1; }
MOSES_ROOT=${MOSES_ROOT:-$OP_HOME/Projects/moses}

[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }

STAMP=$(date +%Y%m%d-%H%M%S)
SUDOERS=/etc/sudoers.d/moses-installers
RAILWAY_ENV=/etc/moses/railway.env

# ── 1. Therapist ────────────────────────────────────────────────────────────
echo "── Therapist ───────────────────────────────────────────"
$OP_HOME/scripts/install-health-report.sh
echo

# ── 2. railway-read ─────────────────────────────────────────────────────────
echo "── railway-read ────────────────────────────────────────"
install -m 0755 -o root -g root $OP_HOME/scripts/railway-read /usr/local/bin/railway-read
echo "installed  /usr/local/bin/railway-read (root-owned, not writable by brad)"

if [ ! -e "$RAILWAY_ENV" ]; then
  install -d -m 0755 -o root -g root /etc/moses
  cat > "$RAILWAY_ENV" <<'TEMPLATE'
# Railway credentials for railway-read. ROOT-ONLY BY DESIGN — the agent must not be able to read
# this file, because Railway has no read-only token type and this token can also write variables
# and trigger deploys. Its inaccessibility is what makes the wrapper a boundary rather than a
# convention.
#
# Fill these in from the Railway dashboard, then re-run this installer to verify:
#   RAILWAY_TOKEN           a PROJECT token (Project Settings -> Tokens), scoped to Customs
#   RAILWAY_PROJECT_ID      from the project URL
#   RAILWAY_ENVIRONMENT_ID  the production environment
#   RAILWAY_SERVICE_ID      the Customs service
#
# RAILWAY_TOKEN_KIND decides which header is sent, and Railway is strict about it:
#   project   -> Project-Access-Token   (a token made in Project Settings -> Tokens)
#   account   -> Authorization: Bearer  (a personal or workspace token)
# A mismatch fails as a bare "Not Authorized", which reads like a bad token rather than a wrong
# header. Leave it as project unless you deliberately made an account token.
RAILWAY_TOKEN_KIND=project
RAILWAY_TOKEN=
RAILWAY_PROJECT_ID=
RAILWAY_ENVIRONMENT_ID=
RAILWAY_SERVICE_ID=
TEMPLATE
  chmod 600 "$RAILWAY_ENV"; chown root:root "$RAILWAY_ENV"
  echo "created    $RAILWAY_ENV (0600 root) — template, needs values"
else
  chmod 600 "$RAILWAY_ENV"; chown root:root "$RAILWAY_ENV"
  echo "exists     $RAILWAY_ENV (permissions reasserted 0600 root)"
fi
echo

# ── 3. sudoers ──────────────────────────────────────────────────────────────
echo "── sudoers ─────────────────────────────────────────────"
LINE="brad ALL=(root) NOPASSWD: /usr/local/bin/railway-read"
if [ -f "$SUDOERS" ] && grep -qF "/usr/local/bin/railway-read" "$SUDOERS"; then
  echo "sudoers    railway-read already allowed — no change"
else
  cp -a "$SUDOERS" "$SUDOERS.bak-$STAMP" 2>/dev/null && echo "backed up  $SUDOERS.bak-$STAMP"
  TMP=$(mktemp)
  { [ -f "$SUDOERS" ] && cat "$SUDOERS"; printf '%s\n' "$LINE"; } > "$TMP"
  # Validate BEFORE installing. A broken sudoers file means no sudo at all, on a headless box.
  if visudo -cf "$TMP" >/dev/null 2>&1; then
    install -m 0440 -o root -g root "$TMP" "$SUDOERS"
    echo "sudoers    railway-read allowed NOPASSWD (validated with visudo)"
  else
    echo "SUDOERS WOULD BE INVALID — not installing. Existing file untouched." >&2
    visudo -cf "$TMP" 2>&1 | sed 's/^/  /' >&2
    rm -f "$TMP"; exit 1
  fi
  rm -f "$TMP"
fi
echo

# ── Verify, as brad, that the boundary actually holds ───────────────────────
echo "── Boundary check (run AS BRAD, which is what matters) ──"
sudo -u brad bash -c '
  printf "  token file readable by brad? "
  if [ -r /etc/moses/railway.env ]; then echo "YES — THE BOUNDARY IS BROKEN"; else echo "no (correct)"; fi
  printf "  wrapper writable by brad?    "
  if [ -w /usr/local/bin/railway-read ]; then echo "YES — THE BOUNDARY IS BROKEN"; else echo "no (correct)"; fi
  printf "  unknown op refused?          "
  sudo -n /usr/local/bin/railway-read destroy 2>&1 | grep -q "unknown operation" && echo "yes (correct)" || echo "CHECK MANUALLY"
'
echo
if grep -q '^RAILWAY_TOKEN=$' "$RAILWAY_ENV" 2>/dev/null; then
  echo "Therapist is live. railway-read is installed but has no token yet —"
  echo "fill in $RAILWAY_ENV and re-run this to verify."
else
  echo "Done. Try:  sudo railway-read vars"
fi
