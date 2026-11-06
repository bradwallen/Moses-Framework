#!/usr/bin/env bash
# install-mfa.sh — turn on Brad's one-time code: for new builds (#14) and for a task's root work (#17).
# Run by the operator, from the worktree whose branch it lands:
#
#   sudo ~/Projects/moses-task-grants/agent/install-mfa.sh
#
# It needs a password because it installs a root helper and a sudoers line, and it is NEVER to be
# added to sudoers itself: a password-free script that copies operator-writable files into
# /usr/local/bin is the hole §7b closed. Running it is also the decision to LAND the work: the branch
# it was built on is merged into the live tree and Moses is restarted, the same as `knight-land`.
#
# ORDER, and why. Nothing changes until every check that can run first has passed:
#   1. preflight     the live tree is clean and on main, the branch merges cleanly, the suite is green
#   2. qrencode      so enrollment can show a QR (falls back to typing the secret if apt cannot)
#   3. the helper    /usr/local/bin/moses-mfa, root-owned, compared byte for byte with its source
#   4. sudoers       challenge, verify, authorized, consume, status, verify-slack [--wait], do, grants, revoke —
#                    NOT enroll — checked by visudo
#   5. enroll        once: a QR to scan, then a code from the app; a mistyped scan saves nothing
#   6. verify        as the operator: status, grants work without a password, enroll and anything else
#                    as root do NOT, and the test overrides are ignored as root — each observed
#   6a. Moses Gate   the separate Slack app's token, root's alone; refused if it is a bot the operator
#                    can already post as; it must post in its channel and read its own thread
#   6b. lxd          the operator leaves the lxd group (root without sudo, #17 MF6)
#   7. land          merge the branch into main in the live tree (--no-ff, one revertable unit)
#   8. restart       moses, moses-mcp, moses-mcp-public; if any does not come back, the merge is
#                    reverted and they are restarted on the old code, and it says so
# Re-running is safe: an installed seed is kept, an identical helper is reinstalled, a merged
# branch is not merged twice.
set -uo pipefail
step() { printf '\n== %s\n' "$*"; }
die()  { printf '\ninstall-mfa: STOPPED — %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run it with sudo."
OP=${SUDO_USER:-}
[ -n "$OP" ] && [ "$OP" != root ] || die "run it through sudo as the operator, not as root directly."
OP_HOME=$(getent passwd "$OP" | cut -d: -f6)
OP_UID=$(id -u "$OP")
SRC=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
LIVE=${MOSES_LIVE:-$OP_HOME/Projects/moses}
HELPER=/usr/local/bin/moses-mfa
SUDOERS=/etc/sudoers.d/moses-mfa
as_op() { runuser -u "$OP" -- env HOME="$OP_HOME" XDG_RUNTIME_DIR="/run/user/$OP_UID" "$@"; }
BRANCH=$(as_op git -C "$SRC" rev-parse --abbrev-ref HEAD)

step "1. preflight — nothing is changed until all of this passes"
[ -d "$LIVE/.git" ] || die "no live Moses tree at $LIVE (set MOSES_LIVE)."
[ "$(as_op git -C "$LIVE" rev-parse --abbrev-ref HEAD)" = main ] || die "$LIVE is not on main."
# THE SOURCE MUST BE EXACTLY THE COMMITTED BRANCH. Root installs the helper from it, and the
# suite below tests it; an uncommitted edit here would be tested and installed while a different,
# committed version is merged. So a dirty source is a refusal, and the helper comes from git itself.
sdirty=$(as_op git -C "$SRC" status --porcelain | head -5)
[ -z "$sdirty" ] || die "$SRC has uncommitted changes, so what would be installed is not what would be merged:
$sdirty"
dirty=$(as_op git -C "$LIVE" status --porcelain | head -5)
[ -z "$dirty" ] || die "$LIVE has uncommitted changes, so nothing was installed:
$dirty"
if as_op git -C "$LIVE" merge-base --is-ancestor "$BRANCH" main; then
    MERGED=1; echo "   $BRANCH is already in main"
else
    MERGED=0
    as_op git -C "$LIVE" merge-tree --write-tree main "$BRANCH" >/dev/null 2>&1 \
        || die "$BRANCH no longer merges cleanly into main. Nothing was installed."
    echo "   $BRANCH merges cleanly into main"
fi
as_op "$SRC/agent/mfa-suite" | sed 's/^/   /'
[ "${PIPESTATUS[0]}" = 0 ] || die "the suite is red against the source. Nothing was installed."

step "2. qrencode, for the enrollment QR"
if [ -x /usr/bin/qrencode ]; then echo "   already installed"
elif DEBIAN_FRONTEND=noninteractive apt-get install -y -qq qrencode >/dev/null 2>&1 && [ -x /usr/bin/qrencode ]; then
    echo "   installed"
else
    echo "   could not install it; enrollment will show the secret to type into the app instead"
fi

step "3. the helper"
COMMITTED=$(mktemp)
as_op git -C "$SRC" show "$BRANCH:agent/moses-mfa" > "$COMMITTED" || die "could not read agent/moses-mfa from $BRANCH."
install -o root -g root -m 0755 "$COMMITTED" "$HELPER.new" && mv -f "$HELPER.new" "$HELPER" \
    || { rm -f "$COMMITTED"; die "could not install $HELPER."; }
cmp -s "$COMMITTED" "$HELPER" || { rm -f "$COMMITTED"; die "$HELPER differs from $BRANCH's agent/moses-mfa after installing."; }
rm -f "$COMMITTED"
install -d -o root -g root -m 0700 /etc/moses/mfa
install -d -o root -g root -m 0755 /var/lib/moses/mfa
echo "   $HELPER (root, 0755) · seed dir /etc/moses/mfa (0700) · ledger dir /var/lib/moses/mfa"

step "4. sudoers — what the account that runs Moses may ask without a password"
# MF8: the operator keeps this line until mf8-retire.sh has run, so a rerun during the soak changes nothing
# the cutover's rollback relies on. After the retire, the line names the account in /etc/moses/agent-user,
# so a rerun never hands the operator back the rights the retire took away (review, 2026-11-06).
ASKER=$OP
[ -e /var/lib/moses/mf8-retired ] && ASKER=$(cat /etc/moses/agent-user 2>/dev/null)
ASKER_UID=$(id -u "$ASKER" 2>/dev/null) || die "/etc/moses/agent-user names '$ASKER', which is not an account."
ASKER_HOME=$(getent passwd "$ASKER" | cut -d: -f6)
# Step 6 asks sudo AS the account the line names: asked as anyone else, every check is refused, and the
# "enroll is refused" check could no longer fail.
as_ask() { runuser -u "$ASKER" -- env HOME="$ASKER_HOME" XDG_RUNTIME_DIR="/run/user/$ASKER_UID" PATH=/usr/local/bin:/usr/bin:/bin "$@"; }
T=$(mktemp)
cat > "$T" <<EOF
# moses-mfa (#14), installed by agent/install-mfa.sh. What $ASKER's processes — the Slack listener,
# moses-new-target, knight-land — may ask the one-time code helper without a password.
# enroll is deliberately absent: making or replacing the seed takes the operator's own password.
$ASKER ALL=(root) NOPASSWD: $HELPER challenge *, $HELPER verify *, $HELPER authorized *, $HELPER consume *, $HELPER status, $HELPER verify-slack, $HELPER verify-slack --wait, $HELPER do *, $HELPER grants, $HELPER revoke *
EOF
visudo -cqf "$T" || { rm -f "$T"; die "the sudoers line did not pass visudo; nothing was written to $SUDOERS."; }
install -o root -g root -m 0440 "$T" "$SUDOERS"; rm -f "$T"
visudo -cq || die "sudoers as a whole no longer parses after adding $SUDOERS — remove it by hand: sudo rm $SUDOERS"
echo "   $SUDOERS (0440), checked by visudo"

step "5. enroll"
if [ -s /etc/moses/mfa/totp.seed ]; then
    echo "   already enrolled — keeping the seed your app already has"
else
    echo "   Open your authenticator app (Google Authenticator, 1Password, Authy…) and add a new code."
    "$HELPER" enroll </dev/tty || die "enrollment did not finish. The helper and sudoers line are installed; run this again to enroll."
fi

step "6. verify, as $ASKER"
# -k: ignore any cached sudo session. You just typed your password to run this, and a cached session
# would let both checks below pass for the wrong reason — status without the sudoers line, or enroll
# "refused" by the helper rather than by sudo.
ok=1
st=$(as_ask sudo -n -k "$HELPER" status 2>&1)
if grep -q '"enrolled": true' <<<"$st"; then echo "   ✓ status runs without a password and reports enrolled"
else echo "   ✗ status: $st"; ok=0; fi
out=$(as_ask sudo -n -k "$HELPER" enroll --rotate </dev/null 2>&1)
# REFUSED BY SUDO, in whatever words this sudo uses: classic sudo says "a password is required",
# sudo-rs (this box, 2026-10-17) says "interactive authentication is required". The first install
# matched only the first wording and stopped on a refusal that had worked. What matters is that sudo
# answered and the helper never ran — its output would name itself or the authenticator.
if grep -q '^sudo:' <<<"$out" && ! grep -qiE 'moses-mfa|authenticator|enrolled' <<<"$out"; then
    echo "   ✓ enroll is refused by sudo without a password (watched failing, as it should)"
else
    echo "   ✗ enroll was not refused by sudo — the sudoers line is too wide: $(head -1 <<<"$out")"; ok=0
fi
st=$(env MOSES_MFA_SEED=/nonexistent MOSES_MFA_STATE=/nonexistent "$HELPER" status 2>&1)
if grep -q '"enrolled": true' <<<"$st"; then echo "   ✓ as root, the test overrides are ignored"
else echo "   ✗ as root the helper honored a test override: $st"; ok=0; fi
# Task grants (#17): the new operations answer without a password, and `run` with no grant is the
# HELPER's refusal (125), not sudo's — watched both ways, so a sudoers line that is too narrow or too
# wide both show here.
out=$(as_ask sudo -n -k "$HELPER" grants 2>&1)
if [ "${out:0:1}" = "[" ]; then echo "   ✓ grants runs without a password"
else echo "   ✗ grants: $out"; ok=0; fi
out=$(as_ask sudo -n -k "$HELPER" do no-such-grant 1 2>&1); rc=$?
if [ "$rc" = 125 ] && grep -q "no task grant" <<<"$out"; then echo "   ✓ do with no grant is refused by the helper (125), not by sudo"
else echo "   ✗ do with no grant: exit $rc, $(head -1 <<<"$out")"; ok=0; fi
out=$(as_ask sudo -n -k /usr/bin/true 2>&1); rc=$?
if [ "$rc" != 0 ]; then echo "   ✓ anything else as root still needs your password (watched failing, as it should)"
else echo "   ✗ the operator ran /usr/bin/true as root with no password — some sudoers rule is too wide"; ok=0; fi
[ "$ok" = 1 ] || die "verification failed. The helper is installed but Moses was NOT changed. Remove $SUDOERS to disable it."

step "6a. the Moses Gate — root's own path to your eyes (#17)"
# Task codes are asked for and read by ROOT, through a Slack app whose token only root can read. If the
# operator could read the token, any process could post a fake task and read your reply, and the gate
# would bind nothing — so the Moses bot's own token is refused here.
GATE=/etc/moses/mfa/slack-gate.env
gate_get() { grep -E "^$1=" "$GATE" 2>/dev/null | head -1 | cut -d= -f2-; }
slack_api() {  # slack_api TOKEN METHOD [k=v…] — prints the JSON reply
    # The token reaches curl on STDIN (`-H @-`), never on its command line: /proc here is readable by
    # every user, and a token in root's argv would be the operator's to copy (third review, 2026-11-04).
    local tok=$1 m=$2; shift 2
    local args=(); for kv in "$@"; do args+=(--data-urlencode "$kv"); done
    printf 'Authorization: Bearer %s\n' "$tok" | curl -sS -m 20 -H @- "${args[@]}" "https://slack.com/api/$m"
}
if [ ! -s "$GATE" ]; then
    OPENV=$OP_HOME/.config/moses/moses.env
    def_owner=$(grep -E '^MOSES_OWNER_SLACK_ID=' "$OPENV" 2>/dev/null | cut -d= -f2- | tr -d '"'"'")
    echo "   Paste the Moses Gate app's Bot User OAuth Token (xoxb-…). It is not shown, and only root keeps it."
    read -r -s -p "   token: " gtok </dev/tty; echo
    echo "   The channel must be one only you and the Moses Gate are in (a private channel), so no bot can read your code."
    read -r -p "   channel id for code requests: " gchan </dev/tty
    read -r -p "   your Slack user id [${def_owner}]: " gown </dev/tty; gown=${gown:-$def_owner}
    [ -n "$gtok" ] && [ -n "$gchan" ] && [ -n "$gown" ] || die "the token, channel and user id are all needed. Nothing was saved."
    T=$(mktemp); chmod 600 "$T"
    printf 'SLACK_GATE_TOKEN=%s\nSLACK_GATE_CHANNEL=%s\nSLACK_OWNER_ID=%s\n' "$gtok" "$gchan" "$gown" > "$T"
    install -o root -g root -m 0600 "$T" "$GATE"; rm -f "$T"; unset gtok
fi
[ "$(stat -c '%U %a' "$GATE")" = "root 600" ] || die "$GATE must be root's, mode 600."
gtok=$(gate_get SLACK_GATE_TOKEN)
who=$(slack_api "$gtok" auth.test)
grep -q '"ok":true' <<<"$who" || die "Slack did not accept the Moses Gate token: $(head -c 200 <<<"$who"). Fix $GATE (or delete it and run this again)."
gate_bot=$(sed -n 's/.*"bot_id":"\([^"]*\)".*/\1/p' <<<"$who")
gate_user=$(sed -n 's/.*"user_id":"\([^"]*\)".*/\1/p' <<<"$who")
# EVERY Slack token the operator can read — any *TOKEN* setting in their Moses config, and any xoxp user
# token anywhere in it — must be a different bot from the gate, and none may be a user token for Brad.
op_bots=()
while IFS= read -r optok; do
    [ -n "$optok" ] || continue
    [ "$optok" != "$gtok" ] || die "the Moses Gate token is also in the operator's config. Create a SEPARATE Slack app for the gate."
    case "$optok" in xoxp-*) die "a Slack USER token is in the operator's config; with it, any process could post as you. Remove it first." ;; esac
    oinfo=$(slack_api "$optok" auth.test)
    obot=$(sed -n 's/.*"bot_id":"\([^"]*\)".*/\1/p' <<<"$oinfo")
    [ -z "$obot" ] || [ "$obot" != "$gate_bot" ] || die "the Moses Gate token belongs to a bot the operator already has a token for. Create a SEPARATE Slack app."
    ou=$(sed -n 's/.*"user_id":"\([^"]*\)".*/\1/p' <<<"$oinfo"); [ -n "$ou" ] && op_bots+=("$ou")
done < <(grep -rhoE '^[A-Z_]*TOKEN[A-Z_]*=.*' "$OP_HOME"/.config/moses/ 2>/dev/null | cut -d= -f2- | tr -d '"'"'" | grep -E '^xox[a-z]-' | sort -u)
# The gate channel must hold nobody but Brad and the gate: a bot that can read Brad's code could try it
# against another request before it is spent.
members=$(slack_api "$gtok" conversations.members "channel=$(gate_get SLACK_GATE_CHANNEL)")
grep -q '"ok":true' <<<"$members" || die "the gate cannot list its channel's members ($(head -c 200 <<<"$members")). Give it channels:read / groups:read."
others=$(grep -oE '"U[A-Z0-9]+"' <<<"$members" | tr -d '"' | grep -vx -e "$(gate_get SLACK_OWNER_ID)" -e "$gate_user" | tr '\n' ' ')
[ -z "$others" ] || die "the gate channel has members besides you and the gate ($others). Use a private channel with just the two of you."
echo "   ✓ the gate token works, is root's alone (600), and is not a bot the operator can post as"
posted=$(slack_api "$gtok" chat.postMessage "channel=$(gate_get SLACK_GATE_CHANNEL)" \
         "text=🔐 Moses Gate here. When Moses needs root, I'll ask in this channel. Your code only counts as a reply in my thread.")
grep -q '"ok":true' <<<"$posted" || die "the gate could not post in its channel ($(head -c 200 <<<"$posted")). Invite the Moses Gate app to that channel and run this again."
pts=$(sed -n 's/.*"ts":"\([^"]*\)".*/\1/p' <<<"$posted" | head -1)
hist=$(slack_api "$gtok" conversations.replies "channel=$(gate_get SLACK_GATE_CHANNEL)" "ts=$pts")
grep -q '"ok":true' <<<"$hist" || die "the gate cannot read its own thread ($(head -c 200 <<<"$hist")). Give the app channels:history (groups:history for a private channel) and reinstall it to the workspace."
echo "   ✓ the gate posts in its channel and can read replies to its own posts"
# Root must never read a file FOR the operator that the operator could not read (review, 2026-11-04):
# watched refusing on the seed itself, before anything is posted.
# A DUMMY root-only file, never the seed: if this guard were broken, what it read would be posted.
probe=/etc/moses/mfa/.read-as-caller-probe
printf 'not a secret\n' > "$probe"; chown root:root "$probe"; chmod 600 "$probe"
spec="{\"title\":\"installer check: must be refused\",\"actions\":[{\"type\":\"install-file\",\"source\":\"$probe\",\"dest\":\"/etc/logrotate.d/x\"}]}"
out=$(as_ask sudo -n -k "$HELPER" challenge task <<<"$spec" 2>&1); rc=$?
rm -f "$probe"
if [ "$rc" != 0 ] && grep -q "as the person who asked" <<<"$out"; then echo "   ✓ root will not read a root-only file for the operator (install-file reads as the caller)"
else die "the helper read a root-only file for the operator: exit $rc, $(head -1 <<<"$out")"; fi
unset gtok optok

step "6b. no way to root around the code (#17 MF6)"
# Membership in the lxd group is root without sudo whenever the LXD daemon runs, which would make the
# code gate decorative. LXD is not used on Reserve (its daemon was inactive on 2026-11-04).
if id -nG "$OP" | tr ' ' '\n' | grep -qx lxd; then
    gpasswd -d "$OP" lxd >/dev/null && echo "   removed $OP from the lxd group"
else
    echo "   $OP is not in the lxd group"
fi
for u in lxd.socket lxd.service snap.lxd.daemon.unix.socket snap.lxd.daemon.service; do
    systemctl list-unit-files "$u" >/dev/null 2>&1 && systemctl disable --now "$u" >/dev/null 2>&1 && echo "   disabled $u"
done
if getent group lxd | cut -d: -f4 | tr ',' '\n' | grep -qx "$OP"; then
    die "$OP is still in the lxd group. Remove it by hand: sudo gpasswd -d $OP lxd"
fi
echo "   ✓ $OP is not in lxd. Processes started before now keep the old groups until your next login or a reboot."

step "7. land $BRANCH"
if [ "$MERGED" = 1 ]; then
    echo "   already merged — nothing to do"
else
    before=$(as_op git -C "$LIVE" rev-parse HEAD)
    as_op git -C "$LIVE" merge -q --no-ff -m "Land $BRANCH (Brad's one-time code: new builds #14, task grants #17), installed by $OP" "$BRANCH" \
        || { as_op git -C "$LIVE" merge --abort >/dev/null 2>&1; die "the merge did not apply; it was backed out and Moses is unchanged."; }
    echo "   merged: $(as_op git -C "$LIVE" log -1 --format='%h %s')"
fi

step "8. restart Moses on the new code"
UNITS=(moses moses-mcp moses-mcp-public)
up() { local u; for u in "${UNITS[@]}"; do as_op systemctl --user is-active --quiet "$u" || return 1; done; }
as_op systemctl --user restart "${UNITS[@]}"
sleep 8
if up; then
    echo "   ✓ ${UNITS[*]} are running"
elif [ "$MERGED" = 0 ]; then
    echo "   ✗ a unit did not come back — reverting the merge and restarting on the old code"
    as_op git -C "$LIVE" revert -m 1 --no-edit HEAD >/dev/null 2>&1
    as_op systemctl --user restart "${UNITS[@]}"; sleep 8
    up && die "Moses is back on the old code (the merge was reverted). The new code failed to start: journalctl --user -u moses -n 30" \
       || die "Moses did not come back even on the old code: journalctl --user -u moses -n 30"
else
    die "a unit is not running: journalctl --user -u moses -n 30"
fi

step "done"
echo "   New builds now need your code. In Slack: \"Moses, build #13\" — he asks for the code,"
echo "   you reply with the six digits, and he creates the project and starts Knight on it."
echo "   Root work for a task: a session runs \`agent/moses-task request \"<title>\" --unit restart:x.service …\`,"
echo "   the Moses Gate posts the actions in #ops, you reply in its thread with the code, and the session"
echo "   may do exactly those actions, as root, until the grant expires. Every attempt is in"
echo "   /var/lib/moses/mfa/audit.jsonl."
