# review-verdict.sh — reading Zryachiy's verdict, in ONE place.
#
# Sourced by `knight-run` (before a push) and by `knight-land` (before a merge). It used to live
# inside knight-run alone, and the moment a second caller needed the same decision the choice was to
# copy it or to share it. Copied, the two would answer differently the first time either was fixed,
# and the one that mattered would be whichever nobody remembered — see the estate's standing rule
# that a module gets one copy.
#
# Sets review_ok / review_why / plausible.
# ── Reading the review's verdict ─────────────────────────────────────────────
# A FUNCTION so the test suite can drive the real decision with fixture files. The review itself
# needs a green gate, real commits and a successful rebase before it ever runs, which makes the
# end-to-end path unreachable from the fake-agent seam — and an untestable gate is one nobody has
# watched go red.
#
# Sets review_ok / review_why / plausible. Every non-clean outcome BLOCKS, including the ones that
# are not findings: a crashed reviewer and an unreadable answer are both "could not look", and
# "could not look" has never meant "fine" anywhere else in this estate.
review_verdict() {
    local rrc=$1 file=$2 body
    review_ok=1; review_why=""; plausible=""
    # A LINE THAT REPORTS NOTHING IS NOT A FINDING. 2026-09-11: Zryachiy passed Brad's bot-cap change
    # with "CONFIRMED: none — no defects found in the diff." and then CLEAN, and this blocked it for
    # starting with the word CONFIRMED. Nothing shipped; a correct build sat on a branch.
    #
    # Deliberately narrow, because the other mistake is worse: a real finding dropped for opening
    # with a word that also means nothing. A null line must BEGIN none / nothing / n/a / "no defects"
    # (etc.) and end or punctuate right there — "none of the callers…" is a finding — and must not
    # name a file:line anywhere. It is only DISCARDED, never counted as a pass: with nothing else on
    # the page the answer is still unreadable, and still blocks.
    body=$(awk '
        { l = tolower($0) }
        l ~ /^(confirmed|missed)[[:space:]]*:?[[:space:]]*((none|nothing|n\/a)[[:space:]]*(found)?[[:space:]]*([.,:;]|—|–|-|$)|no[[:space:]]+(defects?|findings?|issues?|problems?)([^[:alnum:]]|$))/ \
            && $0 !~ /[[:alnum:]_\/-]+\.[[:alnum:]]+:[0-9]+/ { next }
        { print }' "$file" 2>/dev/null)
    if [ "$rrc" -ne 0 ]; then
        review_ok=0
        review_why="the review agent exited $rrc — NOT a clean review, so nothing ships"
    elif ! grep -qE '^(CONFIRMED|MISSED|PLAUSIBLE|CLEAN)\b' <<<"$body"; then
        review_ok=0
        review_why="the review returned nothing this runner could read — treated as a block, not a pass"
    elif grep -qE '^CONFIRMED\b' <<<"$body"; then
        review_ok=0
        # The label once, then the findings — this read "review CONFIRMED: CONFIRMED: …" in Slack.
        review_why="review CONFIRMED a defect: $(grep -E '^CONFIRMED\b' <<<"$body" | head -3 \
            | sed -E 's/^CONFIRMED[[:space:]]*:?[[:space:]]*//' | tr '\n' ' ')"
    # MISSED BLOCKS EXACTLY LIKE CONFIRMED, and it is reported separately because it means something
    # different to whoever reads it. CONFIRMED says the code is wrong; MISSED says the code may be
    # perfect and is not what was asked for. Conflating them would hide the second behind the first,
    # and the second is the one no test in this pipeline could ever have caught.
    elif grep -qE '^MISSED\b' <<<"$body"; then
        review_ok=0
        review_why="review MISSED the request: $(grep -E '^MISSED\b' <<<"$body" | head -3 \
            | sed -E 's/^MISSED[[:space:]]*:?[[:space:]]*//' | tr '\n' ' ')"
    else
        plausible=$(grep -E '^PLAUSIBLE\b' <<<"$body" | head -5 || true)
    fi
}
