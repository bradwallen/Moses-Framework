#!/usr/bin/env bash
# bootstrap-unit-selftest.sh — prove bootstrap.sh never replaces a moses.service it was not asked to.
#
#   tools/bootstrap-unit-selftest.sh                      exit 1 on any failure
#   BOOTSTRAP=/path/to/copy tools/bootstrap-unit-selftest.sh   run against a deliberately broken copy
#
# Runs bootstrap.sh --check under a throwaway HOME, so it changes nothing real. Before 2026-10-29
# bootstrap overwrote the unit every run, and a second checkout quietly took over the live service.

set -uo pipefail
ROOT=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
BOOTSTRAP="${BOOTSTRAP:-$ROOT/bootstrap.sh}"
pass=0; fail=0
ok()  { echo "  PASS  $1"; pass=$((pass+1)); }
bad() { echo "  FAIL  $1"; fail=$((fail+1)); }
unit_step() {  # the "Systemd user unit" section of a --check run under HOME=$1
  HOME="$1" bash "$BOOTSTRAP" --check "${@:2}" 2>/dev/null | sed -n '/Systemd user unit/,/lingering/p'
}

H=$(mktemp -d); mkdir -p "$H/.config/systemd/user"
out=$(unit_step "$H")
case "$out" in *"would: write $H/.config/systemd/user/moses.service"*) ok "no unit yet: it would write one";;
  *) bad "no unit yet: expected 'would: write', got: $out";; esac

printf '[Service]\nExecStart=/some/other/checkout/agent/listener.py\n' > "$H/.config/systemd/user/moses.service"
out=$(unit_step "$H")
case "$out" in *"KEPT"*"--replace-unit"*) ok "a different unit is kept, with the way to replace it";;
  *) bad "a different unit was not kept: $out";; esac
case "$out" in *"would: write"*) bad "it still plans to write over the different unit";;
  *) ok "no write is planned over the different unit";; esac

out=$(unit_step "$H" --replace-unit)
case "$out" in *"would: write"*) ok "--replace-unit plans the replacement";;
  *) bad "--replace-unit did not plan a write: $out";; esac

sed -e "s|%h/moses-venv|$(dirname "$BOOTSTRAP")/.venv|g" -e "s|%h/Projects/moses|$(dirname "$BOOTSTRAP")|g" \
  "$(dirname "$BOOTSTRAP")/agent/systemd/moses.service" > "$H/.config/systemd/user/moses.service"
out=$(unit_step "$H")
case "$out" in *"already current"*) ok "an identical unit is left alone";;
  *) bad "identical unit not recognized: $out";; esac

echo; echo "  $pass passed, $fail failed"
[ "$fail" -eq 0 ]
