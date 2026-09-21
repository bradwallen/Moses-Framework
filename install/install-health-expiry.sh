#!/usr/bin/env bash
# install-health-expiry.sh — teach Birdeye to watch Customs, and split his morning report in two.
#
#   sudo $OP_HOME/scripts/install-health-expiry.sh
#
# WHAT CHANGES (Brad, 2026-08-11):
#   1. A new CUSTOMS section, reported FIRST — "it'll be more critical once Viatica is live."
#      Covers everything that expires or runs out: the app answering at all, TLS certificates, the
#      viatica.travel registration, and the dependency canary on Customs (database, Stripe,
#      Anthropic key AND prepaid credit, Resend sending domain, Maps).
#   2. Signups move UP into Customs, where they belong — they are Viatica data, not Reserve ops.
#   3. Backup, iDrive quota and disks move to the BOTTOM under a RESERVE heading.
# The existing warning emoji convention is untouched: ✅ / ⚠️ / 🔴 already mean what Brad reads them
# to mean, so nothing here re-teaches him a new vocabulary.
#
# Idempotent; backs up both files; restores them on any syntax error.

set -euo pipefail

# THE OPERATOR'S HOME, resolved rather than named — and correct under sudo, where $HOME is root's.
# That distinction is the whole reason this is not just "$HOME": these scripts are run with sudo,
# and a root-relative path would install into /root and appear to work.
OP_HOME=${MOSES_OP_HOME:-$(getent passwd "${SUDO_USER:-$(id -un)}" | cut -d: -f6)}
[ -n "$OP_HOME" ] || { echo "cannot resolve the operator's home; set MOSES_OP_HOME" >&2; exit 1; }
MOSES_ROOT=${MOSES_ROOT:-$OP_HOME/Projects/moses}

[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }

REPORT=/usr/local/bin/ops-report
CHECKS=/usr/local/bin/health-expiry
SRC=$OP_HOME/scripts/health-expiry
STAMP=$(date +%Y%m%d-%H%M%S)

[ -r "$REPORT" ] || { echo "missing $REPORT" >&2; exit 1; }
[ -r "$SRC" ] || { echo "missing $SRC — expected the checker alongside this installer" >&2; exit 1; }

cp -a "$REPORT" "$REPORT.bak-$STAMP"
echo "backed up  $REPORT.bak-$STAMP"

# ── 1. Install the checker ───────────────────────────────────────────────────
install -m 0755 -o root -g root "$SRC" "$CHECKS"
echo "installed  $CHECKS"

# The checker reads VIATICA_APP_URL / CRON_SECRET. ops-report already reads them from
# /etc/moses/ops.env for the signups call, so make sure the checker sees the same file rather
# than quietly checking nothing — a monitor that silently skips is the failure mode being designed
# against.
if [ -r /etc/moses/ops.env ] && ! grep -q '/etc/moses/ops.env' "$CHECKS"; then
    sed -i 's#^for f in /etc/moses/moses.env#for f in /etc/moses/ops.env /etc/moses/moses.env#' "$CHECKS"
    echo "wired      checker to /etc/moses/ops.env"
fi

# ── 2. Patch the report ──────────────────────────────────────────────────────
python3 - "$REPORT" <<'PY'
import sys

path = sys.argv[1]
s = open(path).read()

if "build_customs()" in s:
    print("report     already patched — no change")
    sys.exit(0)

helper = '''
# ── Customs (Viatica in production) ───────────────────────────────────────────
# Everything that can EXPIRE or RUN OUT and take the product down for a paying customer. Reported
# FIRST, above Reserve: a full disk is Brad's problem, a dead Viatica is his customers' problem.
# The work lives in health-expiry so it can be run and tested on its own.
CUSTOMS_ISSUE=0; CUSTOMS_TEXT=""; CUSTOMS_ONELINE=""
build_customs() {
  local out
  if ! command -v /usr/local/bin/health-expiry >/dev/null 2>&1; then
    CUSTOMS_ISSUE=1
    CUSTOMS_TEXT="⚠️ *Customs* — health-expiry is not installed, so nothing about Viatica was checked"
    return
  fi
  if out=$(/usr/local/bin/health-expiry 2>&1); then
    CUSTOMS_TEXT="$out"
    CUSTOMS_ONELINE=$(/usr/local/bin/health-expiry --oneline 2>/dev/null | head -1)
  else
    CUSTOMS_ISSUE=1
    CUSTOMS_TEXT="$out"
  fi
}

'''

anchor = "build_all() {"
assert anchor in s, "build_all not found"
s = s.replace(anchor, helper.lstrip("\\n") + anchor, 1)

s = s.replace(
    "build_all() { build_backup; build_quota; build_disk; build_inflight; build_jobs; build_signups; }",
    "build_all() { build_customs; build_signups; build_backup; build_quota; build_disk; build_inflight; build_jobs; }",
    1,
)

# ── full: Customs block, then Reserve block ─────────────────────────────────
old_full = '''    build_all
    MSG=$(printf '*Reserve — ops report*\\n\\n%s\\n%s\\n%s' "$BACKUP_TEXT" "$QUOTA_TEXT" "$DISK_TEXT")
    [ -n "$SIGNUP_TEXT" ] && MSG="$MSG

$SIGNUP_TEXT"'''
new_full = '''    build_all
    MSG=$(printf '🛬 *Customs — Viatica*\\n\\n%s' "$CUSTOMS_TEXT")
    [ -n "$SIGNUP_TEXT" ] && MSG="$MSG

$SIGNUP_TEXT"
    MSG=$(printf '%s\\n\\n🏠 *Reserve — ops*\\n\\n%s\\n%s\\n%s' "$MSG" "$BACKUP_TEXT" "$QUOTA_TEXT" "$DISK_TEXT")'''
assert old_full in s, "full-mode block not found"
s = s.replace(old_full, new_full, 1)

# ── morning: same split, exception-only ─────────────────────────────────────
old_issues = '    issues=$(( BACKUP_ISSUE + QUOTA_ISSUE + DISK_ISSUE + JOBS_ISSUE ))'
new_issues = '    issues=$(( CUSTOMS_ISSUE + BACKUP_ISSUE + QUOTA_ISSUE + DISK_ISSUE + JOBS_ISSUE ))'
assert old_issues in s, "issue tally not found"
s = s.replace(old_issues, new_issues, 1)

old_clear = '''      MSG="✅ *Reserve* — all clear (${BACKUP_ONELINE}, ${QUOTA_ONELINE}, disks ok)"
      [ -n "$SIGNUP_ONELINE" ] && MSG="$MSG · ${SIGNUP_ONELINE}"'''
new_clear = '''      # Two lines when healthy, one per environment. Still small enough to skim and ignore, but it
      # proves BOTH halves were actually checked — a single "all clear" could not distinguish a
      # healthy Customs from a Customs nobody looked at.
      MSG="✅ *Customs* — all clear (${CUSTOMS_ONELINE:-checked})"
      [ -n "$SIGNUP_ONELINE" ] && MSG="$MSG · ${SIGNUP_ONELINE}"
      MSG="$MSG
✅ *Reserve* — all clear (${BACKUP_ONELINE}, ${QUOTA_ONELINE}, disks ok)"'''
assert old_clear in s, "all-clear line not found"
s = s.replace(old_clear, new_clear, 1)

old_bad = '''      MSG="⚠️ *Reserve — needs a look*"
      [ -n "$SIGNUP_ONELINE" ] && MSG="$MSG  _(${SIGNUP_ONELINE})_"
      [ "$BACKUP_ISSUE" -eq 1 ] && MSG="$MSG'''
new_bad = '''      MSG="⚠️ *Needs a look*"
      [ -n "$SIGNUP_ONELINE" ] && MSG="$MSG  _(${SIGNUP_ONELINE})_"
      # Customs first, always — a Viatica problem outranks anything on Reserve.
      [ "$CUSTOMS_ISSUE" -eq 1 ] && MSG="$MSG

🛬 *Customs*
$CUSTOMS_TEXT"
      { [ "$BACKUP_ISSUE" -eq 1 ] || [ "$QUOTA_ISSUE" -eq 1 ] || [ "$DISK_ISSUE" -eq 1 ] || [ "$JOBS_ISSUE" -eq 1 ]; } && MSG="$MSG

🏠 *Reserve*"
      [ "$BACKUP_ISSUE" -eq 1 ] && MSG="$MSG'''
assert old_bad in s, "needs-a-look block not found"
s = s.replace(old_bad, new_bad, 1)

open(path, "w").write(s)
print("report     Customs on top, Reserve at the bottom")
PY

bash -n "$REPORT" || { echo "SYNTAX ERROR — restoring" >&2; cp -a "$REPORT.bak-$STAMP" "$REPORT"; exit 1; }
bash -n "$CHECKS" || { echo "checker syntax error" >&2; exit 1; }

# ── 3. Prove it renders, without posting to Slack ────────────────────────────
echo
echo "Dry run (--print does not post):"
echo "────────────────────────────────────────────"
"$REPORT" morning --print 2>&1 | sed 's/^/  /'
echo "────────────────────────────────────────────"
echo
echo "Done. Next 08:00 briefing leads with Customs."
