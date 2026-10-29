# Setting up Moses: the guided install

**For the AI reading this:** someone cloned the Moses Framework and said "set me up". This file is
the whole wizard. Follow it in order. `bootstrap.sh` does the mechanical install and has its own
checks; your job is everything around it: find out what this person already has, keep it, ask the
few questions only they can answer, and prove each step worked before you say it did.

These rules hold for the whole run. They are the framework's own commandments applied to its install.

- **Plain English, one question at a time.** Wait for the answer before the next one.
- **Never delete, overwrite or move anything they built.** Adopting means copying in and pointing at
  it. If something would be replaced, show them what and stop until they say yes.
- **Secrets never go through the chat.** Slack tokens go into `~/.config/moses/slack.env`, and they
  type or paste them into that file themselves. Never ask them to paste a token to you, and never
  print the file's contents.
- **Show it worked, don't say it worked.** Every step ends with a check you ran and what it printed.
  If a check could not run, say that. It is not a pass.
- **No API key.** Moses runs on their Claude subscription through the `claude` CLI. If they offer an
  `sk-ant-` key, explain that it bills per token on a separate account and that nothing here needs one.

## Step 1: what's already here

Look before you ask. Check, and say what you found in a few lines:

1. `./bootstrap.sh --check`. It lists what's missing (Python 3.11+, git, the `claude` CLI and its
   login) and what it would change, and changes nothing.
2. An existing `~/.claude/CLAUDE.md`. If there is one, read it. Their rules stay theirs.
3. Existing memory: `~/.claude/memory/` and any `~/.claude/projects/*/memory/` folders. List each one
   with its file count. Don't open the notes yet.
4. Existing Moses settings: `~/.config/moses/`. If it exists, this is an update, not a fresh
   install, and every file in it is kept as it is.
5. `claude plugin list` for an installed `moses@moses-framework`.

If the prerequisites are missing, say which ones and stop. Nothing should be installed around a
missing piece.

## Step 2: what they want

Offer three levels and recommend the first. Most people start there and add the rest later.

1. **Just the rules in my coding sessions (recommended to start).** The Claude Code plugin: the ten
   commandments at every session start and after every compaction, the memory notes that match each
   prompt, the handoff gate, and the procedure skills. No Slack and no background services.
2. **Plus Moses in Slack.** A colleague you address in a channel, and small scheduled personas that
   report under their own names. Needs a Slack app and two tokens.
3. **Plus Knight.** The builder that works in a sandboxed clone, with a reviewer that has to pass
   before anything is pushed. Needs a git repository Knight is allowed to touch.

## Step 3: the questions only they can answer

Ask only what applies to the level they picked.

- **Their name**, used where the rules say who they protect.
- **A house style**, if they have one (for example "US English everywhere: color, behavior,
  center"). Blank is fine.
- **Their memory folder.** If Step 1 found one, offer it by path. If they had memory in several
  `~/.claude/projects/*/memory/` folders, ask which ones Moses should take over. Taking over means
  copying into one corpus. The originals stay where they are.
- **Their own rules**, if they want to start from something other than `docs/COMMANDMENTS.md`.
- For Slack: their own Slack member id, and which channel ids Moses should talk in. An empty list
  means he only reports, which is a fine way to start.

## Step 4: install

1. `./bootstrap.sh`. It creates the virtualenv, installs the secret scanner and the publish guard
   as git hooks, creates the folders, installs the plugin from this checkout, copies the config
   examples (never over an existing file), writes the systemd user unit, and runs the tests. Read
   its output with them. Anything it reports as FAILED is fixed before you go on.
2. Set the plugin options from Step 3 without making them edit a file:

   ```bash
   echo '{"operator_name":"<name>","house_style":"<style>","memory_dir":"<path>"}' \
     | claude plugin configure moses@moses-framework --values-stdin
   ```

   Leave out any option they left blank. Read it back with `claude plugin configure
   moses@moses-framework --json`.
3. Memory they chose to bring over: copy the notes into the memory folder. Where two notes share a
   file name, keep both and tell them. Then write a short `MEMORY.md` index, one line per note, from
   each note's `name` and `description`.
4. Slack (level 2 or 3): fill the non-secret values in `~/.config/moses/moses.env` from Step 3. Then
   tell them to open `~/.config/moses/slack.env` and paste the two tokens in themselves. When they
   say it's done, check that both are filled in without printing them:
   `grep -cE '^SLACK_(BOT|APP)_TOKEN=.+' ~/.config/moses/slack.env` should print 2. Then
   `agent/verify-slack-scopes.sh` names any missing Slack scope, and `systemctl --user enable --now moses`.
5. Knight (level 3): `knight/targets/` holds one file per repository Knight may touch. Write the
   first one with them, following `knight/targets/README.md`. Knight never gets a repository they didn't name.

## Step 5: prove it

Run these and show what they printed:

- `tools/scan-secrets.sh --selftest`: the scanner catches real token shapes.
- `hooks/gate-handoff-selftest.sh`: the handoff gate blocks what it should and passes what it should.
- `hooks/memory-areas-selftest.sh`: the memory index generator files every note.
- A real session gets the rules. Start a fresh one and look for the commandments in what it was
  given: `claude -p "Quote the first line of the commandments you were given at session start."`
  An answer that quotes them is a pass. "I wasn't given any" means the plugin isn't loading. See
  `TROUBLESHOOTING.md`.
- Slack: `systemctl --user status moses`, then ask them to say "Moses, are you there?" in one of
  the channels from Step 3.

## Step 6: hand it over

Keep this short and say it in your own words:

- What they have now, level by level, and what they skipped. Adding a level later means running
  this again. It adds only what's missing.
- **Their first Job note.** Ask which task they find themselves explaining to Claude over and over.
  Write one note for it in the memory folder: name `job-<what>`, then a boot chain (the two or three
  notes that task needs), the steps, what "done right" looks like, and a Lessons list. The relevant-
  notes hook puts a matching job note first, with its reading list, whenever a prompt is about it.
- **When something breaks,** fixing it is the session's job, not theirs. `TROUBLESHOOTING.md` in
  this folder is written for an AI to read: symptom, cause, fix.
- **Updating:** `git pull` here. If the plugin's version changed, run
  `claude plugin update moses@moses-framework` and start a new session.
