#!/usr/bin/env bash
# install-probe-coverage.sh — make the personas' probes state what they actually looked at.
#
#   sudo $OP_HOME/scripts/install-probe-coverage.sh
#
# ATLAS'S DURABLE FIX (2026-08-14, via Brad — "implement Atlas's suggestion"):
#
#   "the root bug wasn't the identity, it was the probe collapsing 'I couldn't look' into 'it's
#    broken'. Fix the identity and it reads true today; the day brad's key rotates or HOME moves, it
#    lies again the same way, because a null result still renders as a negative one. The durable
#    version is the probe emitting 'checked as <identity>, scope <x>'."
#
# THE LIVE LIE THIS REMOVES. `health-expiry --oneline` builds its summary from APP_OK, which is 0
# both when the site is down AND when VIATICA_APP_URL could not be read. Run as brad — who cannot
# read the root-only env — it prints "UNREACHABLE" while the site returns HTTP 200. Three people
# looking at that line would all conclude the same wrong thing.
#
# The fix separates SCOPE from RESULT: a check that never ran says so, in its own words, and never
# borrows the vocabulary of a failure.
#
# Idempotent, backs up both files, and verifies the behavior both ways BEFORE and AFTER.

set -uo pipefail

# THE OPERATOR'S HOME, resolved rather than named — and correct under sudo, where $HOME is root's.
# That distinction is the whole reason this is not just "$HOME": these scripts are run with sudo,
# and a root-relative path would install into /root and appear to work.
OP_HOME=${MOSES_OP_HOME:-$(getent passwd "${SUDO_USER:-$(id -un)}" | cut -d: -f6)}
[ -n "$OP_HOME" ] || { echo "cannot resolve the operator's home; set MOSES_OP_HOME" >&2; exit 1; }
MOSES_ROOT=${MOSES_ROOT:-$OP_HOME/Projects/moses}

[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }

STAMP=$(date +%Y%m%d-%H%M%S)
CUSTOMS=/usr/local/bin/health-expiry
THERAPIST=/usr/local/bin/health-report

echo "── Before ──────────────────────────────────────────────"
printf '  as brad (cannot read the app url) : '
sudo -u brad "$CUSTOMS" --oneline 2>/dev/null | head -1
printf '  as root (can)                     : '
"$CUSTOMS" --oneline 2>/dev/null | head -1
printf '  the site itself                   : '
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' --max-time 15 \
  "${MONITORED_APP_URL:?set MONITORED_APP_URL to the app this probe watches}" 2>/dev/null || echo "(no answer)"
echo "  ^ if the first two disagree, the first one is the lie this fixes."
echo

echo "── Patch health-expiry ───────────────────────────────"
cp -a "$CUSTOMS" "$CUSTOMS.bak-$STAMP" && echo "  backed up  $CUSTOMS.bak-$STAMP"
if grep -q 'APP_CHECKED' "$CUSTOMS"; then
  echo "  already patched — skipping"
else
  python3 - "$CUSTOMS" <<'PYEOF'
import sys
p = sys.argv[1]
s = open(p).read()

# 1. Declare the scope flag next to the other counters.
old = "CERT_WARN_DAYS=21"
new = """# SCOPE, tracked separately from RESULT. A check that could not run must never borrow the
# vocabulary of one that ran and failed — see Atlas, 2026-08-14: a null result rendering as a
# negative one is how "I couldn't look" becomes "it's broken".
APP_CHECKED=0

CERT_WARN_DAYS=21"""
assert old in s, "CERT_WARN_DAYS anchor missing"
s = s.replace(old, new, 1)

# 2. Record that the reachability check actually ran.
old = '''    code=$(curl -sSL -o /dev/null -w '%{http_code}' --max-time 25 "https://$host/" 2>/dev/null)'''
new = '''    APP_CHECKED=1
    code=$(curl -sSL -o /dev/null -w '%{http_code}' --max-time 25 "https://$host/" 2>/dev/null)'''
assert old in s, "curl anchor missing"
s = s.replace(old, new, 1)

# 3. The one-line summary must distinguish the three states, and say who it ran as.
old = '''    parts="reachable"
    [ "$APP_OK" -eq 1 ] || parts="UNREACHABLE"'''
new = '''    if [ "$APP_CHECKED" -eq 0 ]; then
        # NOT the same as unreachable. This is the probe reporting its own blindness, which is a
        # fact about the check and not about Viatica.
        parts="not checked (no app url readable as $(id -un))"
    elif [ "$APP_OK" -eq 1 ]; then
        parts="reachable"
    else
        parts="UNREACHABLE"
    fi'''
assert old in s, "oneline anchor missing"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("  patched    scope is now tracked and reported separately from result")
PYEOF
  [ $? -eq 0 ] || { echo "  PATCH FAILED — restoring"; cp -a "$CUSTOMS.bak-$STAMP" "$CUSTOMS"; exit 1; }
fi
bash -n "$CUSTOMS" || { echo "  SYNTAX ERROR — restoring" >&2; cp -a "$CUSTOMS.bak-$STAMP" "$CUSTOMS"; exit 1; }
echo "  syntax ok"
echo

echo "── Patch health-report to state its coverage ───────────────"
cp -a "$THERAPIST" "$THERAPIST.bak-$STAMP" && echo "  backed up  $THERAPIST.bak-$STAMP"
if grep -q 'checked as' "$THERAPIST"; then
  echo "  already patched — skipping"
else
  python3 - "$THERAPIST" <<'PYEOF'
import sys
p = sys.argv[1]
s = open(p).read()
old = '''  if out=$("$CUSTOMS" 2>&1); then
    CUSTOMS_TEXT="$out"'''
new = '''  if out=$("$CUSTOMS" 2>&1); then
    # Say what was actually looked at, and as whom. A verdict without its coverage is
    # unfalsifiable: "nothing wrong" and "I never looked" read identically.
    CUSTOMS_TEXT="$out
_checked as $(id -un) · scope: Customs reachability, deps, certs, domain_"'''
assert old in s, "customs branch anchor missing"
s = s.replace(old, new, 1)

old = '''  KNIGHT_ONELINE=$(printf '%s\\n' "$out" | grep -E 'jobs today' | sed 's/[[:space:]]\\+/ /g' | sed 's/^ *//')'''
new = '''  KNIGHT_TEXT="$KNIGHT_TEXT
_checked as brad (the user who owns the clone and does the pushing) · scope: Knight preflight_"
  KNIGHT_ONELINE=$(printf '%s\\n' "$out" | grep -E 'jobs today' | sed 's/[[:space:]]\\+/ /g' | sed 's/^ *//')'''
assert old in s, "knight oneline anchor missing"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("  patched    health and readiness now state their coverage")
PYEOF
  [ $? -eq 0 ] || { echo "  PATCH FAILED — restoring"; cp -a "$THERAPIST.bak-$STAMP" "$THERAPIST"; exit 1; }
fi
bash -n "$THERAPIST" || { echo "  SYNTAX ERROR — restoring" >&2; cp -a "$THERAPIST.bak-$STAMP" "$THERAPIST"; exit 1; }
echo "  syntax ok"
echo

echo "── After ───────────────────────────────────────────────"
printf '  as brad (still cannot read the url): '
BRADLINE=$(sudo -u brad "$CUSTOMS" --oneline 2>/dev/null | head -1); echo "$BRADLINE"
printf '  as root (can)                     : '
"$CUSTOMS" --oneline 2>/dev/null | head -1
echo
if printf '%s' "$BRADLINE" | grep -qi 'not checked'; then
  echo "  ✅ A blind probe now says it is blind instead of claiming the site is down."
else
  echo "  ⚠️  The brad-side line still does not say 'not checked' — look before trusting it." >&2
fi
echo
echo "  Therapist's report now carries a coverage line:"
"$THERAPIST" full --print 2>/dev/null | grep -i 'checked as' | sed 's/^/    /' || echo "    (none found — check by hand)"
