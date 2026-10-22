#!/usr/bin/env bash
# test-examples.sh — Brad's confirmed examples are tests, and the runner checks each has one (#3).
#
#   bash knight/test-examples.sh
#
# Drives the REAL lib/examples.sh against real git repositories, with acceptance criteria in the exact
# shape agent/build_spec.py writes after Brad's yes. Then checks bin/knight and bin/knight-run are wired
# to it, and removes each guard from a copy of the lib: the case it protects must go wrong.
set -uo pipefail
# NO TEST POSTS TO SLACK. Everything this suite runs — and anything those scripts hand off to, like
# knight-next after a landing — inherits this. test-land once did not, and its scratch target "t1"
# posted "Stopped" cards into Brad's channel on every run (2026-10-18). test-notify.sh checks every suite.
export KNIGHT_SLACK_CHANNEL="" KNIGHT_SLACK_ENV=/nonexistent
HERE=$(cd "$(dirname "$0")" && pwd)
LIB="$HERE/lib/examples.sh"
pass=0; fail=0
ok()  { printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL  %s\n     %s\n' "$1" "${2:-}"; fail=$((fail+1)); }
TMP=$(mktemp -d /tmp/knight-examples-test.XXXXXX)
trap 'rm -rf "$TMP"' EXIT

ACC="$TMP/acceptance.txt"
cat > "$ACC" <<'A'
Only roles whose title names GIS work are listed

CONFIRMED EXAMPLES (Brad agreed to these before the build):
- It INCLUDES: GIS Analyst Intern
- It INCLUDES: Geospatial Analyst at NGA
- It does NOT include: Civil Engineering Intern that mentions ArcGIS
A

# repo <name> [<path> <content>]… — a git repo holding those files, committed.
repo() {
    local r="$TMP/$1"; shift
    mkdir -p "$r"; git -C "$r" init -q -b main; git -C "$r" config user.email t@t.test; git -C "$r" config user.name t
    while [ $# -ge 2 ]; do mkdir -p "$r/$(dirname "$1")"; printf '%s\n' "$2" > "$r/$1"; shift 2; done
    git -C "$r" add -A; git -C "$r" commit -qm x
    printf '%s' "$r"
}

cases() {  # cases <lib> — one "name=outcome" line each, for the watched-fail runs
    ( . "$1"
      ex="$TMP/ex-$RANDOM"; examples_extract "$ACC" "$ex"
      [ "$(grep -c '^EXCLUDES' "$ex" 2>/dev/null)" = 1 ] && echo extract=both || echo extract=lost
      r=$(repo "c1-$RANDOM" src/app.ts 'x' README.md 'GIS Analyst Intern; Geospatial Analyst at NGA; Civil Engineering Intern that mentions ArcGIS')
      [ -n "$(examples_missing "$r" "$ex")" ] && echo readme=held || echo readme=passed
      r=$(repo "c2-$RANDOM" test/filter.test.ts "it('keeps GIS Analyst Intern'); it('keeps Geospatial Analyst at NGA')")
      [ -n "$(examples_missing "$r" "$ex")" ] && echo halfway=held || echo halfway=passed
      r=$(repo "c3-$RANDOM" tests/test_filter.py 'assert keep("GIS Analyst Intern") and keep("Geospatial Analyst at NGA")
assert not keep("Civil Engineering Intern that mentions \"ArcGIS\"".replace("\"", ""))')
      [ -n "$(examples_missing "$r" "$ex")" ] && echo escaped=held || echo escaped=passed )
}

echo "confirmed examples"
. "$LIB"
EX="$TMP/examples.txt"
examples_extract "$ACC" "$EX"
[ "$(wc -l < "$EX")" = 3 ] && grep -qP '^INCLUDES\tGIS Analyst Intern$' "$EX" \
    && grep -qP '^EXCLUDES\tCivil Engineering Intern that mentions ArcGIS$' "$EX" \
    && ok "the examples are read from the criteria build_spec writes, both kinds" || bad "the examples are read from the criteria build_spec writes, both kinds" "$(cat "$EX")"
printf 'The page works.\n' > "$TMP/plain.txt"; examples_extract "$TMP/plain.txt" "$TMP/none.txt"
[ ! -e "$TMP/none.txt" ] && [ -z "$(examples_brief "$TMP/none.txt")" ] && [ -z "$(examples_missing "$TMP" "$TMP/none.txt")" ] \
    && ok "a job with no examples gets no file, no brief text and no check" || bad "a job with no examples gets no file, no brief text and no check"
b=$(examples_brief "$EX")
grep -q 'EACH ONE IS A TEST' <<<"$b" && grep -q 'It does NOT include: Civil Engineering Intern that mentions ArcGIS' <<<"$b" \
    && ok "the builder's brief lists each example and says it must be a test" || bad "the builder's brief lists each example and says it must be a test" "$b"

r=$(repo none src/app.ts 'export const x = 1')
m=$(examples_missing "$r" "$EX")
grep -q 'GIS Analyst Intern' <<<"$m" && grep -q 'Civil Engineering' <<<"$m" \
    && ok "a branch with no tests is missing every example" || bad "a branch with no tests is missing every example" "$m"
r=$(repo readme src/app.ts 'x' README.md 'GIS Analyst Intern, Geospatial Analyst at NGA, Civil Engineering Intern that mentions ArcGIS')
[ -n "$(examples_missing "$r" "$EX")" ] && ok "naming an example in a README or the code is not a test" \
    || bad "naming an example in a README or the code is not a test"
r=$(repo half test/filter.test.ts "it('keeps GIS Analyst Intern'); it('keeps Geospatial Analyst at NGA')")
m=$(examples_missing "$r" "$EX")
[ "$m" = "Civil Engineering Intern that mentions ArcGIS" ] && ok "a test for what it includes but not what it excludes is still missing the exclusion" \
    || bad "a test for what it includes but not what it excludes is still missing the exclusion" "$m"
r=$(repo all src/filter.test.ts "test('GIS Analyst Intern is kept')" tests/test_nga.py "assert keep('geospatial analyst at NGA')" \
      src/drop_test.go '"Civil Engineering Intern that mentions ArcGIS" is dropped')
m=$(examples_missing "$r" "$EX")
[ -z "$m" ] && ok "every example named in a test file passes — across .test.ts, tests/, _test.go, any case" \
    || bad "every example named in a test file passes — across .test.ts, tests/, _test.go, any case" "$m"

# The ways real tests write text (review, 2026-10-18): each of these held correct work when it was verbatim.
APOS="$TMP/apos.txt"; printf "INCLUDES\tBrad's own trip\n" > "$APOS"
r=$(repo js src/trip.test.js "it('keeps Brad\\'s own trip', () => {})")
[ -z "$(examples_missing "$r" "$APOS")" ] && ok "an apostrophe escaped in a JS string still counts" || bad "an apostrophe escaped in a JS string still counts" "$(cat "$r/src/trip.test.js")"
r=$(repo py tests/test_filter.py 'assert keep("GIS Analyst Intern") and keep("Geospatial  Analyst at nga")
assert not keep("Civil Engineering Intern that mentions \"ArcGIS\"")')
[ -z "$(examples_missing "$r" "$EX")" ] && ok "escaped quotes, doubled spaces and case still count" || bad "escaped quotes, doubled spaces and case still count" "$(examples_missing "$r" "$EX")"
r=$(repo sh knight/test-filter.sh 'check "GIS Analyst Intern" "Geospatial Analyst at NGA" "Civil Engineering Intern that mentions ArcGIS"')
[ -z "$(examples_missing "$r" "$EX")" ] && ok "a test-*.sh suite (Moses's own style) is a test file" || bad "a test-*.sh suite (Moses's own style) is a test file"
r=$(repo rs src/filter.rs '#[cfg(test)] mod t { #[test] fn x() { keep("GIS Analyst Intern"); keep("Geospatial Analyst at NGA"); drop("Civil Engineering Intern that mentions ArcGIS"); } }')
[ -z "$(examples_missing "$r" "$EX")" ] && ok "Rust's inline tests, beside the code, count" || bad "Rust's inline tests, beside the code, count"
rb=$(examples_review_brief "$EX")
grep -q 'THE RIGHT WAY ROUND' <<<"$rb" && grep -q 'It does NOT include: Civil' <<<"$rb" \
    && ok "the reviewer is told to check each test asserts its example the right way round" || bad "the reviewer is told to check each test asserts its example the right way round" "$rb"

# Wiring: the two scripts call the lib where they must.
grep -q 'source "$(dirname "$(readlink -f "$0")")/../lib/examples.sh"' "$HERE/bin/knight" \
    && grep -q 'examples_extract "$dir/acceptance.txt" "$dir/examples.txt"' "$HERE/bin/knight" \
    && grep -q 'examples_brief "$dir/examples.txt"' "$HERE/bin/knight" \
    && grep -q 'examples_review_brief "$dir/examples.txt"' "$HERE/bin/knight" \
    && ok "knight writes each job's examples and puts them in both briefs" || bad "knight writes each job's examples and puts them in both briefs"
grep -q 'source "$(dirname "$0")/../lib/examples.sh"' "$HERE/bin/knight-run" \
    && grep -q 'ex_missing=$(examples_missing "$REPO" "$DIR/examples.txt")' "$HERE/bin/knight-run" \
    && awk '/ex_missing=\$\(examples_missing/{f=1} f&&/gate_ok=0/{print "y"; exit}' "$HERE/bin/knight-run" | grep -q y \
    && awk '!g && index($0, "for step in \"${KT_GATE[@]}\"; do"){g=NR} index($0, "ex_missing=$(examples_missing"){e=NR} index($0, "# ── Push, only on green")==1{p=NR} END{exit !(g && e > g && p > e)}' "$HERE/bin/knight-run" \
    && ok "knight-run holds the job (gate red) when an example has no test — after the gate runs, before any push" \
    || bad "knight-run holds the job (gate red) when an example has no test — after the gate runs, before any push"

# Watch each guard fail: a copy of the lib with it removed must get a case wrong.
MUT="$TMP/mut.sh"
watch_fail() {  # watch_fail <label> <case=outcome it must produce> <sed expression>
    cp "$LIB" "$MUT"; sed -i "$3" "$MUT"
    cmp -s "$LIB" "$MUT" && { bad "$1" "the sabotage no longer applies"; return; }
    grep -qx "$2" <<<"$(cases "$MUT")" && ok "watched fail: $1" || bad "watched fail: $1" "$(cases "$MUT" | tr '\n' ' ')"
}
grep -qx 'extract=both' <<<"$(cases "$LIB")" && grep -qx 'readme=held' <<<"$(cases "$LIB")" && grep -qx 'halfway=held' <<<"$(cases "$LIB")" \
    && grep -qx 'escaped=passed' <<<"$(cases "$LIB")" \
    && ok "the watched-fail cases pass on the real lib" || bad "the watched-fail cases pass on the real lib" "$(cases "$LIB" | tr '\n' ' ')"
watch_fail "with any file counted as a test, a README passes" readme=passed "s/^EXAMPLES_TEST_PATHS=.*/EXAMPLES_TEST_PATHS='.'/"
watch_fail "reading only the INCLUDES, the exclusions are lost" extract=lost 's/(INCLUDES|does NOT include)/(INCLUDES)/'
watch_fail "compared verbatim, an escaped quote holds correct work" escaped=held 's/^examples_norm() {.*/examples_norm() { tr "[:upper:]" "[:lower:]"; }/'
watch_fail "with one example enough, half a test suite passes" halfway=passed 's/out="$out${out:+; }$text"/: /'

echo
echo "$pass ok, $fail failed"
[ "$fail" -eq 0 ]
