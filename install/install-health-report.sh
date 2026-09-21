#!/usr/bin/env bash
# install-health-report.sh — put Therapist on the roster and take health off Birdeye's plate.
#
#   sudo $OP_HOME/scripts/install-health-report.sh
#
# WHAT CHANGES (Brad, 2026-08-13):
#   1. `/usr/local/bin/health-report` — health and expiry, plus one door to Knight's readiness.
#   2. Roster entry, so the 08:00 standup calls her and the overdue check holds her accountable.
#      The ROSTER is what makes a persona real here; a script nobody is declared to run is a script
#      that stops running and nobody notices.
#   3. Birdeye's report loses its Customs section. "The nightly backup failed" and "Stripe is
#      refusing our key" are different emergencies and should not share a heading.
#
# Idempotent. Backs up both files. Restores on syntax or JSON error.

set -euo pipefail

# THE OPERATOR'S HOME, resolved rather than named — and correct under sudo, where $HOME is root's.
# That distinction is the whole reason this is not just "$HOME": these scripts are run with sudo,
# and a root-relative path would install into /root and appear to work.
OP_HOME=${MOSES_OP_HOME:-$(getent passwd "${SUDO_USER:-$(id -un)}" | cut -d: -f6)}
[ -n "$OP_HOME" ] || { echo "cannot resolve the operator's home; set MOSES_OP_HOME" >&2; exit 1; }
MOSES_ROOT=${MOSES_ROOT:-$OP_HOME/Projects/moses}

[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }

SRC=$OP_HOME/scripts/health-report
DST=/usr/local/bin/health-report
REPORT=/usr/local/bin/ops-report
ROSTER=/etc/moses/roster.json
STAMP=$(date +%Y%m%d-%H%M%S)

[ -r "$SRC" ]    || { echo "missing $SRC" >&2; exit 1; }
[ -r "$REPORT" ] || { echo "missing $REPORT" >&2; exit 1; }
[ -r "$ROSTER" ] || { echo "missing $ROSTER" >&2; exit 1; }

cp -a "$REPORT" "$REPORT.bak-$STAMP"; echo "backed up  $REPORT.bak-$STAMP"
cp -a "$ROSTER" "$ROSTER.bak-$STAMP"; echo "backed up  $ROSTER.bak-$STAMP"

install -m 0755 -o root -g root "$SRC" "$DST"
echo "installed  $DST"

# ── 1. Roster ───────────────────────────────────────────────────────────────
python3 - "$ROSTER" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
people = d.setdefault("personas", [])
if any(x.get("id") == "health-report" for x in people):
    print("roster     health-report already present — no change")
    raise SystemExit

entry = {
    "id": "health",
    "name": "Therapist",
    "emoji": ":adhesive_bandage:",
    "runs_on": "Reserve",
    "title": "Health — what could stop working",
    "charter": ("Everything that can expire, run out, or quietly stop answering: Customs "
                "reachability, TLS certificates, the domain registration, and the dependency canary "
                "(database, Stripe, Anthropic key and credit, email sending domain, Maps). Also "
                "reports Knight's readiness, so there is one door to 'is everything OK'."),
    "aoe": {
        "owns": [
            "Customs reachable, TLS certs, domain registration expiry",
            "the dependency canary — real calls to every credential Viatica needs",
            "prepaid credit exhaustion and grant expiry surfacing",
            "Knight readiness, reported (not owned — Knight owns his own preflight)",
        ],
        "does_not_own": [
            "backups, iDrive quota, disks, handed-off jobs — Birdeye's ground",
            "the money itself — Big Pipe reports what it costs; she reports whether it still works",
            "fixing anything: she reports, she does not remediate",
        ],
    },
    "check": {"type": "command", "argv": ["/usr/local/bin/health-report", "morning"]},
    "cadence_hours": 24,
    "kind": "job",
    "status": "deterministic - no model calls",
}
# Before Knight, so the briefing reads health → readiness → work.
idx = next((i for i, x in enumerate(people) if x.get("id") == "knight"), len(people))
people.insert(idx, entry)
json.dump(d, open(p, "w"), indent=2, ensure_ascii=False)
open(p, "a").write("\n")
print("roster     Therapist added (kind=job, cadence 24h)")
PY

# ── 2. Birdeye hands over the Customs section ───────────────────────────────
python3 - "$REPORT" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()

if "build_customs; build_signups" not in s:
    print("report     already handed over — no change")
    raise SystemExit

# Stop calling it. The function and its helper stay in the file, unused and harmless, so this is a
# one-line revert if the split turns out wrong.
s = s.replace("build_all() { build_customs; build_signups;",
              "build_all() { build_signups;", 1)

# Drop the Customs heading from `full`; signups stay (Viatica data, but Brad reads them as growth,
# not health).
# The heading only earns its place when there is something under it. An empty "Viatica" header is
# the report telling you it has a section it forgot to fill.
s = s.replace("""    MSG=$(printf '🛬 *Customs — Viatica*\\n\\n%s' "$CUSTOMS_TEXT")
    [ -n "$SIGNUP_TEXT" ] && MSG="$MSG

$SIGNUP_TEXT\"""",
"""    MSG=""
    [ -n "$SIGNUP_TEXT" ] && MSG=$(printf '🛬 *Viatica*\\n\\n%s' "$SIGNUP_TEXT")""", 1)

s = s.replace("issues=$(( CUSTOMS_ISSUE + BACKUP_ISSUE", "issues=$(( BACKUP_ISSUE", 1)

s = s.replace('''      MSG="✅ *Customs* — all clear (${CUSTOMS_ONELINE:-checked})"
      [ -n "$SIGNUP_ONELINE" ] && MSG="$MSG · ${SIGNUP_ONELINE}"
      MSG="$MSG
✅ *Reserve* — all clear''',
'''      MSG="✅ *Reserve* — all clear''', 1)

s = s.replace('''      [ "$CUSTOMS_ISSUE" -eq 1 ] && MSG="$MSG

🛬 *Customs*
$CUSTOMS_TEXT"
''', '', 1)

open(p, "w").write(s)
print("report     Customs section handed to Therapist")
PY

bash -n "$REPORT" || { echo "SYNTAX ERROR — restoring" >&2; cp -a "$REPORT.bak-$STAMP" "$REPORT"; exit 1; }
jq -e . "$ROSTER" >/dev/null || { echo "ROSTER NOT VALID JSON — restoring" >&2; cp -a "$ROSTER.bak-$STAMP" "$ROSTER"; exit 1; }

echo
echo "Therapist, exception-only (what 08:00 would post):"
echo "────────────────────────────────────────────"
"$DST" morning --print 2>&1 | sed 's/^/  /'
echo "────────────────────────────────────────────"
echo
echo "Birdeye, after handover:"
echo "────────────────────────────────────────────"
"$REPORT" morning --print 2>&1 | sed 's/^/  /' | head -12
echo "────────────────────────────────────────────"
echo
echo "Done. The 08:00 standup calls Therapist from the roster; nothing else to schedule."
