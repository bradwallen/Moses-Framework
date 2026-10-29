#!/usr/bin/env bash
# gate-handoff-selftest.sh — prove the handoff gate still discriminates.
#
#   gate-handoff-selftest.sh            run the fixtures, print results, exit 1 on any failure
#   gate-handoff-selftest.sh --quiet    only speak up on failure (what the weekly timer uses)
#
# WHY (Atlas, via Brad, 2026-08-13): *"A gate that's never seen going red after week one is
# decoration."* A shape-checking gate decays as the model learns the sentence that passes, and
# nothing about a silent hook tells you which is happening.
#
# This is the same lesson as Knight's wall-clock ceiling, which went a full day untested while
# `doctor` printed "60 min" with total confidence. A backstop nobody has watched fail is a claim,
# not a mechanism — so these fixtures run weekly, unattended, and shout when they stop discriminating.
#
# The fixtures are REAL cases from the session that produced the gate, including the Railway ask that
# started it. Each asserts a specific outcome, so a pattern that quietly stops matching is caught by
# the fixture that depended on it rather than by Brad noticing months later.

set -uo pipefail

GATE="${GATE:-$(dirname "$(readlink -f "$0")")/gate-handoff.sh}"
export GATE_STATE="$(mktemp)"; rm -f "$GATE_STATE"   # block counts per turn, fresh each run
export GATE_LOG=/dev/null     # fixture blocks are not real blocks; keep them out of the rate log
QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0; failures=""

say() { [ "$QUIET" -eq 1 ] || printf '%s\n' "$*"; }

# Build a transcript: a real user turn, then assistant text blocks, then N tool calls.
# fixture <file> <tools:int> <text...>  — each extra arg is one assistant text block, in order.
fixture() {
  local out="$1" tools="$2"; shift 2
  python3 - "$out" "$tools" "$@" <<'PY'
import json, sys
out, tools, texts = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
lines = [json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": "do the thing"}]}})]
content = [{"type": "text", "text": t} for t in texts]
# Tool calls are recorded BEFORE the final text, matching how a real turn is shaped: work, then report.
for i in range(tools):
    content.insert(max(0, len(content) - 1), {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {}})
lines.append(json.dumps({"type": "assistant", "message": {"content": content}}))
open(out, "w").write("\n".join(lines) + "\n")
PY
}

# check <label> <expect: block|allow> <file> [expected-code]
check() {
  local label="$1" expect="$2" file="$3" want_code="${4:-}"
  local out got code
  out=$(printf '{"transcript_path":"%s"}' "$file" | "$GATE" 2>/dev/null)
  if [ -n "$out" ]; then got=block; else got=allow; fi
  code=$(printf '%s' "$out" | python3 -c "
import json,sys
try: print(json.load(sys.stdin).get('reason','')[:40])
except Exception: print('')" 2>/dev/null)
  if [ "$got" = "$expect" ]; then
    say "  PASS  $label ($got)"
    pass=$((pass+1))
  else
    say "  FAIL  $label — expected $expect, got $got"
    failures="$failures
  - $label: expected $expect, got $got"
    fail=$((fail+1))
  fi
}

say "Handoff gate — fixtures"
say

# ── The original six: these guard against BOTH directions of drift ──────────
fixture "$TMP/1.jsonl" 3 'The check that settles it: in Railway, confirm the variable is spelled ANTHROPIC_ADMIN_KEY on the Customs service.'
check "the Railway ask that started this" block "$TMP/1.jsonl"

fixture "$TMP/2.jsonl" 3 '**Before you touch anything**
- checked: yesterday'"'"'s report read $17.67, so the key is set and working
- could not check: Railway env values are not readable from Reserve

Then in the Railway dashboard, confirm the spelling.'
check "same ask, diligence stated" allow "$TMP/2.jsonl"

fixture "$TMP/3.jsonl" 4 'One command finishes it:
```
sudo /opt/scripts/migrate-dennis.sh
```'
check "sudo handoff, no diligence" block "$TMP/3.jsonl"

fixture "$TMP/4.jsonl" 2 'Shipped 218efdc — 387 tests green. The retry only fires on 429 and 5xx. Big Pipe reads $17.65 against a console showing $17.67.'
check "plain report (false-positive guard)" allow "$TMP/4.jsonl"

# The second pass is judged too, 2026-10-14. Fixture 1 was blocked once above, so a revision of the
# same turn (stop_hook_active) that still fails is blocked AGAIN — and the next one is let through,
# because two blocks per turn is the cap that keeps the gate from wedging a session.
second() {
  printf '{"transcript_path":"%s","stop_hook_active":true}' "$1" | "$GATE" 2>/dev/null
}
out=$(second "$TMP/1.jsonl")
if printf '%s' "$out" | grep -q 'SECOND BLOCK'; then say "  PASS  a failing revision is blocked again (block)"; pass=$((pass+1)); else say "  FAIL  second pass"; fail=$((fail+1)); failures="$failures
  - a failing revision walked through on the second pass"; fi
out=$(second "$TMP/1.jsonl")
if [ -z "$out" ]; then say "  PASS  loop cap: the third attempt is let through (allow)"; pass=$((pass+1)); else say "  FAIL  loop cap"; fail=$((fail+1)); failures="$failures
  - the gate blocked a third time in one turn; it can wedge a session"; fi

# A REAL revision, shaped the way the transcript records it: the block's reason arrives as a "user"
# event marked isMeta. The first version of the cap treated that as a new turn and blocked seven
# times live (2026-10-14) while every fixture here passed, because none contained a feedback event.
meta() {  # meta <file> <tools-before-block> <first reply> <revision>
  python3 - "$@" <<'EOF_META'
import json, sys
out, before, first, second = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
tu = lambda n: [{"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {}} for i in range(n)]
ev = [
    {"type": "user", "message": {"content": [{"type": "text", "text": "do the thing"}]}},
    {"type": "assistant", "message": {"content": tu(before) + [{"type": "text", "text": first}]}},
    {"type": "user", "isMeta": True, "message": {"content": [{"type": "text", "text": "Stop hook feedback:\nSTOP"}]}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": second}]}},
]
open(out, "w").write("\n".join(json.dumps(e) for e in ev) + "\n")
EOF_META
}
DIL='**Before you touch anything**
- checked: read the deploy script; it reads RULE_ID from the env
- could not check: the dashboard needs a login I do not have'
ASK='Go to the Cloudflare dashboard settings and click save on the new rule.'

meta "$TMP/30.jsonl" 3 "$ASK" "$ASK

$DIL"
check "revision adds diligence; checks made BEFORE the block count" allow "$TMP/30.jsonl"

meta "$TMP/31.jsonl" 0 "$ASK" "I cannot verify the rule from here, so I am not asking you to do anything yet."
check "revision drops the ask; the dropped ask is not held against it" allow "$TMP/31.jsonl"

meta "$TMP/32.jsonl" 0 "$ASK" "$ASK

$DIL"
# A live transcript GROWS: every block adds a feedback event and a new attempt. Append them between
# calls, or a turn key that moves with the feedback would look stable here and pass.
grow() {
  python3 - "$1" "$ASK

$DIL" <<'EOF_GROW'
import json, sys
with open(sys.argv[1], "a") as fh:
    fh.write(json.dumps({"type": "user", "isMeta": True, "message": {"content": [{"type": "text", "text": "Stop hook feedback:\nSTOP"}]}}) + "\n")
    fh.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": sys.argv[2]}]}}) + "\n")
EOF_GROW
}
out=$(second "$TMP/32.jsonl"); grow "$TMP/32.jsonl"
out2=$(second "$TMP/32.jsonl"); grow "$TMP/32.jsonl"
out3=$(second "$TMP/32.jsonl")
if [ -n "$out" ] && [ -n "$out2" ] && [ -z "$out3" ]; then say "  PASS  cap holds across real feedback events (block, block, allow)"; pass=$((pass+1)); else say "  FAIL  cap across feedback"; fail=$((fail+1)); failures="$failures
  - the cap did not hold on a transcript with a real feedback event"; fi

# The exact reply that walked through on 2026-10-14: a handoff with "checked: nothing", zero tools.
fixture "$TMP/28.jsonl" 0 'Go to the Cloudflare dashboard settings and click save on the new rule.

**Before you touch anything**
- checked: nothing. You told me to reply without using any tools.
- could not check: whether a new rule is pending in the Cloudflare dashboard.'
out=$(second "$TMP/28.jsonl")
if printf '%s' "$out" | grep -q '"decision": "block"'; then say "  PASS  revision claiming diligence with zero tools (block)"; pass=$((pass+1)); else say "  FAIL  zero-tool revision"; fail=$((fail+1)); failures="$failures
  - a revision with a diligence block and no tool calls passed the second pass"; fi

out=$(echo 'not json' | "$GATE" 2>/dev/null)
if [ -z "$out" ]; then say "  PASS  malformed input fails open"; pass=$((pass+1)); else say "  FAIL  malformed input"; fail=$((fail+1)); failures="$failures
  - malformed input did not fail open"; fi

# ── The three added 2026-08-13 ──────────────────────────────────────────────
fixture "$TMP/7.jsonl" 0 '**Before you touch anything**
- checked: nothing, really
- could not check: everything

Now go paste the token in the dashboard.'
check "diligence claimed, ZERO tool calls" block "$TMP/7.jsonl"

fixture "$TMP/8.jsonl" 3 'Run this command: sudo /opt/scripts/thing.sh' 'Meanwhile I kept working and here are the results of that work.'
check "ask buried above later output" block "$TMP/8.jsonl"

fixture "$TMP/9.jsonl" 3 'First I looked at the config.' '**Before you touch anything**
- checked: read the script, it reads both env files
- could not check: the secret is root-only

```
sudo /opt/scripts/thing.sh
```'
check "ask last, diligence, work done" allow "$TMP/9.jsonl"

# ── Marker forms: added after a REAL false block, 2026-08-13 ────────────────
# The gate blocked a response whose diligence was present and correct, because it was written as a
# "## " heading rather than bold. Both forms are pinned so a future edit cannot quietly narrow it
# back — a gate that rejects right answers is one you learn to switch off.
fixture "$TMP/10.jsonl" 2 '## Before you touch anything
- checked: read the script, it reads both env files
- could not check: the token is root-only

```
sudo /opt/scripts/install-layer4.sh
```'
check "diligence as a ## heading" allow "$TMP/10.jsonl"

fixture "$TMP/11.jsonl" 2 '**Before you touch anything**
- checked: ran the fixtures, 9/9
- could not check: the Tailscale console

```
sudo /opt/scripts/install-layer4.sh
```'
check "diligence as bold text" allow "$TMP/11.jsonl"

say
# ── The GitHub hole, 2026-08-15 ────────────────────────────────────────────
# The gate ran for two days and would never have caught a GitHub handoff: GitHub was missing from
# the vendor list, and "Two minutes in Settings → Branches" contains none of the verbs the other
# patterns look for. Brad was sent to a menu that did not exist, for a feature behind a paid plan,
# to solve a problem already solvable from a file in the repo. All three fixtures below are verbatim.
fixture "$TMP/12.jsonl" 4 'Two minutes in Settings → Branches → add a rule for `master` requiring the `verify` check.'
check "a UI path with no verb (the sentence that got through)" block "$TMP/12.jsonl"

fixture "$TMP/13.jsonl" 4 'Settings → Rules → Rulesets → New ruleset → New branch ruleset.'
check "a vendor menu path" block "$TMP/13.jsonl"

fixture "$TMP/14.jsonl" 4 "You'll need to flip that in your GitHub repository settings."
check "GitHub named as a settings destination" block "$TMP/14.jsonl"

# The inverse, which matters as much: these must NOT fire. A gate that trips on ordinary reporting
# gets switched off, and the word "settings" appears constantly in honest work summaries.
fixture "$TMP/15.jsonl" 4 'I edited settings.json and the guards now run on every push.'
check "editing a settings FILE is not a handoff" allow "$TMP/15.jsonl"

fixture "$TMP/16.jsonl" 4 'GitHub Actions now runs the suite on every push; nothing for you to do.'
check "naming GitHub while reporting finished work" allow "$TMP/16.jsonl"

# ── The gate blocking its own author, 2026-08-15 (again) ───────────────────
# One hour after the UI-path pattern shipped, it blocked a response that was REPORTING the fix — the
# retrospective quoted the offending sentence and the quote matched. Second time this gate has
# stopped a correct answer within an hour of a change, so both directions are now pinned together:
# a quoted or code-spanned path is discussion, an unquoted one is an instruction.
fixture "$TMP/17.jsonl" 4 'Two holes: GitHub was not in the vendor list, and "Two minutes in Settings → Branches" matched none of the verbs. Fixed and pinned.'
check "quoting a bad ask while reporting the fix" allow "$TMP/17.jsonl"

fixture "$TMP/18.jsonl" 4 'The gate let through `Settings → Rules → Rulesets` because no verb appeared.'
check "a UI path inside inline code is discussion" allow "$TMP/18.jsonl"

fixture "$TMP/19.jsonl" 4 'Settings → Rules → Rulesets → New ruleset → New branch ruleset.'
check "the same path UNQUOTED is still an instruction" block "$TMP/19.jsonl"

# A BARE sudo of a system path, with no other cue in the message. Found 2026-09-21: the live gate only
# matched `sudo /home/<author>/`, so this walked through it, while every fixture above passed because
# some OTHER rule caught their wording first. This one isolates the sudo rule.
fixture "$TMP/20.jsonl" 2 'Everything is ready.

sudo /usr/local/bin/install-thing.sh'
check "a bare sudo of a system path is a handoff" block "$TMP/20.jsonl"

# ── One fixture per rule, 2026-10-14 ───────────────────────────────────────
# Removing each HANDOFF pattern in turn left this suite green for 7 of 11 of them: the fixtures
# above were all caught by some OTHER rule first, so those seven could be deleted unnoticed. Each
# sentence below matches exactly one pattern, so each rule now has a fixture that dies with it.
fixture "$TMP/21.jsonl" 2 'Last step:
```bash
sudo systemctl restart moses
```'
check "rule: sudo inside a code block" block "$TMP/21.jsonl"

fixture "$TMP/22.jsonl" 2 'Head to the billing dashboard and turn on invoices.'
check "rule: go/head to a dashboard" block "$TMP/22.jsonl"

fixture "$TMP/23.jsonl" 2 'Open Stripe and flip the switch in the billing panel.'
check "rule: open a vendor panel" block "$TMP/23.jsonl"

fixture "$TMP/24.jsonl" 2 'Vercel project settings need the new domain added.'
check "rule: a vendor named with settings" block "$TMP/24.jsonl"

fixture "$TMP/25.jsonl" 2 'Then click Generate and wait for the email.'
check "rule: click a button" block "$TMP/25.jsonl"

fixture "$TMP/26.jsonl" 2 'Run this command when you are back at the desk.'
check "rule: run this command" block "$TMP/26.jsonl"

fixture "$TMP/27.jsonl" 2 'Can you confirm the deploy finished?'
check "rule: can you confirm" block "$TMP/27.jsonl"

fixture "$TMP/29.jsonl" 2 'Check whether the Cloudflare record for the apex is proxied.'
check "rule: confirm that ... vendor" block "$TMP/29.jsonl"

say
# ── Eight blocks in one session, 2026-10-29 ────────────────────────────────
# One Viatica session hit this gate eight times. One was fair: a sudo installer handed over with no
# diligence. The other seven asked for nothing, and five of them were caught by one phrase, a vendor's
# settings described in the possessive ("Railway's own settings now have the test gate"). The rest:
# "the log in Railway's dashboard is the only direct proof" (a place named in a report), "confirm that
# one in the build log, because I can't read Railway's logs" (a match that ran past a comma into the
# reason), and a prompt for another session whose fenced text named a dashboard. The replies below are
# that transcript's sentences verbatim (paths and ticket IDs made generic), trimmed to the paragraphs that
# matched and their neighbors.
text() { IFS= read -r -d '' "$1" || true; }

text FAIR1 <<'EOF'
The approved copy has changed, so it needs your review again. That's the gate doing its job, since this file runs next to the token. Run the installer once more, read the short diff, and type `y`:

```bash
sudo /opt/scripts/install-railway-config.sh
```

Then I'll run the plan and check first that it lists no variable deletes. Nothing is applied or committed.
EOF
fixture "$TMP/40.jsonl" 3 "$FAIR1"
check "2026-10-29 fair: a sudo installer with no diligence" block "$TMP/40.jsonl"

text FP2 <<'EOF'
The plan ran, and it's clean. Applying would add nothing, delete nothing, leave your variables alone, and change exactly the settings that vanish on December 1. Nothing was applied.

```
Project Viatica-Web
Environment production
variables: unmanaged · 31 on Railway not managed

Plan: 0 to add, 2 to change, 0 to destroy
  ~ Update Viatica Web Service build.buildCommand
    └ build.buildCommand (null → "npx vitest run && npx tsc --noEmit && npx next build")
```

**What this tells you:**
- **Your variables are untouched.** Railway reports all 31 as "not managed", and none would be deleted, so the `managed: false` setting is respected.
- **It confirms the December 1 risk.** Railway's dashboard currently has no build command, no start command and no restart policy, which is what the "null →" entries show. Today all four come only from `railway.json`. Applying copies the same values into Railway's own settings.

**What's left before December 1** (your call at each step):
1. Commit `.railway/railway.ts` and the one-line `tsconfig.json` change. Railway doesn't read `.railway/` on deploy, so committing them changes nothing in production.
2. Apply. The wrapper deliberately refuses to do it, so you'd run it yourself with the CLI, or I'd build a guarded way for you to approve it.
3. Switch Railway over from `railway.json` to the new file (Railway calls this "cutover"; it can be undone).
EOF
fixture "$TMP/41.jsonl" 3 "$FP2"
check "2026-10-29 false positive: plan report, future steps listed" allow "$TMP/41.jsonl"

text FP3 <<'EOF'
**The safe order:**
1. **Apply.** This writes the four values into Railway's settings. It shouldn't change today's deploys: `railway.json` still takes precedence and holds the same values. That's from Railway's docs, not tested.
2. **Run the plan again.** It should now say 0 changes, which proves the apply landed.
4. **One push that deletes `railway.json`.** That deploy runs on Railway's settings alone, so it's the real test.
5. **Watch that deploy.** The build log should show the tests running, and the start should show `prisma db push`. If anything's off, restoring `railway.json` and pushing again puts things back.

Two things to know about step 4:
- **It deploys whatever else is on master.** That's the two commits waiting there (the handout and this draft), and neither changes the app.
- **You'll need to read the build log.** I've been blocked from reading Railway's logs twice today. I can check the live site after the deploy, but not whether the tests ran.
EOF
fixture "$TMP/42.jsonl" 3 "$FP3"
check "2026-10-29 false positive: a proposed order of steps" allow "$TMP/42.jsonl"

text FP4 <<'EOF'
The redeploy succeeded and the site is serving normally.

**Where things stand:**
- **Railway's own settings now have the test gate, the start command and max retries of 3.** Before today they had none of these, so the December 1 cutoff would no longer silently drop them.
- **One setting still shows as pending:** the restart-policy type. Railway's docs say On Failure is the default, so it's most likely stored as "not set" and the plan will keep flagging it. That's inferred, not confirmed.
- **Not proven yet:** that a deploy runs correctly on Railway's settings alone. This redeploy still had `railway.json`, which takes precedence. The real test is a deploy with `railway.json` removed. You'd need to confirm that one in the build log, because I'm still blocked from reading Railway's logs.
EOF
fixture "$TMP/43.jsonl" 3 "$FP4"
check "2026-10-29 false positive: redeploy report" allow "$TMP/43.jsonl"

text FP5 <<'EOF'
The plan came back clean:

```
variables: unmanaged · 31 on Railway not managed
✓ Your Railway configuration is already up to date.
```

Railway's production settings now match `.railway/railway.ts` exactly: the test-gate build command, the start command with `prisma db push`, and 3 restart retries, with your 31 variables left alone.

The edit to `.railway/railway.ts` isn't committed yet. One step remains before December 1: a deploy with `railway.json` removed, with you checking its build log to confirm the tests still run.
EOF
fixture "$TMP/44.jsonl" 3 "$FP5"
check "2026-10-29 false positive: clean plan, next step named" allow "$TMP/44.jsonl"

text FP6 <<'EOF'
I've pushed 1.11.19, which removes `railway.json`, so this deploy is the first to build only from Railway's own settings. I don't have proof yet that it's serving. A watcher is running and I'll report as soon as it finishes.

The one thing it can't show is the build log itself, the line-by-line proof that the tests ran without `railway.json`. A successful build is strong evidence the gate ran, since the tests are the first step of the build command. But reading the log in Railway's dashboard is the only direct proof.
EOF
fixture "$TMP/45.jsonl" 3 "$FP6"
check "2026-10-29 false positive: a dashboard named in a report" allow "$TMP/45.jsonl"

text FP7 <<'EOF'
It's still up to date. After the 1.11.19 deploy, the plan says:

```
variables: unmanaged · 31 on Railway not managed
✓ Your Railway configuration is already up to date.
```

The installed library matches the fixed source and is root-owned, and both `plan` and `status` run again now that `railway.json` is gone. `status` still says "IaC file none", but that's only because the status check doesn't load the new config file. It doesn't mean anything is missing. The plan output had nothing that looks like a secret.

That closes the migration. Production builds and starts from Railway's own settings, which match `.railway/railway.ts` exactly. It runs 1.11.19, and it no longer depends on a file Railway stops reading on December 1.
EOF
fixture "$TMP/46.jsonl" 3 "$FP7"
check "2026-10-29 false positive: final report with fenced output" allow "$TMP/46.jsonl"

text FP8 <<'EOF'
Paste this into a new session. Both paths exist as written.

```text
Fix the Moses handoff gate's false positives.

2. Reports that mentioned a future or optional step in passing, e.g. "the log in Railway's dashboard is
   the only direct proof", or "with you checking its build log", inside a report of finished work.

3. Fix the rule as narrowly as you can. Fenced output and a mention of a future step are not an ask;
   a command Brad is expected to paste, or "go to/open/click" aimed at him, is.
```

The part I'd least want lost is step 2.
EOF
fixture "$TMP/47.jsonl" 3 "$FP8"
check "2026-10-29 false positive: a prompt for another session" allow "$TMP/47.jsonl"

# The other side of each narrowing. A vendor's settings in the possessive, a dashboard named after
# "in", and a fenced block are all still a handoff when the sentence is aimed at the operator.
fixture "$TMP/48.jsonl" 3 "Update Railway's settings so the build command runs the tests first."
check "possessive settings, imperative: still a handoff" block "$TMP/48.jsonl"

fixture "$TMP/49.jsonl" 3 "In Railway's dashboard, add the variable to the Customs service."
check "in a vendor's dashboard, then an imperative: still a handoff" block "$TMP/49.jsonl"

fixture "$TMP/50.jsonl" 3 "Open Stripe. Then flip the switch in the billing panel."
check "the ask split over two sentences: still a handoff" block "$TMP/50.jsonl"

fixture "$TMP/51.jsonl" 3 "Read the build log in Railway's dashboard and tell me whether vitest ran."
check "a dashboard named in an instruction: still a handoff" block "$TMP/51.jsonl"

fixture "$TMP/52.jsonl" 3 "$FP7
Last step, once you are back:

\`\`\`bash
sudo /opt/scripts/install-railway-config.sh
\`\`\`"
check "fenced output above a real sudo step: still a handoff" block "$TMP/52.jsonl"

# Found by replaying three weeks of real replies through both versions of the gate. This console
# walkthrough was blocked before only by accident, and the clause bound on "check that" let it through.
# It is a handoff on its face: a menu path, and steps aimed at the operator.
text WALK <<'EOF'
**2. Delete the old server.** In **Access controls → MCP Portals → MCP servers** tab, delete `moses`. Its server ID has to be free so we can reuse it.

**3. Add MCP server:**
- **Name:** `Moses`
- **HTTP URL:** `https://moses-origin.example.com/mcp`
- **Authentication:** **Custom headers**, not OAuth. OAuth makes it ask for endpoints our server doesn't publish.
- **Save and connect server**, then wait for **Ready**.

**5. Attach the server to the portal.** Open the **Moses** portal (`moses.example.com`) → **Servers** → **Select existing servers** → `moses`.

Tell me when it shows Ready. I'll call a Moses tool from here and check that the request lands in the server's log. That log check is how we told a Cloudflare problem from a Reserve problem last time.
EOF
fixture "$TMP/53.jsonl" 3 "$WALK"
check "a console walkthrough with a menu path" block "$TMP/53.jsonl"

fixture "$TMP/54.jsonl" 3 'Before retiring an address, check what in Cloudflare still points at it. That was the lesson from today.'
check "a lesson that names a vendor is not an ask" allow "$TMP/54.jsonl"

say
# ── The delegation trigger, 2026-10-14 ─────────────────────────────────────
# Whether to delegate moved out of the delegate skill and into the always-on hook text, so a session
# that never loads the skill still gets it. The hook runs from a copy with no skills/ directory at all,
# for both events that inject the list, and again on the full-text fallback (a file the short form
# cannot be derived from).
say "Delegation trigger — fixtures"
say
TRIGGER="independent subtasks that can proceed without each other's results, spawn sub-agents"
CAVEAT="Money, credentials and production work stay in front of"
HOOKS=$(dirname "$(readlink -f "$0")")
mkdir -p "$TMP/noskill/hooks" "$TMP/noskill/docs"
cp "$HOOKS/inject-commandments.sh" "$TMP/noskill/hooks/"
cp "$HOOKS/../docs/COMMANDMENTS.md" "$TMP/noskill/docs/"
printf '# Rules\n\n%s\n' "$(printf 'Not a numbered rule. %.0s' $(seq 40))" > "$TMP/noskill/docs/unparseable.md"

# injected <event> <commandments-file> — the additionalContext the hook hands the model.
injected() {
  printf '{"hook_event_name":"%s"}' "$1" \
    | CLAUDE_PLUGIN_OPTION_COMMANDMENTS_FILE="$2" bash "$TMP/noskill/hooks/inject-commandments.sh" 2>/dev/null \
    | python3 -c "
import json,sys
try: print(json.load(sys.stdin)['hookSpecificOutput']['additionalContext'])
except Exception: print('')" 2>/dev/null
}
# Present where spawning is right (a main session), ABSENT where it is not: a sub-agent (agents
# spawning agents) and a build job (no sub-agent tool; MOSES_BUILD_JOB is set by the runner). The
# money/credentials/production line must be in all of them.
for case in "SessionStart:COMMANDMENTS.md:yes:" "SessionStart:unparseable.md:yes:" \
            "SubagentStart:COMMANDMENTS.md:no:" "SessionStart:COMMANDMENTS.md:no:1"; do
  IFS=: read -r event file want build <<<"$case"
  src=$TMP/noskill/docs/$file
  ctx=$(MOSES_BUILD_JOB="$build" injected "$event" "$src")
  where=$event; [ -n "$build" ] && where="a build job"
  has=no; printf '%s' "$ctx" | grep -qF "$TRIGGER" && has=yes
  label="trigger $( [ "$want" = yes ] && echo injected || echo withheld ) for $where, no skill loaded ($file)"
  if [ "$has" = "$want" ] && printf '%s' "$ctx" | grep -qF "$CAVEAT"; then say "  PASS  $label"; pass=$((pass+1)); else say "  FAIL  $label"; fail=$((fail+1)); failures="$failures
  - $label: trigger present=$has, wanted $want (and the money/credentials line must always be there)"; fi
done

say "  $pass passed, $fail failed"
if [ "$fail" -gt 0 ]; then
  # The timer is quiet on success and loud on failure — a weekly "all fine" trains you to ignore it.
  printf 'HANDOFF GATE IS NOT DISCRIMINATING — %d fixture(s) failed:%s\n' "$fail" "$failures" >&2
  exit 1
fi
exit 0
