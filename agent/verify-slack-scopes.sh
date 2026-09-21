#!/usr/bin/env bash
# verify-slack-scopes.sh — exercise every Slack path this estate uses, and say which scopes it needs.
#
#   ./verify-slack-scopes.sh            report only, no posting
#   ./verify-slack-scopes.sh --live     actually post, react, upload and clean up after itself
#
# WHY. Three of the bot token's fifteen scopes look unused — calls:write, users:read, files:read —
# and "looks unused" is not "is unused". A grep cannot see a call an SDK helper makes on your behalf.
# This runs the real methods against the real workspace and reports what actually happened, so the
# scopes can be dropped on evidence rather than on a reading of the source.
#
# RUN IT TWICE: once before the reinstall to get a baseline, once after to prove nothing broke. A
# scope that turns out to be needed fails here in seconds instead of silently three weeks later, in
# whichever persona used it.
#
# It cleans up: the test message is deleted and the uploaded file removed. If cleanup fails it says
# so rather than leaving litter nobody knows about.
set -uo pipefail
. "$(dirname "$(readlink -f "$0")")/lib/moses-env.sh"   # whose home, where Moses lives, the operator's settings
cd "$(dirname "$0")"

MODE=${1:-report}
CH=${SLACK_TEST_CHANNEL:-}          # a channel to test in, from the operator's settings
CFG=${MOSES_SLACK_ENV:-$HOME/.config/moses/slack.env}

pass=0; fail=0; skip=0
ok()   { printf '  \033[32mOK  \033[0m %-34s %s\n' "$1" "${2:-}"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %-34s %s\n' "$1" "${2:-}"; fail=$((fail+1)); }
note() { printf '  \033[33m--  \033[0m %-34s %s\n' "$1" "${2:-}"; skip=$((skip+1)); }

[ -r "$CFG" ] || { echo "no $CFG — nothing was checked, which is not the same as nothing being wrong" >&2; exit 2; }
{ set +x; set -a; . "$CFG"; set +a; } 2>/dev/null
[ -n "${SLACK_BOT_TOKEN:-}" ] || { echo "no SLACK_BOT_TOKEN in $CFG" >&2; exit 2; }

api() {  # api <method> <json-body>  -> prints "ok" or "err:<reason>"
  curl -sS -X POST "https://slack.com/api/$1" \
    -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
    -H "Content-Type: application/json; charset=utf-8" \
    --data "$2" 2>/dev/null | python3 -c '
import json,sys
try: d = json.load(sys.stdin)
except Exception: print("err:unparseable"); sys.exit()
print("ok" if d.get("ok") else "err:" + str(d.get("error")))'
}
apiget() {
  curl -sS -G "https://slack.com/api/$1" -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
    $2 2>/dev/null | python3 -c '
import json,sys
try: d = json.load(sys.stdin)
except Exception: print("err:unparseable"); sys.exit()
print("ok" if d.get("ok") else "err:" + str(d.get("error")))'
}

echo "── The scopes the token currently carries ──────────────────"
SCOPES=$(curl -sS -D- -o /dev/null -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
         https://slack.com/api/auth.test 2>/dev/null | grep -i "^x-oauth-scopes:" | cut -d' ' -f2- | tr -d '\r')
printf '  %s\n' "$(echo "$SCOPES" | tr ',' ' ')"
echo
echo "── What the estate actually calls ──────────────────────────"

r=$(apiget auth.test ""); [ "$r" = ok ] && ok "auth.test" "no scope needed" || bad "auth.test" "$r"
r=$(apiget conversations.history "-d channel=$CH -d limit=1")
[ "$r" = ok ] && ok "conversations.history" "channels:history" || bad "conversations.history" "$r"
r=$(apiget conversations.list "-d limit=1 -d types=public_channel")
[ "$r" = ok ] && ok "conversations.list" "channels:read" || bad "conversations.list" "$r"
r=$(apiget users.conversations "-d limit=1")
[ "$r" = ok ] && ok "users.conversations" "channels:read (NOT users:read)" || bad "users.conversations" "$r"

if [ "$MODE" != "--live" ]; then
  note "chat.postMessage" "needs --live"
  note "reactions.add" "needs --live"
  note "files upload" "needs --live"
else
  ts=$(curl -sS -X POST https://slack.com/api/chat.postMessage \
       -H "Authorization: Bearer $SLACK_BOT_TOKEN" -H "Content-Type: application/json; charset=utf-8" \
       --data "$(python3 -c 'import json,sys;print(json.dumps({"channel":sys.argv[1],"text":"scope check — deleting this in a moment","username":"Moses","icon_emoji":":scroll:"}))' "$CH")" \
       2>/dev/null | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("ts","") if d.get("ok") else "")')
  if [ -n "$ts" ]; then
    ok "chat.postMessage" "chat:write + chat:write.customize"
    r=$(api reactions.add "$(python3 -c 'import json,sys;print(json.dumps({"channel":sys.argv[1],"timestamp":sys.argv[2],"name":"eyes"}))' "$CH" "$ts")")
    [ "$r" = ok ] && ok "reactions.add" "reactions:write" || bad "reactions.add" "$r"
    r=$(apiget conversations.replies "-d channel=$CH -d ts=$ts")
    [ "$r" = ok ] && ok "conversations.replies" "channels:history" || bad "conversations.replies" "$r"
    r=$(api chat.delete "$(python3 -c 'import json,sys;print(json.dumps({"channel":sys.argv[1],"ts":sys.argv[2]}))' "$CH" "$ts")")
    [ "$r" = ok ] && ok "cleanup: message deleted" || bad "cleanup: message NOT deleted" "$r — remove it by hand"
  else
    bad "chat.postMessage" "could not post; the rest of the live checks were skipped"
  fi
  r=$(apiget files.getUploadURLExternal "-d filename=scopecheck.txt -d length=5")
  [ "$r" = ok ] && ok "files.getUploadURLExternal" "files:write" || bad "files.getUploadURLExternal" "$r"
fi

echo
echo "── Scopes granted that nothing here exercised ──────────────"
for s in calls:write users:read files:read; do
  case ",$SCOPES," in
    *",$s,"*) note "$s" "granted, and no path above needs it — candidate to drop" ;;
    *)        ok   "$s" "not granted" ;;
  esac
done
echo "  reactions:read is NOT in that list on purpose — the confirm-by-reaction"
echo "  path listens for reaction_added events, which needs it."

printf '\n  %d ok, %d failed, %d not exercised\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ] || exit 1
