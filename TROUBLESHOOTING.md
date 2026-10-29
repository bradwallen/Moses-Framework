# Troubleshooting

**For the AI reading this:** each entry is a symptom someone actually hit, its cause, and the fix.
Find the symptom, check that the cause matches what you see, then fix it. Don't guess past this list
without saying you're guessing. If you fix something that isn't here, add an entry in the same shape.

The order is roughly how likely a new install is to hit each one.

## The plugin and its hooks

### Every prompt gets the commandments twice
**Cause:** The hooks are registered twice: once by the plugin (`hooks/hooks.json`) and once in
`~/.claude/settings.json`. Claude Code doesn't de-duplicate them.
**Fix:** Keep them in one place. If the plugin is installed, remove the `hooks` block from
`settings.json`. Run `/hooks` in a session to see what's registered.

### I edited a hook, skill or agent in my checkout and nothing changed
**Cause:** The installed plugin is a cached copy under `~/.claude/plugins/cache/`, pinned by `version`
in `.claude-plugin/plugin.json`. Edits to the checkout aren't read until that version changes.
**Fix:** Bump `version`, run `claude plugin update moses@moses-framework`, then start a new session
or run `/reload-plugins`.

### The Stop hook blocks my reply, or I can't tell whether the handoff gate still works
**Cause:** `hooks/gate-handoff.sh` blocks a reply that hands you a command to run or a console to open
without a "Before you touch anything" section saying what was already checked. It blocks at most
twice per turn.
**Fix:** Add the section: what you checked and what it showed, and what you couldn't check and why.
To test the gate itself, run `hooks/gate-handoff-selftest.sh`. It exits 1 if any fixture misbehaves.

### Relevant notes never show up in my prompts
**Cause:** `hooks/relevant-notes.py` fails open. If it errors, or no note clears its bar, nothing is
injected and nothing is shown. The bar is deliberate: a prompt needs two topic words that a note
shares, and short replies like "yes please" inject nothing.
**Fix:** Check `~/.local/state/moses/relevant-notes.log`. Every run adds a line with note names only.
An empty or missing log means the hook isn't firing, so check the first entry above. Test a prompt
directly with `hooks/relevant-notes.py --query "your words here"`.

### A job note or a locked decision doesn't stand out
**Cause:** The hook recognizes a job note by its name starting with `job-`, and a locked decision by a
`locked:` line in the note's frontmatter. Each gets its own slot ahead of the ordinary notes, but
only once it clears the same bar as any other note.
**Fix:** Check the name and the frontmatter. If it still doesn't match, put the words people use for
that task into the note's `description`, which counts three times as much as the body.

### The memory index by area is missing or out of date
**Cause:** `hooks/memory-areas.py` rebuilds `<memory>/areas/` at session start, from each note's
frontmatter. A note with no `description` is skipped on purpose. The areas come from
`<memory>/areas.conf` (one `slug<TAB>Title<TAB>regex` per line) if it exists, else four defaults.
**Fix:** `hooks/memory-areas.py --check` says which files are stale. Run it without `--check` to
rebuild. A note in the wrong area gets an `area: <slug>` line in its frontmatter. `hooks/memory-areas-selftest.sh` proves the generator against a throwaway corpus.

## Headless `claude -p` jobs

### A headless job has tools it shouldn't, such as web fetch
**Cause:** `--allowedTools` and `--disallowedTools` are permission lists. A built-in you didn't name is
still available. `--tools` is the actual inventory, and `""` means none.
**Fix:** Launch through `agent/claude_runner.py` (or `agent/claude-run`), which passes `--tools` and
`--restricted` from a declared profile. To check, run `agent/tool-wall-probe`. With
`TOOL_WALL_SABOTAGE=1` it must exit 1, which shows the probe can fail.

### The chat bot says "No such tool available" for a tool that exists
**Cause:** Usually an `ANTHROPIC_API_KEY` in the environment the `claude` CLI inherits. The CLI then
signs in as an API client, the MCP server is still pending when the tool set is fixed, and the model
sees no MCP tools. It also bills the API instead of your subscription.
**Fix:** Don't pass that variable to the CLI. `cli_env()` in `agent/conversation.py` strips it. Read
`apiKeySource` in the CLI's opening `init` event: `none` means your subscription login.

### Every headless turn fails with "OAuth session expired and could not be refreshed"
**Cause:** Headless jobs share the login in `~/.claude/` with your own sessions, and the refresh can
fail. Until someone logs in again, every turn fails.
**Fix:** Run `claude auth login --claudeai` as the user the service runs as. Keep
`agent/moses-liveness` on its timer so you hear about it within about 30 minutes, and
`agent/moses-deadman` so you hear if the liveness probe itself goes quiet.

## systemd

### `bootstrap.sh` says it KEPT moses.service
**Cause:** You already have a `moses.service` that differs from what this checkout would write,
usually because Moses already runs from another checkout. Replacing it would point your live service
here.
**Fix:** Read the diff it printed. If this checkout really should run Moses, run
`./bootstrap.sh --replace-unit`. Otherwise leave it. `tools/bootstrap-unit-selftest.sh` proves this
check.

### `systemctl --user` says "Failed to connect to bus", or a check says nothing is running
**Cause:** systemd can only answer about user units when `XDG_RUNTIME_DIR` is set. Cron, root, `sudo`
and `su` shells often don't have it.
**Fix:** `XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user status moses`. In scripts, export it
first, and treat "couldn't ask systemd" as unknown, not as "not running". Several shipped units set
`/run/user/1000`; change it if your user id differs.

### The drift check reports healthy services as missing
**Cause:** A check run as root that uses `Path.home()` or `systemctl --user` asks root's account, not
yours, so your own units look missing.
**Fix:** Run `agent/moses-drift` as your own user and compare. Key state and units to the owner of the
source tree, not to whoever runs the script.

### The bot says every persona "never reported", but their reports are in the channel
**Cause:** Two state directories got mixed up. Reports written by root land in `/var/lib/moses`, and
the listener was looking in its own writable directory.
**Fix:** Set `MOSES_REGISTRY_STATE` (shared, read-only, default `/var/lib/moses`) separately from
`MOSES_STATE` (the listener's own). See the comment near `REGISTRY_STATE` in `agent/listener.py`.

### A deferred reminder showed up at once instead of on its `after:` date
**Cause:** An item with both `after:` and `every:` is due immediately, because the interval counts
from the epoch for a new item.
**Fix:** Use one key per item. `agent/moses` refuses the pair with a line starting `CONFIG`.

### A timer shows `active` but never fires again after a reboot
**Cause:** `Persistent=true` on a timer that uses `OnBootSec=` and `OnUnitActiveSec=` can leave it
stuck with `NextElapseUSecMonotonic=infinity`. `Persistent=true` is only for `OnCalendar=` timers.
**Fix:** Remove the line from monotonic timers. Check with
`systemctl --user show <name>.timer -p NextElapseUSecMonotonic`.

## Network

### A Python service gets HTTP 403 from a site behind Cloudflare, but curl works
**Cause:** Cloudflare's bot rules can reject Python's default `Python-urllib/3.x` User-Agent. The 403
looks like an auth failure.
**Fix:** Send an explicit User-Agent, as `mcp/live_state.py` does. Treat a 403 separately from a 401
so you don't go chasing the credential.

### A long request through Cloudflare fails with HTTP 524 after about two minutes
**Cause:** Cloudflare drops a connection that stays silent for 125 seconds, even while the origin is
still working. The timer resets whenever bytes arrive.
**Fix:** Send a byte every few seconds, such as a heartbeat in a streamed response. The status code
has already gone out by then, so report a late failure inside the body.

## Working with Claude on this repo

### A chained shell command edited the wrong directory after a `cd` failed
**Cause:** In a Claude Code Bash call, `set -e` doesn't stop a chain. If `cd` fails, the later steps
run wherever you already were.
**Fix:** Put edits in a script with `|| exit 1` after each step, or work on a throwaway copy. Chains
that only read are safe.

### A guard prints a failure, but nothing was actually checked
**Cause:** A guard that crashes before checking anything (a missing dependency, an unreadable file)
can print the same failure as one that ran and found a problem. People then bypass it.
**Fix:** Read the first lines of the output. A stack trace or "module not found" means the check never
ran. Fix that, then run `tools/scan-secrets.sh --selftest` to confirm the scanner itself works.
