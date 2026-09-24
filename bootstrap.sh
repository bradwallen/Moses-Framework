#!/usr/bin/env bash
# bootstrap.sh — set Moses up on a fresh machine, without root, and without surprises.
#
#   ./bootstrap.sh            do it
#   ./bootstrap.sh --check    say what it WOULD do, change nothing
#
# WHAT IT WILL NOT DO: ask for sudo, write outside your home, start anything before you have given it
# a token, or claim a step succeeded without testing it. Every phase below prints what it observed,
# not what it intended.

set -uo pipefail
cd "$(dirname "$0")"
ROOT=$(pwd)
CHECK=0; [ "${1:-}" = "--check" ] && CHECK=1

say()  { printf '  %-42s %s\n' "$1" "$2"; }
step() { printf '\n── %s %s\n' "$1" "$(printf '─%.0s' $(seq 1 $((52 - ${#1}))))"; }
do_or_show() { if [ "$CHECK" = 1 ]; then echo "  would: $*"; else "$@"; fi; }

FAIL=0

step "Prerequisites"
for c in python3 git; do
  if command -v "$c" >/dev/null; then say "$c" "$($c --version 2>&1 | head -1)"
  else say "$c" "MISSING — install it first"; FAIL=1; fi
done
if command -v claude >/dev/null; then
  say "claude CLI" "$(claude --version 2>&1 | head -1)"
else
  say "claude CLI" "MISSING — Moses talks through it; see claude.com/claude-code"
  FAIL=1
fi
PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)
case "$PYV" in 3.1[1-9]|3.[2-9]*) say "python version" "$PYV ok" ;;
  *) say "python version" "$PYV — 3.11+ required"; FAIL=1 ;; esac

# Whether the CLI is LOGGED IN, not merely installed. A framework that installs cleanly and then
# fails on the first message has told you nothing useful.
if [ "$CHECK" = 0 ] && command -v claude >/dev/null; then
  printf '  %-42s ' "claude CLI is logged in"
  if timeout 90 claude -p 'reply with exactly: OK' --model claude-opus-5 --effort low \
       --setting-sources '' --no-session-persistence --output-format json 2>/dev/null \
       | grep -q '"result":"OK"'; then echo "yes"; else
       echo "NO — run 'claude' once and log in"; FAIL=1; fi
fi

[ "$FAIL" = 0 ] || { echo; echo "Stopping: fix the above first. Nothing has been changed." >&2; exit 1; }

step "Virtualenv"
if [ -x .venv/bin/python ]; then say ".venv" "already present"
else
  do_or_show python3 -m venv .venv
  [ "$CHECK" = 0 ] && .venv/bin/pip install --quiet --upgrade pip
fi
[ "$CHECK" = 0 ] && .venv/bin/pip install --quiet slack_sdk
[ "$CHECK" = 0 ] && say "slack_sdk" "$(.venv/bin/python -c 'import slack_sdk;print(slack_sdk.version.__version__)' 2>&1)"

step "Secret scanner (pre-commit)"
# Installed as a real hook, not documented as a good idea. Commandment 2 is enforced by this or it
# is enforced by nobody.
if [ -d .git ]; then
  do_or_show ln -sf ../../tools/scan-secrets.sh .git/hooks/pre-commit
  if [ "$CHECK" = 0 ]; then
    ./tools/scan-secrets.sh --selftest >/dev/null 2>&1 \
      && say "scanner self-test" "passes (catches real tokens, ignores patterns)" \
      || { say "scanner self-test" "FAILED — do not trust it"; FAIL=1; }
  fi
else
  say "pre-commit hook" "skipped — not a git checkout"
fi

step "Publish guard (pre-push)"
# The pause before something becomes public used to be a person typing `git push`. Once an agent can
# push, that pause has to be a mechanism instead.
if [ -d .git ]; then
  do_or_show ln -sf ../../tools/pre-push.sh .git/hooks/pre-push
  if [ "$CHECK" = 0 ]; then
    ./tools/pre-push.sh --selftest >/dev/null 2>&1 \
      && say "publish guard" "passes (refuses the archived history, a rewrite, an unscanned ref)" \
      || { say "publish guard" "FAILED — do not trust it"; FAIL=1; }
  fi
else
  say "pre-push hook" "skipped — not a git checkout"
fi

step "Directories"
for d in "$HOME/.config/moses" "$HOME/.local/state/moses" "$HOME/.claude/memory"; do
  if [ -d "$d" ]; then say "${d/#$HOME/~}" "exists"
  else do_or_show mkdir -p "$d"; say "${d/#$HOME/~}" "created"; fi
done
[ "$CHECK" = 0 ] && chmod 700 "$HOME/.config/moses"

step "Configuration"
CFG="$HOME/.config/moses/moses.env"
if [ -f "$CFG" ]; then
  say "moses.env" "already present — not overwritten"
else
  do_or_show install -m 600 config/moses.env.example "$CFG"
  say "moses.env" "created from the example (0600)"
fi
SLACK="$HOME/.config/moses/slack.env"
if [ -f "$SLACK" ]; then
  say "slack.env" "already present — not overwritten"
else
  do_or_show install -m 600 config/slack.env.example "$SLACK"
  say "slack.env" "created from the example (0600)"
fi
ROSTER="$HOME/.config/moses/roster.json"
if [ -f "$ROSTER" ]; then say "roster.json" "already present"
else do_or_show cp config/roster.example.json "$ROSTER"; say "roster.json" "created from the example"; fi

step "Systemd user unit"
UNITDIR="$HOME/.config/systemd/user"
do_or_show mkdir -p "$UNITDIR"
# The SAME unit its author runs (agent/systemd/moses.service), pointed at wherever this checkout and
# its venv are. %h is systemd's own "your home", so only the two locations need rewriting.
if [ "$CHECK" = 0 ]; then
  sed -e "s|%h/moses-venv|$ROOT/.venv|g" -e "s|%h/Projects/moses|$ROOT|g" \
      agent/systemd/moses.service > "$UNITDIR/moses.service"
  systemctl --user daemon-reload 2>/dev/null || true
fi
say "moses.service" "$UNITDIR/moses.service"
# Lingering is what makes a user unit survive logout. Without it Moses dies when you close the shell,
# which looks exactly like a crash.
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = "yes" ]; then
  say "lingering" "on — the unit survives logout"
else
  say "lingering" "OFF — run: sudo loginctl enable-linger $USER"
fi

step "Tests"
# A HEADER WITH NO OUTPUT IS NOT A PASS. --check used to print this heading and nothing under it,
# which reads exactly like a step that ran and found nothing to say. Say which it was.
if [ "$CHECK" = 1 ]; then
  say "suites" "would run $(ls agent/*_test.py 2>/dev/null | wc -l) suite(s) — skipped in --check"
elif [ ! -x .venv/bin/python ]; then
  say "suites" "NOT RUN — no .venv/bin/python yet"
fi
if [ "$CHECK" = 0 ] && [ -x .venv/bin/python ]; then
  pass=0; fail=0
  # The tests live BESIDE the modules, not in agent/tests/, and that is deliberate. They insert their
  # own directory on sys.path — so in a tests/ subdirectory they inserted the wrong one and four of
  # eight failed on import, silently, from the day they were moved there. Nothing ran them, so
  # nothing said so. The framework and the author's instance are the same tree; the layout has to be
  # the same too, or a file copied between them stops working for reasons that have nothing to do
  # with the change.
  for t in agent/*_test.py; do
    if ./.venv/bin/python "$t" >/dev/null 2>&1; then pass=$((pass+1)); else fail=$((fail+1)); fi
  done
  say "suites" "$pass passed, $fail failed"
  [ "$fail" -gt 0 ] && say "" "run them individually to see which"
fi

step "Portability"
# Advisory. The framework and its author's instance are the same tree, so anything personal in the
# engine is a bug — this counts them rather than lecturing about them. It becomes --strict at zero.
if [ "$CHECK" = 1 ]; then
  say "scan" "would run tools/check-portability.sh — skipped in --check"
elif [ ! -x tools/check-portability.sh ]; then
  say "scan" "NOT RUN — tools/check-portability.sh is missing or not executable"
else
  ./tools/check-portability.sh 2>/dev/null | grep -E 'tie this to one person|PORTABLE' | sed 's/^/  /'
fi

step "What is left for you"
cat <<EOF
  1. Put your Slack tokens in:
       $SLACK
     and your ids and channels in:
       $CFG
     slack.env lists the scopes the bot token needs; agent/verify-slack-scopes.sh checks them.

  2. MOSES_CHAT_CHANNELS is EMPTY on purpose. Moses stays a reporting surface until you name a
     channel — he does not become conversational by default.

  3. Start him:
       systemctl --user enable --now moses
       journalctl --user -u moses -f

  Nothing above needs root. Deploying a change later is 'systemctl --user restart moses'.
EOF
[ "$CHECK" = 1 ] && echo && echo "  (--check: nothing was changed)"
exit 0
