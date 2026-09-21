#!/usr/bin/env bash
# install-persona-tools.sh — the only privileged step for the read-only persona tools.
#
#   sudo $OP_HOME/scripts/install-persona-tools.sh
#
# WHY ROOT, for exactly three things:
#   1. /usr/local/bin/ops-report is root-owned. It gains a --print mode (render instead of post)
#      and, more importantly, it now CACHES every report it produces to a world-readable file.
#   2. VIATICA_APP_URL and CRON_SECRET live in /etc/moses/moses.env (0600 root). The MCP server runs
#      as brad and cannot read it, so a brad-owned 0600 copy is made for that service alone.
#   3. The Slack bot token is likewise root-only, and Knight (running as brad) needs it to post his
#      completion report. Same pattern, same reasoning.
#
# Optional: --knight-channel <C0…> sends Knight's reports somewhere other than #ops.
#
# WHY A CACHE INSTEAD OF LETTING THE MCP SERVER RUN THE REPORT
# Two independent reasons, either sufficient:
#   * Run from the MCP server, ops-report would POST. Asking "did the backup run?" from a phone
#     would fire a message at #ops every time, and a channel that pings on questions stops meaning
#     anything when it pings on a real failure.
#   * Run as brad it cannot read the iDrive profile directory, so it reports "no job summary found"
#     — a false alarm about the one thing Birdeye exists to watch. A wrong answer is worse than none.
# Granting brad sudo on the binary would solve both. The cache is strictly less privilege for the
# same information, and it is the EXACT text Slack received, so the tool cannot disagree with the
# channel. It costs freshness, which the tool states out loud instead of hiding.
#
# ON COPYING THE SECRET: this widens CRON_SECRET from root-only to brad-readable. Worth stating
# plainly rather than burying. It is defensible because the MCP server already runs as brad, and
# because that secret's entire reach is the two reporting endpoints — worst-case abuse is triggering
# a Slack report, not touching data. If that trade is not wanted, the alternative is a separate
# read-only secret on Railway; say so and it's a small change.
#
# Idempotent. Safe to re-run.

set -uo pipefail

# THE OPERATOR'S HOME, resolved rather than named — and correct under sudo, where $HOME is root's.
# That distinction is the whole reason this is not just "$HOME": these scripts are run with sudo,
# and a root-relative path would install into /root and appear to work.
OP_HOME=${MOSES_OP_HOME:-$(getent passwd "${SUDO_USER:-$(id -un)}" | cut -d: -f6)}
[ -n "$OP_HOME" ] || { echo "cannot resolve the operator's home; set MOSES_OP_HOME" >&2; exit 1; }
MOSES_ROOT=${MOSES_ROOT:-$OP_HOME/Projects/moses}

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }

REPORT=/usr/local/bin/ops-report
CACHE_DIR=/var/lib/moses/ops
CACHE="$CACHE_DIR/last-report.txt"
SRC_ENV=/etc/moses/moses.env
DEST_DIR=$OP_HOME/.config/moses
DEST_ENV="$DEST_DIR/customs.env"

echo "== 1/2  ops-report: --print mode + report cache =="
if [ ! -f "$REPORT" ]; then
    echo "   $REPORT not found — skipping" >&2
elif grep -q "last-report.txt" "$REPORT"; then
    echo "   already patched, nothing to do"
else
    cp -a "$REPORT" "$REPORT.bak.$(date +%Y%m%d-%H%M%S)"
    echo "   backed up to $REPORT.bak.*"
    if python3 - "$REPORT" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()

# --print must not be mistaken for the mode. Someone will eventually type `ops-report --print`
# with no mode and should get the morning report, not a usage error.
old_mode = 'MODE="${1:-morning}"'
new_mode = '''PRINT=0
for _a in "$@"; do [ "$_a" = "--print" ] && PRINT=1; done
MODE="${1:-morning}"
[ "$MODE" = "--print" ] && MODE=morning'''
if old_mode not in s:
    sys.exit("could not find the MODE assignment — file changed shape; not patching")
s = s.replace(old_mode, new_mode, 1)

# The tail. `ops-jobs say` is the only posting call. The cache is written on EVERY run — including
# the scheduled root run that posts — so the cached text is always exactly what #ops received.
# Written to a temp file and moved, so a reader never sees a half-written report.
old_tail = 'ops-jobs say "$MSG"'
new_tail = '''mkdir -p /var/lib/moses/ops
_tmp=$(mktemp /var/lib/moses/ops/.last-report.XXXXXX) && {
  printf '%s\\n' "$MSG" > "$_tmp"
  chmod 644 "$_tmp"
  mv -f "$_tmp" /var/lib/moses/ops/last-report.txt
}

if [ "${PRINT:-0}" -eq 1 ]; then
  printf '%s\\n' "$MSG"
else
  ops-jobs say "$MSG"
fi'''
if old_tail not in s:
    sys.exit("could not find the posting call — not patching")
s = s.replace(old_tail, new_tail, 1)

open(p, "w").write(s)
print("   patched")
PY
    then
        bash -n "$REPORT" && echo "   syntax OK"
    else
        echo "   PATCH FAILED — restoring the backup" >&2
        cp -a "$(ls -1t "$REPORT".bak.* | head -1)" "$REPORT"
        exit 1
    fi
fi

# Populate the cache now, as root, so the tool has something correct to read immediately rather
# than waiting for tomorrow's 06:00 run.
echo "   priming the cache (as root, so the backup check is accurate)…"
mkdir -p "$CACHE_DIR"
if "$REPORT" full --print >/dev/null 2>&1 && [ -s "$CACHE" ]; then
    echo "   cache written: $CACHE ($(stat -c %a "$CACHE"), $(wc -l < "$CACHE") lines)"
    echo "   ---- first lines ----"
    head -3 "$CACHE" | sed 's/^/     /'
else
    echo "   ⚠️  cache not written — check $REPORT manually" >&2
fi

echo
echo "== 2/2  Customs credentials for the MCP service =="
# Source BOTH files, in the same order and for the same reason /usr/local/bin/moses does. The first
# version of this script read only moses.env and reported the vars missing — they may well live in
# ops-jobs.env, and a check narrower than the thing it is mirroring will lie about what is configured.
APP_URL="${VIATICA_APP_URL:-}"
SECRET="${CRON_SECRET:-}"
for c in /etc/moses/ops.env "$SRC_ENV"; do
    [ -r "$c" ] || continue
    # shellcheck disable=SC1090
    v=$(. "$c" 2>/dev/null; printf '%s' "${VIATICA_APP_URL:-}"); [ -n "$v" ] && APP_URL="$v"
    v=$(. "$c" 2>/dev/null; printf '%s' "${CRON_SECRET:-}");     [ -n "$v" ] && SECRET="$v"
done

# Allow them to be supplied directly, for the case where Reserve simply never had them: the
# scheduled CFO reports run from GitHub Actions, so these may only exist as GH/Railway secrets.
#   sudo install-persona-tools.sh --app-url https://… --secret <CRON_SECRET>
while [ $# -gt 0 ]; do
    case "$1" in
        --app-url)        APP_URL="${2:-}"; shift 2 ;;
        --secret)         SECRET="${2:-}";  shift 2 ;;
        --knight-channel) KNIGHT_CHANNEL_ARG="${2:-}"; shift 2 ;;
        *) shift ;;
    esac
done

if [ -z "$APP_URL" ] || [ -z "$SECRET" ]; then
    echo "   Missing:$([ -z "$APP_URL" ] && printf ' VIATICA_APP_URL')$([ -z "$SECRET" ] && printf ' CRON_SECRET')" >&2
    echo "   Searched /etc/moses/ops.env and $SRC_ENV. Keys actually present (values hidden):" >&2
    for c in /etc/moses/ops.env "$SRC_ENV"; do
        [ -r "$c" ] || continue
        echo "     $c:" >&2
        grep -oE '^[[:space:]]*(export[[:space:]]+)?[A-Z_][A-Z0-9_]*=' "$c" \
            | sed -E 's/^[[:space:]]*(export[[:space:]]+)?//; s/=$//; s/^/       • /' >&2
    done
    echo "   These are also GitHub Actions secrets (cfo-reports.yml) and Railway vars, so if Reserve" >&2
    echo "   never had them, re-run with:" >&2
    echo "     sudo $0 --app-url https://… --secret <CRON_SECRET>" >&2
    echo "   Big Pipe and Tagilla keep reporting the missing config rather than a wrong number." >&2
    exit 1
fi

install -d -o brad -g brad -m 700 "$DEST_DIR"
umask 077
cat > "$DEST_ENV" <<EOF
# Customs credentials for the Moses MCP service ONLY. Copied from $SRC_ENV by
# install-persona-tools.sh because that file is root-only and the MCP server runs as brad.
# Read-only reach: /api/agents/report on Customs. Re-run this installer after rotating CRON_SECRET.
VIATICA_APP_URL=$APP_URL
CRON_SECRET=$SECRET
EOF
chown brad:brad "$DEST_ENV"
chmod 600 "$DEST_ENV"
echo "   wrote $DEST_ENV ($(stat -c %a:%U:%G "$DEST_ENV"))"
echo "   VIATICA_APP_URL=$APP_URL"
echo "   CRON_SECRET=$(printf '%s' "$SECRET" | cut -c1-4)…[${#SECRET} chars]"

echo
echo "== Knight's Slack credentials =="
# Knight posts his completion report as a PERSONA — a per-message display identity via
# chat:write.customize — so he needs no app, no bot of his own and no reinstall. A reinstall would
# rotate this token and silently break Tagilla and Big Pipe on Railway, which is why the existing
# Reserve token is reused rather than replaced.
#
# Same reasoning as the Customs credentials above: the token is root-only and Knight runs as brad,
# so a brad-owned 0600 copy is made for that use alone.
KNIGHT_ENV=$OP_HOME/.config/moses/knight.env
KNIGHT_CHANNEL_ARG="${KNIGHT_CHANNEL_ARG:-}"
SLACK_TOKEN=""; KNIGHT_CHAN="${KNIGHT_CHANNEL_ARG:-}"

# Precedence: explicit flag > whatever Knight is ALREADY posting to > SLACK_KNIGHT_CHANNEL in the
# root env > #ops. Reading back the existing value matters: this script is meant to be re-run (after
# a CRON_SECRET rotation, say), and without this a re-run with no flag would silently move Knight's
# reports back to #ops. A setting that quietly reverts on an unrelated re-run is the kind of drift
# nobody notices until the reports are going somewhere no one reads.
if [ -z "$KNIGHT_CHAN" ] && [ -r "$KNIGHT_ENV" ]; then
    # shellcheck disable=SC1090
    KNIGHT_CHAN=$(. "$KNIGHT_ENV" 2>/dev/null; printf '%s' "${SLACK_KNIGHT_CHANNEL:-}")
    [ -n "$KNIGHT_CHAN" ] && echo "   keeping Knight's existing channel $KNIGHT_CHAN (pass --knight-channel to change it)"
fi
for c in /etc/moses/ops.env "$SRC_ENV"; do
    [ -r "$c" ] || continue
    # shellcheck disable=SC1090
    v=$(. "$c" 2>/dev/null; printf '%s' "${SLACK_BOT_TOKEN:-}");     [ -n "$v" ] && SLACK_TOKEN="$v"
    # Prefer a channel chosen for Knight; fall back to #ops so this works with zero Slack changes.
    if [ -z "${KNIGHT_CHANNEL_ARG:-}" ]; then
        v=$(. "$c" 2>/dev/null; printf '%s' "${SLACK_KNIGHT_CHANNEL:-}"); [ -n "$v" ] && KNIGHT_CHAN="$v"
    fi
    if [ -z "$KNIGHT_CHAN" ]; then
        v=$(. "$c" 2>/dev/null; printf '%s' "${SLACK_OPS_CHANNEL:-}"); [ -n "$v" ] && KNIGHT_CHAN="$v"
    fi
done

if [ -z "$SLACK_TOKEN" ] || [ -z "$KNIGHT_CHAN" ]; then
    echo "   no Slack token/channel found — Knight will report by pull only (knight_status)" >&2
else
    umask 077
    cat > "$KNIGHT_ENV" <<EOF
# Knight's Slack credentials, copied from the root-only env by install-persona-tools.sh.
# Reuses the existing Reserve bot token; Knight is a per-message persona, not a separate bot.
SLACK_BOT_TOKEN=$SLACK_TOKEN
SLACK_KNIGHT_CHANNEL=$KNIGHT_CHAN
EOF
    chown brad:brad "$KNIGHT_ENV"; chmod 600 "$KNIGHT_ENV"
    echo "   wrote $KNIGHT_ENV ($(stat -c %a:%U:%G "$KNIGHT_ENV")) → channel $KNIGHT_CHAN"

    # Prove it end to end rather than assuming the scope is granted. A token without
    # chat:write.customize accepts the call but IGNORES the custom name, so the check below reads
    # back the posted message's username instead of trusting ok:true.
    resp=$(curl -sS --max-time 15 -X POST https://slack.com/api/chat.postMessage \
             -H "Authorization: Bearer $SLACK_TOKEN" \
             -H 'Content-type: application/json; charset=utf-8' \
             --data "$(jq -nc --arg c "$KNIGHT_CHAN" \
                 '{channel:$c,text:"Knight reporting in. Completion reports will arrive here.",username:"Knight",icon_emoji:":crossed_swords:"}')")
    if printf '%s' "$resp" | jq -e '.ok == true' >/dev/null 2>&1; then
        who=$(printf '%s' "$resp" | jq -r '.message.username // .message.bot_profile.name // "?"')
        echo "   test post accepted, appeared as: $who"
        [ "$who" = "Knight" ] || echo "   ⚠️  posted as '$who', not 'Knight' — the app is missing chat:write.customize" >&2
    else
        echo "   ⚠️  Slack rejected the test post: $(printf '%s' "$resp" | jq -r '.error // "unknown"')" >&2
        echo "      (not_in_channel? invite the bot to that channel and re-run.)" >&2
    fi
fi

echo
echo "== reloading brad's MCP services =="
sudo -u brad XDG_RUNTIME_DIR=/run/user/$(id -u brad) \
    systemctl --user restart moses-mcp.service moses-mcp-public.service \
  && echo "   restarted" \
  || echo "   restart them yourself: systemctl --user restart moses-mcp moses-mcp-public"

echo
echo "Done. Nothing about the scheduled Slack reports changed — same timers, same channels."
