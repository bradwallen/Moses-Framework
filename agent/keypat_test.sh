#!/usr/bin/env bash
# keypat_test.sh — the key scanner must tell a credential from a sentence about credentials.
#
#   ./keypat_test.sh
#
# WHY. purge-key-copies.sh first grepped for `sk-ant-` alone and flagged a backup of listener.py
# whose only match is the line "a Pro plan covers Claude Code, NOT `sk-ant-` pay-per-token" — a
# comment ABOUT keys, containing none. It then exited non-zero saying the key was still present on a
# machine that was already clean.
#
# That is this project's oldest bug in a new hat: a scanner matching its own subject matter. This
# pulls the REAL pattern out of the script and runs it against fixtures, so the distinction cannot
# quietly rot back.
set -uo pipefail
cd "$(dirname "$0")"
pass=0; fail=0
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$*"; pass=$((pass+1)); }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; fail=$((fail+1)); }

# The scanner lives in the instance; the framework does not ship it. Say which tree this is rather
# than dying, so the credential-hygiene half below still runs everywhere.
if [ -f purge-key-copies.sh ]; then
  KEYPAT=$(sed -n "s/^KEYPAT='\(.*\)'$/\1/p" purge-key-copies.sh)
  [ -n "$KEYPAT" ] || { echo "  KEYPAT is gone from purge-key-copies.sh — this test is watching nothing" >&2; exit 2; }
else
  KEYPAT=""
fi

T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
want() { local label=$1 file=$2 expect=$3
  [ -n "$KEYPAT" ] || { skipped=$((skipped+1)); return; }
  if grep -q "$KEYPAT" "$file"; then got=flag; else got=clean; fi
  [ "$got" = "$expect" ] && ok "$label" || bad "$label — got $got, want $expect"; }
skipped=0

# A real key: the prefix plus a long run of key characters.
printf 'ANTHROPIC_API_KEY=sk-ant-api03-%s\n' \
  "$(head -c 200 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 95)" > "$T/real.env"
want "a real-shaped key is flagged" "$T/real.env" flag

# Prose: the prefix followed by punctuation. This is the case that produced the false alarm.
printf 'covers Claude Code, NOT `sk-ant-` pay-per-token, and he does not want a meter\n' > "$T/prose.py"
want "documentation naming the prefix is NOT flagged" "$T/prose.py" clean
printf '# an sk-ant- key is a separate, pay-per-token bill\n' > "$T/prose2.py"
want "the same in a code comment" "$T/prose2.py" clean

# Obvious fixtures. Short by construction, and flagging them would train people to ignore this.
printf 'os.environ["ANTHROPIC_API_KEY"] = "sk-ant-should-never-reach-the-cli"\n' > "$T/fixture.py"
want "a short test fixture is NOT flagged" "$T/fixture.py" clean

# The live files this actually runs against — none of them should trip it.
for f in listener.py conversation.py diagnose.py; do
  want "the real $f does not trip it" "$f" clean
done

echo
echo "no script may leak a credential under bash -x"
# On 2026-09-03 the Slack bot token reached a transcript because someone traced moses-liveness with
# `bash -x` to find out why it was hanging. xtrace echoes every expansion, so sourcing a credential
# file prints it. Debugging a script must never be the thing that leaks from it.
_checked=0
for f in moses-liveness moses-deadman moses-inbox; do
  # Absent is not failing: the framework does not ship all of these. Absent EVERYWHERE would be,
  # and that is asserted after the loop — a test that silently checks nothing is the failure this
  # whole file exists to prevent.
  [ -f "$f" ] || continue
  _checked=$((_checked+1))
  if grep -q 'set -a' "$f" && ! grep -qF 'case $- in *x*)' "$f"; then
    bad "$f sources a credential file with no xtrace guard"
  else
    ok "$f guards its credential handling from xtrace"
  fi
done
# And prove the guard actually works, rather than trusting that the line is present.
[ "$_checked" -gt 0 ] && ok "checked $_checked credential-sourcing script(s) in this tree" \
                      || bad "NO credential-sourcing script was found — this half checked nothing"
_t=$(mktemp -d); printf 'SLACK_BOT_TOKEN=xoxb-FAKE-CANARY-0000\n' > "$_t/e"
cat > "$_t/g.sh" <<'GUARDED'
say() { local _xt=0; case $- in *x*) _xt=1; set +x ;; esac
  set -a; . "$1"; set +a; [ -n "${SLACK_BOT_TOKEN:-}" ] && echo used
  [ "$_xt" = 1 ] && set -x; }
say "$1"
GUARDED
_n=$(bash -x "$_t/g.sh" "$_t/e" 2>&1 | grep -c CANARY)
[ "$_n" -eq 0 ] && ok "the guard pattern emits nothing under -x (proven, not assumed)" \
                || bad "the guard pattern still leaked $_n time(s)"
rm -rf "$_t"

[ "${skipped:-0}" -gt 0 ] && echo "  ($skipped key-pattern case(s) skipped — purge-key-copies.sh is not in this tree)"
printf '\n  %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
