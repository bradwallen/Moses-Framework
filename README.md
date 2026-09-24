# Moses

*Keeper of the Commandments, the project of projects.*

**A standards layer for someone who runs more projects than they can hold in their head.**

Moses is not a chatbot and not a monitoring tool. **He is a set of operating principles with
machinery attached** — ten rules about how work gets done, loaded into every prompt, and enforced
wherever possible by something that is not a prompt.

The Slack agent, the personas, the memory: those are how the rules reach your day. The rules are the
part worth having.

He runs on your own machine, against your own Claude subscription. There is no API key anywhere in
this repository, and adding one should be a decision you make out loud.


```
        ______________________     ______________________
       /                      \   /                      \
      |    I   MEMORY          | |   VI   DEGRADE         |
      |   II   SECRETS         | |  VII   FINISH IT       |
      |  III   VERIFY          | | VIII   PROVE THE GUARD |
      |   IV   SHIP SMALL      | |   IX   ACT POLICY      |
      |    V   DIAGRAMS        | |    X   DILIGENCE       |
      |                        | |                        |
      |________________________| |________________________|
```

**I** — Every project remembers. One fact per file. Write down what wasn't obvious.
**II** — Secrets never live in the repo. Not in code, not in a commit, not in a config.
**III** — Verify. Don't name a cause you haven't seen; build the instrument that shows it.
**IV** — Ship small, and report it the same way every time.
**V** — Every project carries its architecture as text.
**VI** — Enhancements degrade. They never crash.
**VII** — Deliver finished work. Never design around a limitation and call it judgment.
**VIII** — Every guard ships with a way to watch it fail. Check the behavior, not the declaration.
**IX** — A clear policy on what an agent may do without asking — and the knowledge that the policy is
the weakest layer, because it's text a model reads.
**X** — Do the diligence before the work and before any handover. Say who a business is for and what
would prove it wrong, push back on the idea itself, and write down what you checked and what you
couldn't.

Each one is followed in [`docs/COMMANDMENTS.md`](docs/COMMANDMENTS.md) by **the mechanism that
actually holds it** — a hook, a permission gate, a self-test — because the first thing this project
learned is that a rule a model is *asked* to follow is a request, not a control.

---

## What you actually get

**A memory that outlives the conversation.** One fact per file, indexed, written when something is
learned rather than when someone remembers to write it down. Moses reads it back — so "why did we do
it that way?" has an answer six months later.

**Personas that own ground.** Small scheduled jobs that report into Slack under their own names, each
accountable for one thing: backups, finances, support, health, code. Silence from one is a failure
with a name on it, not an absence.

**A colleague in a channel.** Address him the way you address a person — "Hey Moses, …" — and he
answers, reads his own memory to do it, and can decide that a 👍 or saying nothing is the better
reply. Anyone in the channel can stop him with **"stand down"**; only you can start him again.

**Diagnosis, never automatic repair.** When a persona reports trouble, Moses gathers evidence and
tells you whether the alarm is *real*, a *false alarm*, or *unclear* — with the evidence attached.
He does not fix it. Every verdict is recorded, so whether he should ever be allowed to act is a
decision you make against a number rather than a feeling.

**Work that survives the chat.** He can propose a task; you confirm it with a word or a ✅; and
deterministic code — never the model — writes it to your project board. Every project gets a board,
a changelog and a searchable page on a small local dashboard.

**A builder who cannot ship on his own say-so.** Knight takes a task from Moses, works in his own
clone with no remote, and must pass the target's own tests and a separate reviewer — who judges it
against what *you* said must be true, written down before any code existed. Green work lands on a
branch; merging and restarting happen when you say so (`knight_land`), and the restart checks itself.

**A tester who goes looking.** Zryachiy takes "test X" from Slack, explores the running product in a
sandbox against stated criteria, and reports clean, findings, or could-not-finish — the verdict comes
from the runner's exit code, never from the model's opinion of its own work.

**One command line for every agent.** Every `claude -p` any of them runs is built in one place, with
the tools each one may use granted by name and the rest removed — not denied by a list that has to
think of everything.

---

## The ideas it is built on

These are not decoration. Each one is here because its absence cost a day.

**Prose is the weakest layer.** A rule a model is asked to follow is a request. A rule enforced by a
hook, a permission gate, or a file it cannot read is a mechanism. Every guard in here is the second
kind, and the first thing any of them does is prove it can still fail.

**A check that could not look must never say "broken."** "I found nothing wrong" and "I never looked"
are the same sentence unless a probe states what it actually examined and under whose identity. Every
probe here emits its own coverage. This one cost the most to learn.

**Verify, never assert.** Don't name a cause you haven't seen; build the instrument that shows it.
Don't test the logic outside the environment it runs in — the seam is where the bugs live.

**A limiter that reaches a human is a bug.** Rate limits, caps and budgets exist to bound machines
talking to machines. The moment one makes the agent ignore *you*, it is a fault wearing a safety
feature's clothes.

**Enhancements degrade, they never crash.** A model failure makes Moses quieter, not absent. The
deterministic half keeps answering.

---

## Install

Ubuntu, Python 3.11+, and the [`claude` CLI](https://claude.com/claude-code) logged in as the user
who will run this.

```bash
git clone <your-fork> ~/moses
cd ~/moses
./bootstrap.sh
```

`bootstrap.sh` creates a virtualenv, installs the pre-commit secret scanner, writes systemd **user**
units (no root — see below), and stops to tell you exactly which values it still needs. It changes
nothing it has not first shown you.

Then put your Slack tokens in `~/.config/moses/slack.env` and your ids and channels in
`~/.config/moses/moses.env` (both created from `config/`), and start him:

```bash
systemctl --user enable --now moses
```

### It deliberately does not want root

The service binds no port and touches only your files, so it runs as **you**, under a systemd user
unit. That is not a limitation worked around — it is the reason deploying a change is
`systemctl --user restart moses` and never `sudo`. The only pieces that need privilege are the ones
that genuinely install into `/usr/local/bin`, and they are separate scripts you run once.

---

## Framework, and yours

| Framework — yours to update from upstream | Yours — never leaves your machine |
|---|---|
| `agent/` the Slack agent, its guards, the watchdogs, the systemd units | your memory corpus |
| `knight/` the builder, the reviewer, the explorer | `~/.config/moses/*.env` — tokens and settings |
| `mcp/` the MCP server and the dashboard | `~/.config/moses/roster.json`, `estate.md` |
| `personas/`, `install/` the reporting jobs | `~/.local/state/moses` — ledgers, proposals, verdicts |
| `hooks/` Claude Code hooks · `config/*.example` · `tools/` | `knight/targets/` — the repositories Knight may touch |

**Your data stays yours.** The corpus is where Moses keeps what he knows about *your* projects —
decisions, money, infrastructure, people. This repository ships the shape of a corpus and never its
contents. `.gitignore` refuses `memory/`, `state/` and every `*.env`, and `tools/scan-secrets.sh`
runs as a pre-commit hook and refuses anything token-shaped. Both, because a `.gitignore` only
protects files nobody force-adds.

Run `tools/scan-secrets.sh --selftest` to watch it catch six real token shapes and correctly ignore
the four legitimate ones this repo contains.

---

## Status

Working and in daily use by its author. **This repository is his running tree, filtered** — not a copy
kept by hand. A map in his tree marks every file as shipped or private (with the reason), and a sync
copies what ships byte for byte and refuses a file the map does not cover, a secret, or a file this
repository would silently ignore. So what you read here is what runs.

**Not yet fully portable, and measured rather than estimated.** Nothing personal is written in the code
any more — paths come from your home, everything else from `~/.config/moses/moses.env` — and the
author's ids, addresses and home paths are gone from it. Run `tools/check-portability.sh` for what is
left: the name of the author's own product (Knight's explorer and one MCP tool are built around it, as
worked examples) and his personas' display names. It stays advisory until that count reaches zero,
then becomes `--strict` — the honest definition of "it is a framework now".

**Run as a stranger, the tests say what is missing rather than pass.** With an empty home and none of
the author's settings, the suites that need something you have not set up yet — a roster, a Knight
target, a logged-in CLI, Playwright for one browser test — fail by naming it.

Read `docs/ARCHITECTURE.md` for how the pieces fit, and `docs/PRINCIPLES.md` for the rules above with
the incidents that produced them.

Built alongside [Atlas](https://github.com/ecpunk/atlas-framework), a different answer to a
neighboring problem — two agents, built by two people, that compare notes.

---

## License

Apache License 2.0 — see [LICENSE](LICENSE). Use it, change it, run it commercially; the patent grant
is explicit, and you have to say what you changed.

The commandments in `docs/COMMANDMENTS.md` are one person's operating principles, and the point is
that you write your own. Take them as a worked example, not a rule set to obey.
