#!/usr/bin/env bash
# root_scripts_test.sh — the scripts that run as ROOT never execute a file the operator can write.
#
#   bash agent/root_scripts_test.sh
#
# WHY (2026-09-21). The 08:00 standup (/usr/local/bin/moses, a root timer) sourced ~/.config/moses/
# slack.env and ran moses-drift, project_status.py and moses-project straight from the checkout;
# birdeye-customs and therapist sourced knight.env / customs.env; and install-persona-tools.sh — which
# runs as root with NO password — sourced knight.env. Every agent runs as the operator and can write all
# of those, so each one was a way from "an agent" to "root". Now the operator's settings are read as
# DATA (op_value) and the operator's code runs AS the operator (as_op).
#
# Checked two ways. BEHAVIOR: the real scripts are run against settings files that plant a command, and
# the command must not run. SHAPE: the lines that would reopen it are named, so a later edit that
# re-adds `. "$KNIGHT_ENV"` fails here rather than at 08:00. Runs unprivileged; the root-only branches
# (as_op's runuser) are asserted by shape, and that is said rather than hidden.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"
pass=0; fail=0
ok()  { printf '  PASS  %s\n' "$*"; pass=$((pass+1)); }
bad() { printf '  FAIL  %s\n' "$*"; fail=$((fail+1)); }

T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
H="$T/home"; mkdir -p "$H/.config/moses" "$T/state"
CANARY="$T/PWNED"
# Every way a sourced file runs code: a bare command, a command substitution, a backquote.
plant() {
  cat > "$1" <<EOF
touch "$CANARY.bare"
SLACK_BOT_TOKEN=xoxb-planted\$(touch "$CANARY.subst")
MOSES_NAME="Planted"
VIATICA_APP_URL=http://127.0.0.1:9
CRON_SECRET=\`touch "$CANARY.tick"\`
SLACK_KNIGHT_CHANNEL=C0PLANTED
SLACK_OPS_CHANNEL=C0PLANTED
EOF
}
for f in slack knight customs; do plant "$H/.config/moses/$f.env"; done
printf '{"version":1,"personas":[]}\n' > "$T/roster.json"
canaries() { ls "$CANARY".* 2>/dev/null | xargs -r -n1 basename | tr '\n' ' '; }

echo "behavior — the real scripts, pointed at planted settings"
MOSES_OP_HOME="$H" MOSES_REGISTRY="$T/roster.json" MOSES_STATE="$T/state" MOSES_EXEC_GUARD=/nonexistent \
  ./moses roster >/dev/null 2>&1
[ -z "$(canaries)" ] && ok "the standup reads slack.env without running it" \
                     || bad "the standup RAN the operator's slack.env: $(canaries)"
rm -f "$CANARY".*

if [ -f root/birdeye-customs ]; then
  MOSES_OP_HOME="$H" timeout 60 ./root/birdeye-customs --oneline >/dev/null 2>&1
  [ -z "$(canaries)" ] && ok "birdeye-customs reads knight.env and customs.env without running them" \
                       || bad "birdeye-customs RAN the operator's settings: $(canaries)"
else
  echo "  skip  root/birdeye-customs (not in this tree)"
fi
rm -f "$CANARY".*

# therapist and the installer do real work (post to Slack, install into /usr/local), so their reader is
# exercised directly — the function itself, lifted from each file, not a copy of it written here.
for s in root/therapist root/install-persona-tools.sh moses; do
  # The root/ scripts are this machine's own personas and do not ship in the framework; absent is
  # skipped and SAID. The standup is always present — a test that could check nothing must not pass.
  if [ ! -f "$s" ]; then [ "$s" = moses ] && bad "moses is missing" || echo "  skip  $s (not in this tree)"; continue; fi
  fn=$(awk '/^op_value\(\) \{/,/^\}/' "$s")
  if [ -z "$fn" ]; then bad "$s has no op_value reader"; continue; fi
  got=$(bash -c "$fn"$'\n'"op_value '$H/.config/moses/knight.env' SLACK_KNIGHT_CHANNEL; op_value '$H/.config/moses/knight.env' CRON_SECRET" 2>/dev/null | tr '\n' '|')
  if [ -z "$(canaries)" ] && [ "$got" = "C0PLANTED|\`touch \"$CANARY.tick\"\`|" ]; then
    ok "$s: op_value returns the text, and runs none of it"
  else
    bad "$s: op_value ran something or misread (got '$got', canaries: $(canaries))"
  fi
  rm -f "$CANARY".*
done

echo
echo "shape — the lines that would reopen it"
# No root script may source anything that is not root-owned. The only sources left are the exec guard
# and loops over /etc/… files.
for s in moses moses-remediate root/therapist root/birdeye-customs root/install-persona-tools.sh; do
  [ -f "$s" ] || { echo "  skip  $s (not in this tree)"; continue; }
  # `.` or `source` where a COMMAND starts — line start, after ; && || ( $( or then/do/else. Matching it
  # anywhere fired on `jq -e . "$REG"`, where `.` is a jq filter: a guard that fires on correct work is
  # the one that gets switched off.
  hits=$(grep -nE '(^\s*|[;(]\s*|&&\s*|\|\|\s*|\$\(\s*|\b(then|do|else)\s+)(\.|source)\s+"?\$' "$s" | grep -vE '^\s*[0-9]+:\s*#' \
         | grep -vE '\. "\$GUARD"|\. "\$c"\s+# root-owned|\. "\$f"\s+# root-owned|\. "\$c" 2>/dev/null; printf' || true)
  # ANY source line not on the list above fails — not only ones that NAME an operator path. The first
  # version required the name on the same line and so passed the old therapist, whose loop listed the
  # operator's knight.env one line above `(. "$f"; …)`. Allowed: the exec guard, and the /etc loops,
  # each marked `# root-owned` (install-persona-tools' `$c` loops run over /etc only).
  bad_hits=$hits
  [ -z "$bad_hits" ] && ok "$s sources nothing the operator can write" \
                     || bad "$s sources an operator file: $bad_hits"
done
# The standup runs the operator's code only through as_op.
naked=$(grep -nE '(python3|^\s*|\$\()\s*"?\$MOSES_ROOT/' moses | grep -vE '^\s*[0-9]+:\s*#|as_op|\[ -x ' || true)
[ -z "$naked" ] && ok "the standup runs checkout code only as the operator (as_op)" \
                || bad "the standup runs checkout code as itself: $naked"
grep -q 'runuser -u "$_op" -- env HOME="$OP_HOME"' moses \
  && ok "as_op drops to the operator under root (shape only: this test cannot run as root)" \
  || bad "as_op no longer drops privilege"

echo
echo "  $pass passed, $fail failed"
[ "$fail" -eq 0 ]
