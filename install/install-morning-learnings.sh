#!/usr/bin/env bash
# install-morning-learnings.sh — two fixes to the 08:00 briefing, in one pass.
#
#   sudo $OP_HOME/scripts/install-morning-learnings.sh
#
# 1. LOOK-INTO-FURTHER IN THE BRIEF. Moses files what he learns from Atlas as it happens; the
#    briefing now also lists anything filed since the last one, so a lesson cannot be missed just
#    because Brad was not reading Slack at the moment it landed. Each item is surfaced ONCE.
#
# 2. THERAPIST'S KNIGHT CHECK RUNS AS BRAD, AND SHOWS ITS WORKING. On 2026-08-14 it reported
#    "cannot reach the push remote" while the remote was demonstrably reachable — running
#    `knight doctor` by hand printed "reachable ✓" minutes later.
#
#    Knight is brad's: his clone, his HOME, his SSH identity. Therapist runs from moses-morning,
#    which is a root system unit, so it was checking a different user's ability to reach GitHub than
#    the one that will actually push. **That mismatch is worth removing whether or not it caused
#    this alarm** — the check is meaningless unless it runs as the user who does the work.
#
#    It could not be reproduced from an unprivileged shell (ssh keeps resolving brad's identity via
#    /etc/passwd no matter how the environment is stripped), so the cause is NOT asserted here. The
#    second half of the fix is the instrument: when Therapist reports Knight trouble it now quotes
#    the doctor line that triggered it, so a recurrence arrives with its own evidence instead of a
#    claim.
#
# Idempotent, backs up both files, verifies before and after, and reconciles a source-vs-installed
# drift that has been sitting there since 2026-08-12.

set -uo pipefail

# THE OPERATOR'S HOME, resolved rather than named — and correct under sudo, where $HOME is root's.
# That distinction is the whole reason this is not just "$HOME": these scripts are run with sudo,
# and a root-relative path would install into /root and appear to work.
OP_HOME=${MOSES_OP_HOME:-$(getent passwd "${SUDO_USER:-$(id -un)}" | cut -d: -f6)}
[ -n "$OP_HOME" ] || { echo "cannot resolve the operator's home; set MOSES_OP_HOME" >&2; exit 1; }
MOSES_ROOT=${MOSES_ROOT:-$OP_HOME/Projects/moses}

[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }

STAMP=$(date +%Y%m%d-%H%M%S)
MOSES=/usr/local/bin/moses
THERAPIST=/usr/local/bin/health-report
SRC=$MOSES_ROOT/agent/moses
MEM=$OP_HOME/.claude/memory

echo "── Before ──────────────────────────────────────────────"
printf '  knight doctor as ROOT   : '
if timeout 90 $MOSES_ROOT/knight/bin/knight doctor 2>&1 | grep -qE 'reachable ✓'; then
  echo "remote reachable"
else
  echo "remote NOT reachable  <-- reproduces this morning's alarm"
fi
printf '  knight doctor as BRAD   : '
if sudo -u brad timeout 90 $MOSES_ROOT/knight/bin/knight doctor 2>&1 | grep -qE 'reachable ✓'; then
  echo "remote reachable"
else
  echo "remote NOT reachable"
fi
echo "  (if those two differ, the user mismatch WAS the false positive — now recorded, not guessed)"
echo

echo "── Reconcile the drift ─────────────────────────────────"
if ! diff -q "$MOSES" "$SRC" >/dev/null 2>&1; then
  cp -a "$SRC" "$SRC.bak-$STAMP" 2>/dev/null
  install -m 0755 -o brad -g brad "$MOSES" "$SRC"
  echo "  source copy refreshed from the installed one (they had diverged since 2026-08-12)"
else
  echo "  already in sync"
fi
echo

echo "── 1. Look-into-further in the briefing ────────────────"
cp -a "$MOSES" "$MOSES.bak-$STAMP" && echo "  backed up  $MOSES.bak-$STAMP"

if grep -q 'learnings_new()' "$MOSES"; then
  echo "  already patched — skipping"
else
  python3 - "$MOSES" <<'PYEOF'
import re, sys
p = sys.argv[1]
s = open(p).read()

helper = '''
# ── Look into further ────────────────────────────────────────────────────────
# Moses files a lesson the moment Atlas says something worth chasing, and says so in the channel.
# This is the second net: a lesson must not be missed because nobody was reading Slack at 9pm.
# Each item is surfaced ONCE — a briefing that repeats itself is one you learn to skim.
LEARN_FILE=${MOSES_LEARN_FILE:-$OP_HOME/.claude/memory/look-into-further.md}
LEARN_STATE="$STATE/learnings-surfaced"

learnings_new() {
  [ -r "$LEARN_FILE" ] || return 0
  touch "$LEARN_STATE" 2>/dev/null
  while IFS= read -r line; do
    case "$line" in "- [ ] "*) ;; *) continue ;; esac
    local key; key=$(printf '%s' "$line" | sha1sum | cut -c1-16)
    grep -qF "$key" "$LEARN_STATE" 2>/dev/null && continue
    # The brackets MUST be escaped: inside ${var#pattern}, `[ ]` is a glob character class matching
    # one space, not the literal text. Unescaped, every item arrived with its checkbox still on it.
    printf '%s\\t%s\\n' "$key" "${line#- \\[ \\] }"
  done < "$LEARN_FILE"
}

learnings_mark() { printf '%s\\n' "$1" >> "$LEARN_STATE" 2>/dev/null; }

'''

anchor = "cmd_standup() {"
assert anchor in s, "cmd_standup not found"
s = s.replace(anchor, helper.lstrip("\n") + anchor, 1)

# Collect new learnings alongside the deferred items, so they ride in the same briefing.
old = '''  for id in $(ids); do
    line=$(overdue_line "$id") && overdue="$overdue
$line"
  done
'''
new = '''  local learned="" lkey lbody
  while IFS=$'\\t' read -r lkey lbody; do
    [ -n "$lkey" ] || continue
    learned="$learned
   • $lbody"
    learnings_mark "$lkey"
  done < <(learnings_new)

  for id in $(ids); do
    line=$(overdue_line "$id") && overdue="$overdue
$line"
  done
'''
assert old in s, "overdue loop not found"
s = s.replace(old, new, 1)

# Quiet morning: a filed lesson is worth breaking the silence for, same as a deferred decision.
old = '''    if [ -n "$deferred" ]; then
      say_msg "🗓️ *Worth revisiting*$deferred"
      return 0
    fi'''
new = '''    if [ -n "$deferred" ] || [ -n "$learned" ]; then
      local quiet=""
      [ -n "$learned" ] && quiet="📌 *Look into further*$learned"
      [ -n "$deferred" ] && quiet="${quiet:+$quiet

}🗓️ *Worth revisiting*$deferred"
      say_msg "$quiet"
      return 0
    fi'''
assert old in s, "clean-morning branch not found"
s = s.replace(old, new, 1)

# Busy morning: append it to the report that is already going out.
old = '''  [ -n "${DEFERRED_TAIL:-}" ] && msg="$msg

🗓️ *Worth revisiting*$DEFERRED_TAIL"'''
new = '''  [ -n "${DEFERRED_TAIL:-}" ] && msg="$msg

🗓️ *Worth revisiting*$DEFERRED_TAIL"
  [ -n "$learned" ] && msg="$msg

📌 *Look into further*$learned"'''
assert old in s, "deferred tail not found"
s = s.replace(old, new, 1)

open(p, "w").write(s)
print("  patched    look-into-further into cmd_standup")
PYEOF
  [ $? -eq 0 ] || { echo "  PATCH FAILED — restoring"; cp -a "$MOSES.bak-$STAMP" "$MOSES"; exit 1; }
fi

if ! bash -n "$MOSES"; then
  echo "  SYNTAX ERROR after patching — restoring" >&2
  cp -a "$MOSES.bak-$STAMP" "$MOSES"; exit 1
fi
echo "  syntax ok"
echo

echo "── 2. Therapist checks Knight AS BRAD, and quotes its evidence ──"
cp -a "$THERAPIST" "$THERAPIST.bak-$STAMP" && echo "  backed up  $THERAPIST.bak-$STAMP"
if grep -q 'runuser -u brad' "$THERAPIST"; then
  echo "  already patched — skipping"
else
  python3 - "$THERAPIST" <<'PYEOF'
import sys
p = sys.argv[1]
s = open(p).read()

old = '''  local out
  out=$("$KNIGHT" doctor 2>&1) || true
  KNIGHT_TEXT="$out"'''
new = '''  local out
  # AS BRAD. Knight is his: his clone, his HOME, his SSH identity, and his push. Therapist runs from
  # moses-morning, a root unit, so checking "can we reach the remote" as root asked about a user who
  # will never do the pushing. On 2026-08-14 that reported the remote unreachable minutes before a
  # hand-run doctor printed "reachable ✓". Run it as the user who does the work, or do not run it.
  if [ "$(id -u)" = 0 ]; then
    out=$(runuser -u brad -- "$KNIGHT" doctor 2>&1) || true
  else
    out=$("$KNIGHT" doctor 2>&1) || true
  fi
  KNIGHT_TEXT="$out"'''
assert old in s, "doctor invocation not found"
s = s.replace(old, new, 1)

# Quote the line that triggered the alarm. A claim that carries its own evidence can be checked in
# one glance; one that does not costs a morning of re-running things by hand.
old = '''  printf '%s\\n' "$out" | grep -qE 'CANNOT REACH THE REMOTE' && trouble="$trouble
   • cannot reach the push remote — a green build would build, then fail to land"'''
new = '''  if printf '%s\\n' "$out" | grep -qE 'CANNOT REACH THE REMOTE'; then
    local why; why=$(printf '%s\\n' "$out" | grep -A2 'CANNOT REACH THE REMOTE' | tail -1 | sed 's/^ *//')
    trouble="$trouble
   • cannot reach the push remote — a green build would build, then fail to land
     _${why:-no detail from git}_"
  fi'''
assert old in s, "remote grep not found"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("  patched    runs as brad + quotes the failing line")
PYEOF
  [ $? -eq 0 ] || { echo "  PATCH FAILED — restoring"; cp -a "$THERAPIST.bak-$STAMP" "$THERAPIST"; exit 1; }
fi
bash -n "$THERAPIST" || { echo "  SYNTAX ERROR — restoring" >&2; cp -a "$THERAPIST.bak-$STAMP" "$THERAPIST"; exit 1; }
echo "  syntax ok"
echo

echo "── Verify, without posting to Slack ────────────────────"
printf '  health-report now reports Knight as: '
MSG=$(MOSES_STATE=/var/lib/moses "$THERAPIST" full --print 2>&1)
if printf '%s' "$MSG" | grep -q 'not ready'; then
  printf '%s\n' "$MSG" | grep -A3 'not ready' | sed 's/^/    /'
else
  echo "ready ✓"
fi
echo
echo "  a new look-into-further item would appear as:"
MOSES_LEARN_FILE=/dev/null bash -c "true"   # the real file is read at 08:00
if [ -r "$MEM/look-into-further.md" ]; then
  grep -c '^- \[ \]' "$MEM/look-into-further.md" | xargs -I{} echo "    {} open item(s) waiting for the next briefing"
else
  echo "    (no items filed yet — the file appears on the first real learning)"
fi
echo
echo "Done. Next 08:00 briefing carries anything Moses files between now and then."
echo "To see it immediately without waiting:  sudo moses standup"
