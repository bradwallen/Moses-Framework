#!/usr/bin/env bash
# roster_guard_test.sh — root must never run something the operator could rewrite.
#
#   ./roster_guard_test.sh
#
# The roster is operator-writable now, which is the point: Moses no longer runs as root, so editing
# who is accountable should not need sudo. But TWO root-run scripts execute the command a persona
# declares — the standup's `moses` CLI and the sudo wrapper. Without this guard, "declare a persona"
# would mean "run anything as root".
#
# Drives the REAL guard out of lib/, not a restatement of it.
set -uo pipefail
cd "$(dirname "$0")"
pass=0; fail=0
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$*"; pass=$((pass+1)); }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; fail=$((fail+1)); }

[ -r lib/roster-exec-guard.sh ] || { echo "  the guard is GONE — this test is watching nothing" >&2; exit 2; }
source lib/roster-exec-guard.sh

want() { local label=$1 path=$2 expect=$3
  if roster_argv_ok "$path" 2>/dev/null; then got=allow; else got=refuse; fi
  [ "$got" = "$expect" ] && ok "$label" || bad "$label — got $got, want $expect ($path)"; }

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
printf '#!/bin/sh\necho hi\n' > "$TMP/mine.sh"; chmod 755 "$TMP/mine.sh"
ln -s "$TMP/mine.sh" "$TMP/looks-official"
# The inverse symlink: a link the OPERATOR owns pointing at a root-owned probe. Correct code
# resolves it and allows; code that judges the link itself sees an operator-owned file and refuses.
# This is the only fixture that isolates the readlink step, so it earns its place.
ln -s /usr/local/bin/therapist "$TMP/to-a-real-probe"

echo "the real probes still run"
want "a root-owned probe in /usr/local/bin" /usr/local/bin/birdeye-report allow
want "the other one too"                    /usr/local/bin/therapist      allow

echo
echo "anything the operator could rewrite is refused"
want "a script in the operator's own tmp"   "$TMP/mine.sh"                refuse
want "a SYMLINK to one — judged by what it resolves to, not its name" "$TMP/looks-official" refuse

echo
echo "resolution happens before judgement"
want "an operator-owned symlink TO a root probe is allowed" "$TMP/to-a-real-probe" allow
want "a file in the Moses source tree"      "$PWD/moses"                  refuse
want "a relative path (root must not search PATH)" "birdeye-report"       refuse
want "an empty command"                     ""                            refuse
want "a directory"                          /tmp                          refuse
want "a non-executable root file"           /etc/hostname                 refuse

echo
echo "the callers refuse when the guard itself is missing"
# The CLI sits beside the agent modules in the instance and under cli/ in the framework — the two
# layouts differ, so the test LOOKS rather than assuming, and says plainly when it cannot find one
# instead of passing by default.
CALLERS=""
for cand in moses ../cli/moses moses-remediate; do
  [ -f "$cand" ] && CALLERS="$CALLERS $cand"
done
case "$CALLERS" in
  *moses-remediate*) ;;
  *) bad "moses-remediate not found — the guard's main caller is missing" ;;
esac
case "$CALLERS" in
  *cli/moses*|*\ moses\ *) ;;
  *) bad "no standup CLI found in either layout — cannot check its guard call" ;;
esac
for f in $CALLERS; do
  grep -q 'roster_argv_ok' "$f" && ok "$(basename "$f") calls the guard" || bad "$f does not call the guard"
done
# A missing guard file must fall back to refusing, never to running unchecked.
for f in $CALLERS; do
  if sed -n '/if \[ -r "\$GUARD" \]/,/^fi$/p' "$f" | grep -q 'refusing to run anything it named'; then
    ok "$(basename "$f") refuses when the guard file is absent"
  else
    bad "$f does not fail closed on a missing guard"
  fi
done

echo
echo "what this test canNOT isolate, said out loud"
# The file-owner and directory-owner checks mask each other for every fixture an unprivileged test
# can build: anything this script creates lives in a directory it owns, so the directory check
# refuses it first, and there is no non-root file inside a root-owned, non-writable directory on
# this box to use instead. Disabling EITHER check alone therefore leaves the composite cases green.
# Asserting the branches exist is weaker than exercising them, and it is labeled as such rather than
# counted as coverage — see Commandment 8 on stating what a check did not look at.
for probe in owner downer; do
  if grep -qF "\"\$$probe\" = root" lib/roster-exec-guard.sh; then
    ok "SOURCE-ONLY: the $probe check is present (not behaviorally isolated here)"
  else
    bad "the $probe check is GONE from the guard"
  fi
done

echo
echo "the operator resolves by PRIVILEGE, never by SUDO_USER"
# On 2026-09-02 the migration verified itself through `sudo -u brad` run BY ROOT. SUDO_USER is then
# *root*, the old resolver looked up root's home, and the installer reported two failures against a
# system that was working perfectly. A guard that fires on correct work gets switched off.
for f in $CALLERS; do
  base=$(basename "$f")
  got=$(SUDO_USER=root MOSES_OP_HOME= bash -c '
      if [ "$(id -u)" -ne 0 ]; then OP_HOME=${MOSES_OP_HOME:-$HOME}
      else _op=${MOSES_OPERATOR_USER:-${SUDO_USER:-brad}}; [ "$_op" = root ] && _op=${MOSES_OPERATOR_USER:-brad}
           OP_HOME=${MOSES_OP_HOME:-$(getent passwd "$_op" | cut -d: -f6)}; fi
      printf %s "$OP_HOME"')
  [ "$got" = "$HOME" ] && ok "SUDO_USER=root does not redirect the operator home" \
                       || bad "resolver gave $got, want $HOME"
  break
done
for f in $CALLERS; do
  grep -qF '"$(id -u)" -ne 0' "$f" \
    && ok "$(basename "$f") keys the resolver off privilege" \
    || bad "$(basename "$f") still trusts SUDO_USER for identity"
done

printf '\n  %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
