# persona.sh — a persona's display name and emoji come from the ROSTER, not from a string literal.
#
#   . "$(dirname "$0")/../lib/persona.sh"
#   NAME=$(persona_name health)      # "Therapist", or whatever the operator called it
#   EMOJI=$(persona_emoji health)    # ":adhesive_bandage:"
#
# WHY. The roster already declares each persona as `id` + `name` + `emoji` + `charter` + `cadence` —
# the id is the ROLE (health, ops, finance) and the name is what the operator decided to call it.
# Scripts were then hardcoding the name anyway, which made the roster decorative and meant adopting
# this framework required accepting one person's naming scheme. Escape from Tarkov bosses are not
# ubiquitous.
#
# Rename a persona by editing one line of the roster. Nothing else needs to know.
#
# FALLS BACK TO THE ID, CAPITALIZED. A missing roster, a malformed one, or an id that is not in it
# must never stop a persona reporting — a health check that refuses to run because it could not look
# up its own name has failed at the only job that mattered. `health` becomes "Health": plain, but
# correct and obviously un-configured.

persona_roster() {
    for p in "${MOSES_ROSTER:-}" "$HOME/.config/moses/roster.json" /etc/moses/roster.json \
             "$(dirname "${BASH_SOURCE[0]}")/../config/roster.example.json"; do
        [ -n "$p" ] && [ -r "$p" ] && { printf '%s' "$p"; return 0; }
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
        if o.get("id") == want and field in o:
            found = o[field]
        for v in o.values():
            walk(v)
    elif isinstance(o, list):
        for v in o:
            walk(v)
try:
    walk(json.load(open(path)))
except Exception:
    pass
print(found if found else fallback)
PY
}

# The display name. Falls back to the id with its first letter capitalized.
persona_name() {
    local id="$1"
    _persona_field "$id" name "$(printf '%s' "${id^}")"
}

# The emoji, defaulting to something neutral rather than to nothing — Slack renders an empty
# icon_emoji as the app's own avatar, which silently undoes the per-message identity.
persona_emoji() {
    _persona_field "$1" emoji ":robot_face:"
}
