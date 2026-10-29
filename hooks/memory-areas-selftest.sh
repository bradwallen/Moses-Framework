#!/usr/bin/env bash
# memory-areas-selftest.sh — prove memory-areas.py files every note somewhere, flags orphans, and fails open.
#
#   memory-areas-selftest.sh                    run against a throwaway corpus; exit 1 on any failure
#   GEN=/path/to/copy memory-areas-selftest.sh  run the same fixtures against a deliberately broken copy
#
# Uses a corpus it builds itself, never the operator's, so it runs the same on any install.

set -uo pipefail
GEN="${GEN:-$(dirname "$(readlink -f "$0")")/memory-areas.py}"
T=$(mktemp -d)
pass=0; fail=0
ok()  { echo "  PASS  $1"; pass=$((pass+1)); }
bad() { echo "  FAIL  $1"; fail=$((fail+1)); }
note() {  # note <file-stem> <name> <description> [extra frontmatter line]
  printf -- '---\nname: %s\ndescription: %s\n%s\nmetadata:\n  type: reference\n---\n\nbody\n' "$2" "$3" "${4:-}" > "$T/$1.md"
}

printf '# Memory index\n- [Listed](project_app_listed.md) - in the boot index\n' > "$T/MEMORY.md"
note project_app_listed project_app_listed "a project note the index mentions"
note job-ship-it job-ship-it "JOB — ship it"
note feedback-locked-one feedback-locked-one "a settled rule" 'locked: "the operator, 2026-10-29: settled"'
note odd-name odd-name "matches no area rule"
note moved-by-hand moved-by-hand "has an explicit area" 'area: jobs'
printf 'no frontmatter at all\n' > "$T/scratch.md"

python3 "$GEN" --memory "$T"; rc=$?
[ "$rc" -eq 0 ] && ok "generates without error" || bad "generator exited $rc"
grep -q '\[\[project_app_listed\]\] — ' "$T/areas/projects.md" 2>/dev/null \
  && ok "a listed note carries no orphan mark" || bad "listed note missing or wrongly marked"
grep -q '\[\[job-ship-it\]\] `JOB · not in MEMORY.md`' "$T/areas/jobs.md" 2>/dev/null \
  && ok "a job note is filed under Jobs and its orphan status shows" || bad "job note not filed or not marked"
grep -q '\[\[feedback-locked-one\]\] `LOCKED' "$T/areas/working-rules.md" 2>/dev/null \
  && ok "a locked note is marked LOCKED" || bad "locked note not marked"
grep -q '\[\[odd-name\]\]' "$T/areas/reference.md" 2>/dev/null \
  && ok "a note no rule matches still lands somewhere" || bad "unmatched note was dropped"
grep -q '\[\[moved-by-hand\]\]' "$T/areas/jobs.md" 2>/dev/null \
  && ok "an area: line overrides the name rules" || bad "area: override ignored"
! grep -rq 'scratch' "$T/areas" 2>/dev/null \
  && ok "a file without frontmatter is not listed" || bad "scratch file was listed"
python3 "$GEN" --memory "$T" --check >/dev/null; rc=$?
[ "$rc" -eq 0 ] && ok "--check is clean right after a run" || bad "--check says stale after a run ($rc)"
note brand-new brand-new "added after the last run"
python3 "$GEN" --memory "$T" --check >/dev/null; rc=$?
[ "$rc" -eq 1 ] && ok "--check notices a new note" || bad "--check missed a new note ($rc)"
# An operator's own areas.conf replaces the defaults, and a catch-all is still added after it.
printf '# comment\nwidgets\tWidgets\t^project_app\n' > "$T/areas.conf"
python3 "$GEN" --memory "$T"
grep -q '\[\[project_app_listed\]\]' "$T/areas/widgets.md" 2>/dev/null \
  && ok "areas.conf replaces the default areas" || bad "areas.conf ignored"
grep -q '\[\[odd-name\]\]' "$T/areas/reference.md" 2>/dev/null \
  && ok "a catch-all is added after the operator's areas" || bad "no catch-all after areas.conf"
python3 "$GEN" --memory /nonexistent; rc=$?
[ "$rc" -eq 0 ] && ok "a missing corpus fails open" || bad "missing corpus exited $rc"

echo; echo "  $pass passed, $fail failed"
[ "$fail" -eq 0 ]
