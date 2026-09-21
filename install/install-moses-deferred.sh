#!/usr/bin/env bash
# install-moses-deferred.sh — give Moses a memory for decisions that were consciously postponed.
#
#   sudo $OP_HOME/scripts/install-moses-deferred.sh
#
# THE GAP THIS CLOSES (Brad, 2026-08-11): *"I will want that at some point but I won't remember this
# is even a thing we can do or may not even notice when I'd need it."*
#
# Everything Moses has today WAITS TO BE ASKED. `recall`, the transcript index, `ideas.md` — all
# excellent at answering a question, all silent when nobody asks one. A deferred decision is exactly
# the case with no natural moment where anyone thinks to search: the trigger arrives months later,
# disguised as ordinary work, and the earlier reasoning is never retrieved. The discovery sweep does
# go looking, but it hunts cross-project overlap, not postponed decisions with conditions attached.
#
# So this is small and literal: items live in the memory corpus as data, the standup evaluates them
# against the clock, and due ones are named in the morning briefing.
#
# IT MUST SURFACE ON A CLEAN MORNING. The standup is exception-only by design, and that design is
# right — but a deferred item is not an exception, it is a reminder, and a quiet morning is precisely
# when one would otherwise stay buried for another year. So a due item breaks the silence on its own.
#
# Idempotent; backs up the moses CLI; restores on syntax error.

set -euo pipefail

# THE OPERATOR'S HOME, resolved rather than named — and correct under sudo, where $HOME is root's.
# That distinction is the whole reason this is not just "$HOME": these scripts are run with sudo,
# and a root-relative path would install into /root and appear to work.
OP_HOME=${MOSES_OP_HOME:-$(getent passwd "${SUDO_USER:-$(id -un)}" | cut -d: -f6)}
[ -n "$OP_HOME" ] || { echo "cannot resolve the operator's home; set MOSES_OP_HOME" >&2; exit 1; }
MOSES_ROOT=${MOSES_ROOT:-$OP_HOME/Projects/moses}

[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }

MOSES=/usr/local/bin/moses
DEFERRED=$OP_HOME/.claude/memory/deferred.md
STATE=/var/lib/moses
STAMP=$(date +%Y%m%d-%H%M%S)

[ -r "$MOSES" ] || { echo "missing $MOSES" >&2; exit 1; }
[ -r "$DEFERRED" ] || { echo "missing $DEFERRED — expected the item file in the memory corpus" >&2; exit 1; }

cp -a "$MOSES" "$MOSES.bak-$STAMP"
echo "backed up  $MOSES.bak-$STAMP"

mkdir -p "$STATE"
# State lives OUTSIDE the memory corpus on purpose: the corpus is declarative and backed up nightly,
# and "when did I last mention this" is neither. Keeping them apart means restoring a backup never
# resurrects a stale surfaced-at timestamp.
touch "$STATE/deferred-surfaced"
chmod 644 "$STATE/deferred-surfaced"
echo "state      $STATE/deferred-surfaced"

python3 - "$MOSES" <<'PY'
import sys

path = sys.argv[1]
s = open(path).read()

if "cmd_deferred()" in s:
    print("moses      already patched — no change")
    sys.exit(0)

block = '''
# ── Deferred decisions ────────────────────────────────────────────────────────
# Things consciously NOT done, with the condition that should bring them back. Declared in
# ~/.claude/memory/deferred.md — data in the corpus, evaluation here, the same split the roster uses.
#
# The vocabulary is two keys, `every: <N>d` and `after: <YYYY-MM-DD>`, and that is deliberate: a
# richer trigger language would be a second scheduler to debug, and a scheduler nobody debugs fails
# silently — which is the exact failure this is meant to prevent.
DEFERRED_FILE=$OP_HOME/.claude/memory/deferred.md
DEFERRED_STATE=/var/lib/moses/deferred-surfaced

# Emits one "id<TAB>title<TAB>why" line per item that is due now. Silence means nothing is due.
deferred_due() {
  [ -r "$DEFERRED_FILE" ] || return 0
  python3 - "$DEFERRED_FILE" "$DEFERRED_STATE" <<'PYEOF'
import re, sys, time, datetime

items, cur = [], None
for line in open(sys.argv[1]):
    m = re.match(r"^##\\s+(.*)", line)
    if m:
        title = m.group(1).strip()
        # A title marked [closed] stays in the file as a record of the decision, but stops nagging.
        cur = None if "[closed]" in title.lower() else {"title": title}
        if cur is not None:
            items.append(cur)
        continue
    if cur is None:
        continue
    m = re.match(r"^-\\s*(id|every|after|since|why|topic):\\s*(.*)", line)
    if m:
        cur[m.group(1)] = m.group(2).strip()
    elif cur.get("why") and line.startswith("  "):
        cur["why"] += " " + line.strip()

try:
    seen = dict(l.split(None, 1) for l in open(sys.argv[2]) if l.strip())
except Exception:
    seen = {}

now = time.time()
today = datetime.date.today()
for it in items:
    iid = it.get("id")
    if not iid:
        continue
    due = False
    if "after" in it:
        try:
            # A one-shot date item keeps surfacing until it is closed — the point is that Brad acts
            # on it, and a reminder that fires once into a busy morning has not achieved that.
            due = datetime.date.fromisoformat(it["after"]) <= today
        except ValueError:
            due = False
    if "every" in it:
        m = re.match(r"(\\d+)\\s*d", it["every"])
        if m:
            last = float(seen.get(iid, 0) or 0)
            due = due or (now - last) >= int(m.group(1)) * 86400
    if due:
        why = re.sub(r"\\s+", " ", it.get("why", "")).strip()
        print(f"{iid}\\t{it['title']}\\t{why}")
PYEOF
}

# Records that an item was surfaced, so `every: Nd` measures from the last MENTION rather than from
# the deferral date — otherwise every item nags every single morning forever after its first due day.
deferred_mark() {
  local id=$1 tmp
  mkdir -p "$(dirname "$DEFERRED_STATE")"
  tmp=$(mktemp)
  { grep -v "^$id " "$DEFERRED_STATE" 2>/dev/null || true; echo "$id $NOW"; } > "$tmp"
  mv -f "$tmp" "$DEFERRED_STATE"
  chmod 644 "$DEFERRED_STATE" 2>/dev/null || true
}

cmd_deferred() {
  local out
  out=$(deferred_due)
  if [ -z "$out" ]; then
    echo "moses: nothing deferred is due"
    return 0
  fi
  while IFS=$'\\t' read -r id title why; do
    [ -n "$id" ] || continue
    printf '  • %s\\n    %s\\n' "$title" "$why"
  done <<< "$out"
}

'''

anchor = "cmd_standup() {"
assert anchor in s, "cmd_standup not found"
s = s.replace(anchor, block.lstrip(chr(10)) + anchor, 1)

# Collect due items during the standup, alongside the persona checks.
old = '''  mkdir -p "$STATE" && echo "$NOW" > "$HEARTBEAT"
  record "moses" 1 "standup"'''
new = '''  mkdir -p "$STATE" && echo "$NOW" > "$HEARTBEAT"
  record "moses" 1 "standup"

  # Deferred decisions that have come due. Collected here so they are part of the same briefing
  # rather than a second message Brad has to correlate.
  local deferred="" dline
  while IFS=$'\\t' read -r did dtitle dwhy; do
    [ -n "$did" ] || continue
    deferred="$deferred
   • *$dtitle*
     $dwhy"
    deferred_mark "$did"
  done < <(deferred_due)'''
assert old in s, "heartbeat block not found"
s = s.replace(old, new, 1)

# Surface them even when the standup is otherwise clean — the whole point.
old_clean = '''  if [ -z "$failures" ] && [ -z "$overdue" ] && [ -z "$gap" ]; then
    echo "moses: standup clean — nothing to report (by design)"
    return 0
  fi'''
new_clean = '''  if [ -z "$failures" ] && [ -z "$overdue" ] && [ -z "$gap" ]; then
    # A clean standup normally says nothing, and that restraint is correct. A deferred decision is
    # not an exception though — it is a reminder — and a quiet morning is exactly when one would
    # otherwise stay buried. So it is worth breaking the silence for, and only for.
    if [ -n "$deferred" ]; then
      say_msg "🗓️ *Worth revisiting*$deferred"
      return 0
    fi
    echo "moses: standup clean — nothing to report (by design)"
    return 0
  fi'''
assert old_clean in s, "clean-standup block not found"
s = s.replace(old_clean, new_clean, 1)

# And append them to a report that already has content.
old_tail = '''  local msg="📋 *Standup — someone's behind*"'''
new_tail = '''  local msg="📋 *Standup — someone's behind*"
  DEFERRED_TAIL="$deferred"'''
assert old_tail in s, "standup message header not found"
s = s.replace(old_tail, new_tail, 1)

s = s.replace("  standup)        cmd_standup ;;",
              "  standup)        cmd_standup ;;" + chr(10) + "  deferred)       cmd_deferred ;;", 1)

open(path, "w").write(s)
print("moses      deferred decisions wired into the standup")
PY

# The standup posts through a helper; make sure the deferred tail rides along with it.
python3 - "$MOSES" <<'PY'
import re, sys
path = sys.argv[1]
s = open(path).read()
if 'DEFERRED_TAIL' in s and 'Worth revisiting*$DEFERRED_TAIL' not in s:
    # Append the block to the assembled message just before it is sent.
    m = re.search(r'\n(\s*)(say_msg|ops-jobs say|post)\s+"\$msg"', s)
    if m:
        indent = m.group(1)
        inject = (f'\n{indent}[ -n "${{DEFERRED_TAIL:-}}" ] && '
                  f'msg="$msg\n\n🗓️ *Worth revisiting*$DEFERRED_TAIL"\n')
        s = s[:m.start()] + inject + s[m.start():]
        open(path, "w").write(s)
        print("moses      deferred items appended to a non-empty standup too")
    else:
        print("moses      NOTE: could not find the standup send call — due items still surface on a clean morning")
PY

bash -n "$MOSES" || { echo "SYNTAX ERROR — restoring" >&2; cp -a "$MOSES.bak-$STAMP" "$MOSES"; exit 1; }

echo
echo "What is due right now:"
echo "────────────────────────────────────────────"
"$MOSES" deferred 2>&1 | sed 's/^/  /'
echo "────────────────────────────────────────────"
echo
echo "Done. Add items to $DEFERRED — 'moses deferred' lists what is due."
