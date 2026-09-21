#!/usr/bin/env bash
# install-finance-periodic.sh — make ALL of Big Pipe's briefs fire at 08:00 Central.
#
#   sudo $OP_HOME/scripts/install-finance-periodic.sh
#
# THE PROBLEM (Brad, 2026-08-10): the daily lands at 08:00, but the weekly arrived at 09:48 on a
# Monday. The weekly/monthly/quarterly still run from GitHub Actions, whose scheduler is explicitly
# best-effort — its own docs say runs "can be delayed during periods of high load", 30-60 minutes is
# routine, and runs can be DROPPED. The daily was moved to Reserve for exactly this reason; these
# three were left behind. Cron there is also UTC, so "0 14 * * 1" is 09:00 in CDT and 08:00 in CST:
# retiming it to 13:00 would fix today and silently break in November.
#
# THE FIX: fire them from the same systemd timer as the daily (08:00 America/Chicago, DST-proof),
# and declare WHEN each is due in the ROSTER — not in a script. That rule is already load-bearing
# here: moses-morning once carried a hardcoded list of endpoints, so a persona dropped from the list
# stopped being asked and nothing noticed. A cadence hardcoded in a script has the same defect.
#
# Idempotent: re-running changes nothing. Backs up both files first.

set -euo pipefail

# THE OPERATOR'S HOME, resolved rather than named — and correct under sudo, where $HOME is root's.
# That distinction is the whole reason this is not just "$HOME": these scripts are run with sudo,
# and a root-relative path would install into /root and appear to work.
OP_HOME=${MOSES_OP_HOME:-$(getent passwd "${SUDO_USER:-$(id -un)}" | cut -d: -f6)}
[ -n "$OP_HOME" ] || { echo "cannot resolve the operator's home; set MOSES_OP_HOME" >&2; exit 1; }
MOSES_ROOT=${MOSES_ROOT:-$OP_HOME/Projects/moses}

[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }

MOSES=/usr/local/bin/moses
ROSTER=/etc/moses/roster.json
STAMP=$(date +%Y%m%d-%H%M%S)

for f in "$MOSES" "$ROSTER"; do
    [ -r "$f" ] || { echo "missing $f" >&2; exit 1; }
    cp -a "$f" "$f.bak-$STAMP"
done
echo "backed up  $MOSES.bak-$STAMP"
echo "backed up  $ROSTER.bak-$STAMP"

# ── 1. Teach the roster that Big Pipe owes more than a daily ─────────────────
python3 - "$ROSTER" <<'PY'
import json, sys

path = sys.argv[1]
d = json.load(open(path))
bp = next((p for p in d["personas"] if p["id"] == "finance"), None)
if bp is None:
    sys.exit("no finance entry in the roster")

# "when" is evaluated against the local date at standup time. Keep the vocabulary tiny and literal —
# a mini cron language here would be a second scheduler to debug.
periodic = [
    {"path": "/api/cron/cfo?period=weekly",    "when": "monday",           "label": "weekly"},
    {"path": "/api/cron/cfo?period=monthly",   "when": "first_of_month",   "label": "monthly"},
    {"path": "/api/cron/cfo?period=quarterly", "when": "first_of_quarter", "label": "quarterly"},
]
if bp.get("periodic") == periodic:
    print("roster     already current — no change")
else:
    bp["periodic"] = periodic
    json.dump(d, open(path, "w"), indent=2, ensure_ascii=False)
    open(path, "a").write("\n")
    print("roster     finance now declares weekly / monthly / quarterly")
PY

# ── 2. Teach the standup to fire what is due ─────────────────────────────────
python3 - "$MOSES" <<'PY'
import sys

path = sys.argv[1]
s = open(path).read()

if "run_periodic()" in s:
    print("moses      already patched — no change")
    sys.exit(0)

helper = '''
# ── Periodic reports ──────────────────────────────────────────────────────────
# Some personas owe more than a daily. The CADENCE IS DECLARED IN THE ROSTER (`periodic[]`), not
# here, for the same reason the daily list moved out of moses-morning: whatever holds the list
# becomes the real definition of the duty, and a script is not somewhere anyone thinks to look.
#
# These used to run from GitHub Actions, whose scheduler is best-effort — the weekly landed at 09:48
# on 2026-08-10 instead of 08:00, and GitHub reserves the right to drop a run entirely. Firing from
# the 08:00 America/Chicago timer makes the time real and survives DST, which a UTC cron cannot.
period_due() {
  case "$1" in
    monday)           [ "$(date +%u)" = "1" ] ;;
    first_of_month)   [ "$(date +%d)" = "01" ] ;;
    first_of_quarter) [ "$(date +%d)" = "01" ] && case "$(date +%m)" in 01|04|07|10) true ;; *) false ;; esac ;;
    daily)            true ;;
    *)                false ;;   # an unknown rule must never fire, and never silently pass either
  esac
}

# Fires every periodic report that is due today. Echoes one "label: detail" per FAILURE; silence
# means everything due was delivered. A period that is not due today is not a failure.
run_periodic() {
  local id=$1 count i path when label base code body why
  count=$(jq -r --arg i "$id" '.personas[] | select(.id==$i) | (.periodic // []) | length' "$REG" 2>/dev/null)
  [ -n "$count" ] && [ "$count" != "null" ] && [ "$count" -gt 0 ] || return 0

  if [ -z "$VIATICA_APP_URL" ] || [ -z "$CRON_SECRET" ]; then
    echo "periodic: VIATICA_APP_URL / CRON_SECRET not set"
    return 0
  fi
  base="${VIATICA_APP_URL%/}"; base="${base#http://}"; base="${base#https://}"; base="https://$base"

  i=0
  while [ "$i" -lt "$count" ]; do
    path=$(jq -r --arg i "$id" --argjson n "$i" '.personas[] | select(.id==$i) | .periodic[$n].path'  "$REG")
    when=$(jq -r --arg i "$id" --argjson n "$i" '.personas[] | select(.id==$i) | .periodic[$n].when'  "$REG")
    label=$(jq -r --arg i "$id" --argjson n "$i" '.personas[] | select(.id==$i) | .periodic[$n].label' "$REG")
    i=$(( i + 1 ))
    period_due "$when" || continue

    body=$(mktemp)
    code=$(curl -sSL -o "$body" -w '%{http_code}' --max-time 90 \
      -H "Authorization: Bearer $CRON_SECRET" "$base$path" 2>/dev/null)
    if [ "$code" = "200" ]; then rm -f "$body"; continue; fi
    # A 502 here carries the reason in `error` — the endpoints answer that way on purpose so a failed
    # Slack post travels with its cause instead of leaving an HTTP code to guess at.
    why=$(jq -r '.error // empty' "$body" 2>/dev/null); rm -f "$body"
    echo "$label: HTTP ${code:-000}${why:+ — $why}"
  done
}

'''

anchor = "cmd_standup() {"
assert anchor in s, "cmd_standup not found — file is not the shape this installer expects"
s = s.replace(anchor, helper.lstrip("\n") + anchor, 1)

# Call it inside the standup loop, right after the persona's daily check is recorded. A periodic
# report that fails is a standup failure like any other, named by cadence so the line says which one.
old = '''      record "$id" 0 "$detail"
      failures="$failures
   • *$name* — $detail"
    fi
  done'''
new = '''      record "$id" 0 "$detail"
      failures="$failures
   • *$name* — $detail"
    fi

    # Anything else this persona owes today (weekly/monthly/quarterly), fired at the same 08:00.
    while IFS= read -r pfail; do
      [ -n "$pfail" ] || continue
      failures="$failures
   • *$name* ($pfail)"
    done < <(run_periodic "$id")
  done'''
assert old in s, "standup loop not found — file is not the shape this installer expects"
s = s.replace(old, new, 1)

open(path, "w").write(s)
print("moses      standup now fires due periodic reports at 08:00")
PY

bash -n "$MOSES" || { echo "SYNTAX ERROR — restoring backup" >&2; cp -a "$MOSES.bak-$STAMP" "$MOSES"; exit 1; }
jq -e . "$ROSTER" >/dev/null || { echo "ROSTER NOT VALID JSON — restoring" >&2; cp -a "$ROSTER.bak-$STAMP" "$ROSTER"; exit 1; }

# ── 3. Prove it, don't assert it ─────────────────────────────────────────────
# Exercise the date rule against fixed dates rather than trusting it reads correctly. This is the
# part that decides whether a report fires at all, and it is wrong in a way nobody notices for a
# month or a quarter.
echo
echo "Verifying the schedule rule:"
fail=0
# Evaluate the same rule against FIXED dates. Whether a report fires at all rides on this, and it is
# wrong in a way nobody notices for a month or a quarter — the 1st of September is the first of a
# month but NOT the start of a quarter, and that distinction is one `case` away from silently
# sending the quarterly twelve times a year.
probe() {  # probe <YYYY-MM-DD> <rule> <expected yes|no>
    local d=$1 rule=$2 want=$3 got=no
    local u dd mm
    u=$(date -d "$d" +%u); dd=$(date -d "$d" +%d); mm=$(date -d "$d" +%m)
    case "$rule" in
        monday)           [ "$u" = "1" ] && got=yes ;;
        first_of_month)   [ "$dd" = "01" ] && got=yes ;;
        first_of_quarter) { [ "$dd" = "01" ] && case "$mm" in 01|04|07|10) true ;; *) false ;; esac; } && got=yes ;;
    esac
    if [ "$got" = "$want" ]; then
        printf '  PASS  %s %-16s -> %s\n' "$d" "$rule" "$got"
    else
        printf '  FAIL  %s %-16s -> %s (expected %s)\n' "$d" "$rule" "$got" "$want"; fail=1
    fi
}
probe 2026-08-10 monday           yes    # a Monday
probe 2026-08-11 monday           no     # a Tuesday
probe 2026-09-01 first_of_month   yes
probe 2026-09-02 first_of_month   no
probe 2026-10-01 first_of_quarter yes    # Oct 1 — quarter start
probe 2026-09-01 first_of_quarter no     # 1st of the month, NOT a quarter
probe 2027-01-01 first_of_quarter yes
[ "$fail" = 0 ] || { echo "  schedule rule is wrong — restoring backups" >&2; cp -a "$MOSES.bak-$STAMP" "$MOSES"; cp -a "$ROSTER.bak-$STAMP" "$ROSTER"; exit 1; }

echo
echo "Roster now declares:"
jq -r '.personas[] | select(.id=="finance") | .periodic[] | "  \(.label)  \(.when)  \(.path)"' "$ROSTER"
echo
echo "Next fire: $(systemctl show moses-morning.timer -p NextElapseUSecRealtime --value 2>/dev/null || echo 'timer not found')"
echo
echo "Done. All of Big Pipe's briefs now fire from the 08:00 America/Chicago timer."
echo "Remaining step (Brad, GitHub UI or already handled in the repo): the Actions workflow"
echo "schedules should be removed so the weekly does not ALSO post from GitHub."
