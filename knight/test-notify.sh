#!/usr/bin/env bash
# test-notify.sh — the conversation that asked hears how its build is going, on ONE card.
#
#   bash knight/test-notify.sh
#
# 2026-09-11: Moses promised "I'll ping you here when it lands" and nothing could. That became a post to
# the asking conversation (notify.json) beside Knight's channel report. 2026-10-17, Brad: too many
# messages from too many agents. Now every step of a run edits ONE status card (lib/card.py), which
# moves into the asking conversation and pings there when the run is done or needs him. card_test.py
# drives card.py itself against a fake Slack; this checks the WIRING — that every step that used to
# post is now a card update, so nothing posts around it.
set -uo pipefail
# NO TEST POSTS TO SLACK. Everything this suite runs — and anything those scripts hand off to, like
# knight-next after a landing — inherits this. test-land once did not, and its scratch target "t1"
# posted "Stopped" cards into Brad's channel on every run (2026-10-18). test-notify.sh checks every suite.
export KNIGHT_SLACK_CHANNEL="" KNIGHT_SLACK_ENV=/nonexistent
HERE=$(cd "$(dirname "$0")" && pwd)
pass=0; fail=0
ok()  { printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL  %s\n' "$1"; fail=$((fail+1)); }

echo "every step of a run reports on its one card"
grep -q 'lib/card.py" "\$dir" building' "$HERE/bin/knight" && ok "knight start opens (or joins) the run's card" || bad "knight start does not use the card"
grep -q -- '--run-of' "$HERE/bin/knight" && ok "a job can join its run's card (--run-of)" || bad "knight start cannot join a run"
grep -q '"\$CARD" "\$DIR" checking' "$HERE/bin/knight-run" && ok "the checks starting is a card line" || bad "knight-run does not say the checks started"
grep -q 'card.py" "\$DIR" reviewing' "$HERE/bin/knight-run" && ok "the review is a card line, not Zryachiy's own post" || bad "the review is not on the card"
grep -q '"\$CARD" "\$DIR" "\$_cs"' "$HERE/bin/knight-run" && ok "every job outcome updates the card" || bad "the job's outcome is not on the card"
grep -q 'report-slack.txt' "$HERE/bin/knight-run" && ok "the report goes under the card as a reply" || bad "the report is not under the card"

echo
echo "nothing posts around the card"
! grep -q 'chat.postMessage' "$HERE/bin/knight-run" && ok "knight-run posts nothing itself" || bad "knight-run still posts on its own"
! grep -q 'notify_payload' "$HERE/bin/knight-run" && ok "the old second post to the asking conversation is gone" || bad "knight-run still double-posts"
left=$(sed -n '/^# The tracker.s word on the project/,$p' "$HERE/bin/knight-next" | grep -c 'slack_say "')
[ "$left" = 0 ] && ok "knight-next says everything on the card" || bad "knight-next still posts directly ($left place(s))"
grep -q 'card_or_say live' "$HERE/bin/knight-land-live" && ok "making it live is a card line" || bad "knight-land-live posts on its own"
grep -q 'live)   extra+=(--next "Nothing — it is live.")' "$HERE/bin/knight-land-live" \
    && ok "a live card's Next line says so, not \"say merge it\"" || bad "a live card keeps its old Next line"
grep -q -- '--run-of "\$RUN_OF"' "$HERE/bin/knight-next" && ok "knight-next's jobs join the run's card" || bad "knight-next starts jobs with cards of their own"

echo
echo "the conversation that asked"
grep -q 'notify.json' "$HERE/../agent/knight_notify.py" && ok "the listener records the conversation that asked" || bad "nothing writes notify.json"
grep -q 'notify.json' "$HERE/lib/card.py" && ok "the card goes where the request came from" || bad "the card ignores the asking conversation"

echo
echo "no suite posts to Slack"
# Every knight/test-*.sh switches Slack off before it runs anything, for itself and for whatever the
# scripts it runs hand off to. 2026-10-18: test-land did not, and a knight-next it set off posted a
# "t1 … Stopped" card into Brad's channel on every gate run, which Moses then tried to explain to him.
loud=""
for t in "$HERE"/test-*.sh; do
    grep -qE '^export .*KNIGHT_SLACK_CHANNEL=""' "$t" || loud="$loud $(basename "$t")"
done
[ -z "$loud" ] && ok "every knight suite exports KNIGHT_SLACK_CHANNEL=\"\" at the top" \
    || bad "these suites can post to the real Slack:$loud"

printf '\n%d ok, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
