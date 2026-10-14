---
name: diligence
description: Commandment X as a procedure — the checks to run before starting non-trivial work and before handing the operator any action (a command to run, a console to open, something to click or paste), and the "Before you touch anything" block the Stop hook requires. Use before asking the operator to do anything, and before work that publishes, touches security, money or credentials, or changes what a customer reads.
---

# Do the diligence before the work, and before any handover

## Match the ceremony to the stakes
Local, reversible, nobody-sees-it work (a registry write, a one-line fix, a note) is done and reported in a line. No diligence block, no memory entry, no verification pass. The full weight below is for work that **publishes, touches security or money, changes what a customer reads, or hands the operator an action.** A process that turns a thirty-second task into five minutes gets routed around.

## Before building something new
Say who it is for. If it is a business, answer three questions first: **who specifically pays**, **what you have actually observed** that says they want it, and **the cheapest test that could prove it wrong.** "Nobody yet, this is a bet" is a real answer; silence is not. Housekeeping says it is housekeeping and is asked nothing further.

Push back on the idea itself when it is warranted: once, plainly, then build what is decided.

## Before handing the operator an action
Exhaust what you can check yourself first:
- **Read the script** instead of recalling how it behaves.
- **Grep the tooling** for which files and variables it actually reads.
- **Hit the endpoint**, query the database, check the service state.
- **Read the vendor's current docs** before describing their console. Say whether you are quoting the docs or inferring.
- **Re-read this conversation**: earlier evidence may already prove or disprove the thing.

If everything is checkable from where you sit, do it and drop the handoff. Handing over a task you could have finished is the failure.

## The block
When an ask remains, it ends the turn, with nothing after it, and comes with this block (the Stop hook looks for the heading and refuses a handoff without it, or a claim made with no tool calls behind it):

```
**Before you touch anything**
- checked: <what you ran or read, and what it showed>
- could not check: <what needs the operator, and exactly why you can't do it yourself>
```

## Write it down
A decision and its reason, a gotcha, the thing that ate an afternoon: record it (see the memory-hygiene skill) so the next session doesn't re-derive it.
