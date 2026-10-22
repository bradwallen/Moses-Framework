#!/usr/bin/env bash
# install-mfa.sh — turn on "a new build needs Brad's one-time code" (#14 A7). Run ONCE, by the operator:
#
#   sudo ~/Projects/moses-mfa/agent/install-mfa.sh
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
#   4. sudoers       challenge, verify, authorized, consume, status — NOT enroll — checked by visudo
#   5. enroll        once: a QR to scan, then a code from the app; a mistyped scan saves nothing
#   6. verify        as the operator: status works without a password, enroll does NOT, and the
#                    test overrides are ignored as root — each one observed, not assumed
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

step "4. sudoers — what the operator may ask without a password"
T=$(mktemp)
cat > "$T" <<EOF
# moses-mfa (#14), installed by agent/install-mfa.sh. What $OP's processes — the Slack listener,
# moses-new-target, knight-land — may ask the one-time code helper without a password.
# enroll is deliberately absent: making or replacing the seed takes the operator's own password.
$OP ALL=(root) NOPASSWD: $HELPER challenge *, $HELPER verify *, $HELPER authorized *, $HELPER consume *, $HELPER status
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

step "6. verify, as $OP"
# -k: ignore any cached sudo session. You just typed your password to run this, and a cached session
# would let both checks below pass for the wrong reason — status without the sudoers line, or enroll
# "refused" by the helper rather than by sudo.
ok=1
st=$(as_op sudo -n -k "$HELPER" status 2>&1)
if grep -q '"enrolled": true' <<<"$st"; then echo "   ✓ status runs without a password and reports enrolled"
else echo "   ✗ status: $st"; ok=0; fi
out=$(as_op sudo -n -k "$HELPER" enroll --rotate </dev/null 2>&1)
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
[ "$ok" = 1 ] || die "verification failed. The helper is installed but Moses was NOT changed. Remove $SUDOERS to disable it."

step "7. land $BRANCH"
if [ "$MERGED" = 1 ]; then
    echo "   already merged — nothing to do"
else
    before=$(as_op git -C "$LIVE" rev-parse HEAD)
    as_op git -C "$LIVE" merge -q --no-ff -m "Land $BRANCH (#14: a new build needs Brad's one-time code), installed by $OP" "$BRANCH" \
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
echo "   Every attempt is in /var/lib/moses/mfa/audit.jsonl."
