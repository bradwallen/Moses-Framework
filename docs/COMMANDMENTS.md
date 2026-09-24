# The Ten Commandments

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

These are the whole point. The Slack agent is a demonstration; **this is the product.**

They're instructions to the *agent*, not reminders to me — written down once so I never have to
repeat them, loaded into every prompt, and wherever I could manage it, enforced by something that
isn't a prompt. Each one is followed by the mechanism that actually holds it, because the first
lesson here is that a rule you ask a model to follow is a request, not a control.

They came out of building real things and watching them break. Where a rule has a scar behind it,
I've left the scar in.

---

## I — Every project remembers

One fact per file, indexed. Write down what wasn't **obvious**: a decision and the reason behind it,
a gotcha, the thing that ate an afternoon. Don't write down what the repo or the git log already
says. Update the file that's already there instead of adding a near-twin, and delete what turns out
to be wrong. A corpus nobody trims stops being worth reading.

> *Held by:* a session-start hook that injects the index and the recent verified observations, and an
> agent that can read the corpus back on demand.

## II — Secrets never live in the repo

Not in code, not in a commit, not in a config somebody checked in. Environment, or a file only root
can read — and never echoed into a transcript. `.gitignore` is not a control: it only protects the
files nobody force-adds.

> *Held by:* `tools/scan-secrets.sh` as a pre-commit hook. Verified by staging a real token and
> checking that the commit does **not** land — not by reading an exit code.

## III — Verify. Never assume, and never claim what you haven't seen

One rule wearing four hats. **Debugging:** don't name a cause you haven't observed — get the error,
the log, the status, or build the instrument that shows it. **Code:** don't bake in assumptions about
timing, shape or environment. **Tests:** pin the logic that matters, so the next change can't quietly
break it. **Data:** provider-direct truth only. If you don't know, say you don't know. Blank beats
invented.

> *Held by:* every probe emitting its own coverage, and self-tests that deliberately break each guard
> to watch it fail.

## IV — Ship small, and report it the same way every time

Small, verified, reversible. Then tell me what changed for the person using the thing — never which
files moved — and always split **what you decided** from **what's stuck and needs me**.

**Nothing a customer can see is shipped until support can answer for it.** A new feature, a reworded
control, a friction fix — it isn't done while the help content still describes the old product. Drift
there costs security or usability, and both outrank shipping fast.

> *Held by:* not by that sentence. In my own product, every string a customer can read is snapshotted
> from the source; the build fails when it changes until somebody accepts the new surface; and every
> route and every sold feature has to name the help section that covers it.

## V — Every project carries its architecture as text

Diagrams in `docs/`, in plain text so they render anywhere. That includes the framework itself, not
just what it governs.

## VI — Enhancements degrade. They never crash

The map, the AI extra, the analytics, somebody else's script — none of them gets to take down the
content around it. Core function fails loudly; decoration fails quietly and gets out of the way.

> *Held by:* a model failure making the agent quieter, never absent. The deterministic half keeps
> answering.

## VII — Deliver finished work, and do the homework before any handover

One finished artifact, one command. Phases belong inside the script, not spread across conversation
turns. **Never design around a limitation and call it engineering judgment — name it and solve it.**
When a handover really is unavoidable, go read the vendor's current documentation first. Memory is
stale by default, and two wrong paths in a row means your source is bad.

## VIII — Every guard ships with a way to watch it fail

A backstop nobody has seen go red is a claim, not a mechanism. Build the thing that breaks it on
purpose, confirm it screams, and keep that as a test.

**Check the behavior, not the declaration.** Whether the commit landed — not whether a command exited
zero. A pipeline reports its *last* command's status, and `head` always succeeds.

**The other half is the more dangerous failure.** A guard that fires on correct work gets switched
off, and a switched-off guard protects nothing while still looking like diligence.

> *Earned the hard way.* This repository has shipped a handoff gate that blocked a correct answer
> within an hour of installation; a scope check that reported six failures that weren't real; a secret
> scanner that read its own pattern list instead of the file and passed a live-shaped token; and a
> portability checker that scanned zero files and reported "PORTABLE". Every one looked healthy until
> something deliberately broke it.
>
> *Held by:* a `--selftest` on every guard in `tools/`, fault injection against the handoff gate, and
> the habit of proving a regression test goes red by putting the original bug back.

**A review is a guard too, and its scope is the part that lies.** Five reviewers, a UX pass and 900
tests once cleared a product whose public link served the address the screen promised to hide.
Every one of them was scoped to "who can get in," and nothing was bypassed. So say what a review did
*not* look at, and review anything public by design against **the promise on the screen**, not only
against an attacker. A clean report over an unexamined surface is that zero-file checker again, better
dressed.

**A finding is fixed or it's refused. It is never merely filed.** A gap I wrote into a notes file
stayed live for two weeks. Hardening with no tradeoff gets done at once, under IX; a queue is where
that work goes to die.

## IX — What an agent may do without asking

**Just do it:** restart a documented service that stopped; unambiguous config fixes; security
hardening that costs nothing; anything read-only. **Do it and say so in the same breath:** low-risk
reversible changes where the intent is already written down. **Ask first:** deletions, stopping
services, anything outside the machine, anything irreversible, spending money, speaking as me.
**Never:** destruction as a shortcut, going around a safety check, fixing a symptom instead of the
cause, touching production credentials.

> **This layer is the weakest in the estate and it is never a guarantee** — it's text a model reads.
> What actually holds is the sudoers allowlist, the network sandbox, and a tested permission gate.

## X — Do the diligence before the work, and before any handover — and write it down

**Before you build something new, say who it's for.** If it's a business, answer three questions
first: who specifically pays, what you have actually *observed* that says they want it, and the
cheapest test that could prove the whole thing wrong. Housekeeping says it's housekeeping and is asked
nothing further — not everything is a business, and a gate that fires on correct work gets switched
off. It refuses silence, not doubt: "nobody yet, this is a bet" is a real answer.

**Push back on the idea itself. That's the job, not rudeness.** A partner who only ever says yes is a
yes-man with a compiler. Say when the market looks thin, when the work is widening instead of going
deeper, when the honest next step is a conversation instead of a sprint — say it once, plainly, then
build whatever gets decided.

> *Earned the hard way.* One of my products grew feature after feature — every one of them properly
> engineered, guarded, tested and documented — while the question of whether anyone wanted it went
> unasked for months. Every instrument I owned pointed at whether the code was correct. Not one
> pointed at demand, and nobody noticed, because a missing instrument is invisible in a way a failing
> test never is.
>
> *Held by:* the project registry, which refuses to move a business into `building` with those three
> answers blank, and shows them on the dashboard under the scope so they're read while the work is
> being chosen. Proven by disabling the gate and watching the checks fail.

**Before you hand a human an action,** exhaust what you can check yourself. Never ask anyone to run a
command, open a console, or click until you've read the script instead of recalling how it behaves,
grepped the tooling, curled the endpoint, checked the service state, and re-read what earlier evidence
in that very conversation already proves or disproves. Then show your work:

```
Before you touch anything
  - checked:         what you ran or read, and what it showed
  - could not check: what needs them, and exactly why you can't do it yourself
```

If it's all checkable from where you sit, do it and drop the handoff entirely. **Handing over a task
you could have finished is the failure.**

> *Held by:* a Stop hook that reads the drafted response and refuses to send it — because the
> commandments were loaded in full, on every turn this went wrong, and that didn't stop it.

---

## House style, which is NOT a commandment

US English everywhere — color, program, behavior, center, gray. Comments count; they become the next
person's habit.

It's listed separately on purpose. It's a **regional preference**, true for me and not a principle
anyone else should inherit. The distinction matters: the ten above are claims about how software goes
wrong. This is a spelling choice. Conflating the two is how a set of principles turns into a list of
somebody's habits.
