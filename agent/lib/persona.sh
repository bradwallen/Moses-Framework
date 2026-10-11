# persona.sh — a persona's display name, emoji and title come from the ROSTER, not from a literal.
#
#   . "$(dirname "$0")/lib/persona.sh"
#   NAME=$(persona_name birdeye)      # "Birdeye", or whatever this operator called it
#   EMOJI=$(persona_emoji birdeye)    # ":eagle:"
#   TITLE=$(persona_title birdeye)    # "Ops"
#
# The shell twin of persona.py: same search order, same fallback. The id is what the code knows; the
# name is what the operator chose. Rename a persona by editing one line of the roster.
#
# WHERE THE ROSTER IS, first readable wins: $MOSES_ROSTER, $MOSES_CONFIG_DIR/roster.json (set by
# moses-env.sh when it has been sourced), ~/.config/moses/roster.json, /etc/moses/roster.json, this
# tree's agent/roster.json, and the framework's config/roster.example.json.
#
# FALLS BACK TO THE ID, CAPITALIZED. A missing roster, a corrupt one, or an id that is not in it must
# never stop a persona reporting: "birdeye" becomes "Birdeye" — plain, but correct and visibly
# un-configured.

persona_roster() {
    local here; here=$(dirname "${BASH_SOURCE[0]}")
    local p
    for p in "${MOSES_ROSTER:-}" \
             "${MOSES_CONFIG_DIR:+$MOSES_CONFIG_DIR/roster.json}" \
             "$HOME/.config/moses/roster.json" \
             /etc/moses/roster.json \
             "$here/../roster.json" \
             "$here/../../config/roster.example.json"; do
        [ -n "$p" ] && [ -f "$p" ] && [ -r "$p" ] && { printf '%s' "$p"; return 0; }
    done
    return 1
}

_persona_field() {   # $1 = id, $2 = field, $3 = fallback
    local roster; roster=$(persona_roster) || { printf '%s' "$3"; return 0; }
    python3 - "$roster" "$1" "$2" "$3" <<'PY' 2>/dev/null || printf '%s' "$3"
import json, sys
path, want, field, fallback = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
found = None
def walk(o):
    global found
    if isinstance(o, dict):
        if o.get("id") == want and "name" in o and isinstance(o.get(field), str) and o[field].strip():
            found = o[field].strip()
        for v in o.values():
            walk(v)
    elif isinstance(o, list):
        for v in o:
            walk(v)
try:
    walk(json.load(open(path, encoding="utf-8")))
except Exception:
    pass
sys.stdout.write(found if found else fallback)
PY
}

# The display name. Falls back to the id with its first letter capitalized.
persona_name() {
    local id="$1"
    _persona_field "$id" name "${id^}"
}

# The emoji, defaulting to something neutral rather than to nothing — Slack renders an empty
# icon_emoji as the app's own avatar, which silently undoes the per-message identity.
persona_emoji() {
    _persona_field "$1" emoji ":robot_face:"
}

# The one-line title. Falls back to empty.
persona_title() {
    _persona_field "$1" title ""
}
