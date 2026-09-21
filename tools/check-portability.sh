#!/usr/bin/env bash
# check-portability.sh — how far is this from being usable by someone who is not Brad?
#
#   tools/check-portability.sh              count what is still personal, exit 0 (ADVISORY)
#   tools/check-portability.sh --strict     exit 1 if anything is found (once the count hits zero)
#   tools/check-portability.sh --show CAT   list every occurrence in one category
#
# WHY THIS EXISTS. The framework and its author's instance are the SAME TREE — his setup is this
# repo plus his config, never a fork, because the moment there are two copies upstream rots. That
# makes the rule one sentence: **anything personal appearing in framework code is a bug.**
#
# A rule is prose, and prose is the weakest layer. This is the mechanism.
#
# IT STARTS ADVISORY ON PURPOSE. A guard that fails on day one against 100 hits gets disabled on day
# two, and a disabled guard protects nothing. So it prints a countdown instead: fix a few whenever
# you are already in that area, watch the number fall, and flip --strict on in the pre-push hook the
# day it reaches zero. That is also the honest definition of "it is a framework now" — a measurement
# rather than a feeling.
#
# WHAT IS *NOT* A LEAK: "Moses" is the framework's own name. Role ids (ops, finance, health) are the
# framework's vocabulary. Only the things that belong to one person count.

set -uo pipefail
# NO TRAILING /.. — the first version had one, so it cd'd to the PARENT of the repo, `git ls-files`
# returned nothing, and the tool reported "PORTABLE — nothing personal left" having scanned zero
# files. That is precisely the failure this project's second principle is about: a check that could
# not look must never render as a clean verdict. Caught because the answer was obviously wrong;
# a subtler version would have been believed.
cd "$(git rev-parse --show-toplevel 2>/dev/null || dirname "$0")" || exit 1

MODE="${1:-advisory}"
WANT="${2:-}"

# category <TAB> regex <TAB> what to do about it
#
# TAB-SEPARATED, NOT PIPE. The first version used '|' as the delimiter — and the regexes contain '|'
# for alternation, so `IFS='|' read` split "\b(100|10)\." into a broken pattern that matched
# nothing. The two categories with alternation in them, private-ips and persona-names, both reported
# CLEAN while the values were sitting in tracked files. A delimiter that can occur in the data is not
# a delimiter.
CHECKS='slack-ids	\b[CUB]0[A-Z0-9]{8,10}\b	move to config (MOSES_OWNER_SLACK_ID, MOSES_CHAT_CHANNELS)
private-ips	\b(100|10)\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b	move to config (MOSES_MCP_URL and friends)
home-paths	/home/[a-z][a-z0-9_-]+/	use $HOME or a configured root
personal-domains	viatica	the operators own product name does not belong in the engine
personal-accounts	bradwallen	a github account name is not framework vocabulary
app-urls	[a-z0-9-]+\.up\.railway\.app	move to config
persona-names	\b(Birdeye|Tagilla|Big Pipe|Therapist)\b	read the display name from the roster, not a literal'

# Deliberate, explained exceptions. An allowlist you cannot read is one nobody audits.
#   docs/*      tell the incidents that produced the rules; the detail IS the evidence
#   config/*.example  shows an operator what their own values look like
#   tools/check-portability.sh  contains the patterns by definition
skip() {
  case "$1" in
    docs/*|config/*.example*|tools/check-portability.sh|README.md|.gitignore) return 0 ;;
  esac
  return 1
}

# ── Self-test ───────────────────────────────────────────────────────────────
# Every category gets a known positive. This tool reported "PORTABLE" twice while broken — once
# because it scanned the wrong directory, once because the field delimiter collided with the '|' in
# its own regexes. Both times the output was a clean bill of health. A checker with no self-test is
# a checker you are trusting on its word.
if [ "$MODE" = "--selftest" ]; then
    echo
    echo "  check-portability self-test"
    bad=0
    while IFS=$'\t' read -r cat pat _; do
        [ -n "$cat" ] || continue
        case "$cat" in
          slack-ids)         probe='OWNER_ID = "U0BKN5JT3PC"' ;;
          private-ips)       probe='http://100.87.21.18:8765/mcp' ;;
          home-paths)        probe='KNIGHT_HOME=/home/someone/knight' ;;
          personal-domains)  probe='https://viatica.travel' ;;
          personal-accounts) probe='git@github.com:bradwallen/Moses.git' ;;
          app-urls)          probe='https://my-thing-production.up.railway.app/' ;;
          persona-names)     probe='username:"Birdeye"' ;;
          *)                 probe='' ;;
        esac
        if [ -z "$probe" ]; then printf '    %-18s NO PROBE DEFINED\n' "$cat"; bad=1; continue; fi
        if printf '%s\n' "$probe" | grep -qE "$pat"; then
            printf '    %-18s catches its known positive\n' "$cat"
        else
            printf '    %-18s BROKEN — does not match %s\n' "$cat" "$probe"; bad=1
        fi
    done <<< "$CHECKS"
    # THE CODE/PROSE CLASSIFIER, tested at the seam rather than in the head. Patterns being right
    # was never the problem: on 2026-09-03 every pattern passed this self-test while the classifier
    # counted Python docstrings as code, so the persona-name figure read 36 when about eight were
    # actionable — and it did not move when the names underneath were changed. What follows builds a
    # fixture with the same name in BOTH positions and asserts they are told apart.
    _t=$(mktemp -d); trap 'rm -rf "$_t"' EXIT
    cat > "$_t/fixture.py" <<'FIXTURE'
"""A module docstring that mentions Birdeye while explaining an old incident."""
# A comment that also mentions Birdeye.
OPS_NAME = "Birdeye"


def f():
    """Another docstring naming Birdeye."""
    return 1
FIXTURE
    _pat='\b(Birdeye|Tagilla|Big Pipe|Therapist)\b'
    _pl=$(python3 "$(dirname "$0")/prose-lines.py" "$_t/fixture.py" | tr '\n' '|' | sed 's/|$//')
    _hits=$(grep -nE "$_pat" "$_t/fixture.py" | grep -vE '^[0-9]+:[[:space:]]*#' | cut -d: -f1)
    _code=$(printf '%s\n' "$_hits" | grep -cvE "^(${_pl})$") || _code=0
    _all=$(grep -cE "$_pat" "$_t/fixture.py")
    if [ "$_all" = 4 ] && [ "$_code" = 1 ]; then
        printf '    %-18s counts the literal, exempts 2 docstrings and 1 comment\n' "classifier"
    else
        printf '    %-18s BROKEN — saw %s hits, called %s of them code (want 4 and 1)\n' \
               "classifier" "$_all" "$_code"; bad=1
    fi
    # And it must NOT go quiet when the literal is the only occurrence — the failure that would
    # make every future number look good.
    printf 'OPS_NAME = "Birdeye"\n' > "$_t/only.py"
    _pl2=$(python3 "$(dirname "$0")/prose-lines.py" "$_t/only.py" | tr '\n' '|' | sed 's/|$//')
    _c2=$(grep -nE "$_pat" "$_t/only.py" | cut -d: -f1 | grep -cvE "^(${_pl2:-__none__})$") || _c2=0
    if [ "$_c2" = 1 ]; then
        printf '    %-18s a bare literal is still code\n' "classifier"
    else
        printf '    %-18s BROKEN — a bare literal counted %s, want 1\n' "classifier" "$_c2"; bad=1
    fi

    # And a line that must trip nothing, so a pattern cannot become a wildcard.
    innocent='def reply(history, bot_user_id, channel, reactive=False):'
    while IFS=$'\t' read -r cat pat _; do
        [ -n "$cat" ] || continue
        printf '%s\n' "$innocent" | grep -qE "$pat" && { printf '    %-18s FIRES ON ORDINARY CODE\n' "$cat"; bad=1; }
    done <<< "$CHECKS"
    echo
    [ "$bad" = 0 ] && echo "  self-test OK — every category matches its probe and none match ordinary code" \
                   || echo "  SELF-TEST FAILED — do not trust the counts" >&2
    exit "$bad"
fi

# ASSERT COVERAGE BEFORE REPORTING ANYTHING. This tool exists to enforce a rule; it must be able to
# prove it actually examined something first. "I scanned nothing" and "I found nothing" are the same
# output unless the tool says which — and this one shipped its first run saying the wrong one.
SCANNED=$(git ls-files 2>/dev/null | wc -l)
if [ "$SCANNED" -lt 5 ]; then
    printf '\n  REFUSING TO REPORT: only %d file(s) visible from %s\n' "$SCANNED" "$(pwd)" >&2
    printf '  That is this tool being blind, not the repo being clean.\n\n' >&2
    exit 2
fi

TOTAL=0; PROSE=0
printf '\n  Portability — what still belongs to one person\n'
printf '  scanned %d tracked files as %s\n' "$SCANNED" "$(id -un)"
printf '  %s\n\n' "$(printf '─%.0s' $(seq 1 62))"

while IFS=$'\t' read -r cat pat advice; do
  [ -n "$cat" ] || continue
  files=""; n=0; prose=0
  while IFS= read -r f; do
    skip "$f" && continue
    [ -f "$f" ] || continue
    # CODE vs PROSE, counted separately and only the first one gates.
    #
    # An early version counted every occurrence and reported 84 persona names. Most were comments and
    # docstrings recording the incidents that produced the design — "Birdeye posted his ops report;
    # Moses counted it as his own reply" — and test fixtures asserting the personas are told apart.
    # Renaming those would make the repository harder to understand while making the number smaller.
    # A metric that rewards deleting your documentation is the wrong metric.
    #
    # So: a hit on a comment line, or anywhere under tests/, is prose. Everything else is code, and
    # code is the only thing --strict will ever fail on.
    # Python DOCSTRINGS are prose too, and missing that was a real miscount (2026-09-03): the
    # persona-name total read 36 "in code" when about eight were actionable literals, and it did
    # not move when the names underneath actually changed. `tools/prose-lines.py` marks docstring
    # line numbers via the AST; a string in VALUE position stays code, which is the whole point.
    case "$f" in
      */tests/*|*_test.py) c=0; pc=$(grep -cE "$pat" "$f" 2>/dev/null) || pc=0 ;;
      *.py) tot=$(grep -cE "$pat" "$f" 2>/dev/null) || tot=0
            pl=$(python3 "$(dirname "$0")/prose-lines.py" "$f" 2>/dev/null | tr '\n' '|' | sed 's/|$//')
            # `#` comments are prose in Python too — the old branch dropped them and this one
            # must as well, or the fix trades one miscount for another (it did, briefly: the
            # personal-domains total ROSE from 23 to 25 on the first attempt).
            hits=$(grep -nE "$pat" "$f" 2>/dev/null | grep -vE '^[0-9]+:[[:space:]]*#' | cut -d: -f1)
            if [ -n "$pl" ]; then
              c=$(printf '%s\n' "$hits" | grep -c . >/dev/null 2>&1 && \
                  printf '%s\n' "$hits" | grep -cvE "^(${pl})$") || c=0
            else
              c=$(printf '%s\n' "$hits" | grep -c '[0-9]') || c=0
            fi
            pc=$((tot - c)) ;;
      *) c=$(grep -vE '^[[:space:]]*(#|//|\*|>)' "$f" 2>/dev/null | grep -cE "$pat") || c=0
         tot=$(grep -cE "$pat" "$f" 2>/dev/null) || tot=0
         pc=$((tot - c)) ;;
    esac
    prose=$((prose + pc))
    [ "$c" -gt 0 ] || continue
    n=$((n + c)); files="$files $f"
  done < <(git ls-files 2>/dev/null)

  TOTAL=$((TOTAL + n))
  PROSE=$((PROSE + prose))
  if [ "$n" -eq 0 ]; then
    printf '  %-18s %s' "$cat" "clean ✓"
    [ "$prose" -gt 0 ] && printf '   (%d in comments/tests — left alone on purpose)' "$prose"
    printf '\n'
  else
    printf '  %-18s %3d in code' "$cat" "$n"
    [ "$prose" -gt 0 ] && printf ', %d in comments/tests' "$prose"
    printf '   (%d file(s))\n' "$(printf '%s' "$files" | wc -w)"
    printf '  %-18s → %s\n' "" "$advice"
    if [ "$MODE" = "--show" ] && [ "$WANT" = "$cat" ]; then
      for f in $files; do
        printf '      %s\n' "$f"
        grep -nE "$pat" "$f" | head -4 | cut -c1-110 | sed 's/^/          /'
      done
    fi
  fi
done <<< "$CHECKS"

printf '\n  %s\n' "$(printf '─%.0s' $(seq 1 62))"
if [ "$TOTAL" -eq 0 ]; then
  printf '  PORTABLE — nothing personal left in the engine.\n'
  printf '  Time to turn on --strict in the pre-push hook.\n\n'
  exit 0
fi
printf '  %d in CODE still tie this to one person (%d more in comments/tests, which stay).\n' "$TOTAL" "$PROSE"
printf '  See one category: tools/check-portability.sh --show <category>\n\n'
[ "$MODE" = "--strict" ] && exit 1
exit 0
