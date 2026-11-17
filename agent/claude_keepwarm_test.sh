#!/usr/bin/env bash
# claude_keepwarm_test.sh — the keep-alive asks as the subscription, never with an API key, and says
# when it fails. Run with --mutants to watch each guard fail.
set -uo pipefail
HERE=$(dirname "$(readlink -f "$0")")
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
pass=0; fail=0
check() { if eval "$2"; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL: $1"; fi; }

# A fake runner: records the environment and arguments it was started with.
cat > "$W/run" <<'R'
#!/usr/bin/env bash
env > "$KW_W/env"; echo "$@" > "$KW_W/args"; [ -z "${KW_FAIL:-}" ] || { echo "Not logged in"; exit 1; }; echo ok
R
chmod +x "$W/run"

run() {  # run <script>
  KW_W=$W MOSES_STATE=$W MOSES_CLAUDE_RUN=$W/run ANTHROPIC_API_KEY=sk-ant-test ANTHROPIC_AUTH_TOKEN=x \
    ANTHROPIC_BASE_URL=http://x "$1" >/dev/null 2>&1
}
suite() {
  local s=$1
  run "$s"; local rc=$?
  check "a good run exits 0" '[ $rc -eq 0 ]'
  check "no API key reaches the CLI" '! grep -q "^ANTHROPIC_API_KEY=" "$W/env"'
  check "no auth token reaches the CLI" '! grep -q "^ANTHROPIC_AUTH_TOKEN=" "$W/env"'
  check "no other endpoint reaches the CLI" '! grep -q "^ANTHROPIC_BASE_URL=" "$W/env"'
  check "it runs the login-keepwarm profile" 'grep -q -- "--app login-keepwarm" "$W/args"'
  check "a good run is recorded ok" 'grep -q "\"verdict\": \"ok\"" "$W/keepwarm.json"'
  KW_FAIL=1 run "$s"; rc=$?; unset KW_FAIL
  check "a signed-out run exits 1" '[ $rc -eq 1 ]'
  check "a signed-out run is recorded with its reason" 'grep -q "Not logged in" "$W/keepwarm.json"'
}
export -f run check
KW_FAIL= ; unset KW_FAIL
suite "$HERE/claude-keepwarm"
echo "claude_keepwarm_test: $pass passed, $fail failed"
[ "${1:-}" = --mutants ] || exit $(( fail > 0 ))

# Watch it fail: each mutant must turn at least one check red.
mutate() {  # mutate <name> <sed expression>
  local m="$HERE/.keepwarm-mutant"; sed "$2" "$HERE/claude-keepwarm" > "$m"; chmod +x "$m"
  local before=$fail; pass=0; fail=0; suite "$m" >/dev/null; local f=$fail; rm -f "$m"; fail=$before
  if [ "$f" -gt 0 ]; then echo "mutant caught: $1"; else echo "MUTANT SURVIVED: $1"; fail=$((fail+1)); fi
}
mutate "API key passed through" 's/-u ANTHROPIC_API_KEY //'
mutate "auth token passed through" 's/-u ANTHROPIC_AUTH_TOKEN //'
mutate "failure exits 0" 's/^\[ "\$rc" -eq 0 \]$/true/'
mutate "failure reason dropped" 's/why=\$(printf .*$/why=""; fi/'
exit $(( fail > 0 ))
