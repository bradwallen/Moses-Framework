# examples.sh — Brad's confirmed examples are TESTS, checked by the runner (#3, 2026-10-18).
#
# Sourced by `knight` (which writes them into the job and the builder's brief) and by `knight-run`
# (which holds a job whose examples have no test). Brad, 2026-10-17: most review holds were judgment
# calls on wording, and "civil and water" was built as something else entirely. An example he agreed
# to — "GIS Analyst Intern: yes; Civil Engineering Intern that mentions ArcGIS: no" — is pass or fail,
# so it becomes a test the project's own gate runs, and the runner checks that each one HAS a test.
#
# What the runner can check, said plainly: that every example's text appears in a test file at the tip
# of the branch, and that the gate — which runs those tests — passed. "Appears" ignores case, quotes,
# backslashes and spacing, so `"Brad's"` written as 'Brad\'s' or \"ArcGIS\" still counts (review,
# 2026-10-18: verbatim matching held correct work). It cannot read the test's mind: whether a test
# asserts an example the RIGHT WAY ROUND — kept, or left out — is the review's, and its brief says so.
#
# The examples come from the acceptance criteria agent/build_spec.py writes after Brad's yes:
#   - It INCLUDES: <text>        - It does NOT include: <text>
# A follow-up inherits them, because its acceptance carries the original's.

# examples_extract <acceptance-file> <examples-file> — one "INCLUDES<TAB>text" / "EXCLUDES<TAB>text"
# line per example; no file at all when there are none.
examples_extract() {
    grep -E '^- It (INCLUDES|does NOT include): ' "$1" 2>/dev/null \
        | sed -E 's/^- It INCLUDES: /INCLUDES\t/; s/^- It does NOT include: /EXCLUDES\t/' > "$2"
    [ -s "$2" ] || rm -f "$2"
}

# examples_brief <examples-file> — the builder's instructions, printed only when there are examples.
examples_brief() {
    [ -s "$1" ] || return 0
    printf '\nCONFIRMED EXAMPLES — EACH ONE IS A TEST. Brad agreed to these before the build. For each one,\n'
    printf 'add a test that gate.sh runs, which uses the example text VERBATIM (as the test name or a string\n'
    printf 'in it) and asserts it is included or excluded. The runner checks that every example text appears\n'
    printf 'in a test file; an example with no test holds the job.\n'
    awk -F'\t' '{print "- " ($1 == "INCLUDES" ? "It INCLUDES: " : "It does NOT include: ") $2}' "$1"
}

# A test file, by the conventions of the languages Knight builds in: a tests/ test/ spec/ __tests__/
# directory, or test_x.py, x_test.go, x.test.ts, x.spec.js and the like.
EXAMPLES_TEST_PATHS='(^|/)(tests?|spec|__tests__)/|(^|/)test[_-][^/]*$|[^/][._-](test|spec)\.[A-Za-z0-9]+$'

# Lowercased, with quotes, backticks and backslashes removed and runs of space made one: the form both
# sides are compared in.
examples_norm() { tr -d '"`\\'"'" | tr '[:upper:]' '[:lower:]' | tr -s '[:space:]' ' '; }

# examples_review_brief <examples-file> — what only the reviewer can judge, printed when there are examples.
examples_review_brief() {
    [ -s "$1" ] || return 0
    printf '\nCONFIRMED EXAMPLES. The runner has checked that each example below is NAMED in a test. Check\n'
    printf 'that each such test asserts it THE RIGHT WAY ROUND — an "It INCLUDES" example is kept, an "It does\n'
    printf 'NOT include" example is left out — and that it is a real assertion, not a comment or a fixture.\n'
    printf 'An example with no such assertion is MISSED.\n'
    awk -F'\t' '{print "- " ($1 == "INCLUDES" ? "It INCLUDES: " : "It does NOT include: ") $2}' "$1"
}

# examples_missing <repo> <examples-file> — prints the example texts no test file names, "; "-joined.
# Prints nothing when every example has a test, or when there are no examples. Test files are found by
# path, plus Rust files holding inline tests (#[cfg(test)]), which live beside the code.
examples_missing() {
    local repo=$1 file=$2 kind text out="" files=() hay
    [ -s "$file" ] || return 0
    mapfile -d '' files < <( { git -C "$repo" ls-files -z 2>/dev/null | grep -zE "$EXAMPLES_TEST_PATHS"
                               git -C "$repo" grep -lz -F '#[cfg(test)]' -- '*.rs' 2>/dev/null; } )
    hay=$( [ "${#files[@]}" -gt 0 ] && ( cd "$repo" && cat -- "${files[@]}" 2>/dev/null ) | examples_norm )
    while IFS=$'\t' read -r kind text; do
        [ -n "$text" ] || continue
        grep -qF -- "$(printf '%s' "$text" | examples_norm)" <<<"$hay" || out="$out${out:+; }$text"
    done < "$file"
    printf '%s' "$out"
}
