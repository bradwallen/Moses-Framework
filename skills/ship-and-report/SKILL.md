---
name: ship-and-report
description: Commandment IV as a procedure — how to ship a change small and report it the same way every time. Use when finishing a change that will be committed, pushed, deployed or released, and when writing the summary of finished work.
---

# Ship small, and report it the same way every time

**Small, verified, reversible.** One change per commit, each one able to be reverted alone.

## Before it ships
1. **Verify the behavior, not the declaration.** Run the test, load the page, hit the endpoint. "The tests passed" and "the thing works" are different claims; make the one you can stand behind.
2. **Stage explicit paths.** Never `git add -A` or `git add .`; name each file so nothing unrelated rides along.
3. **Anything a customer can see is not shipped until support can answer for it.** If the change rewords a control, adds a feature or removes friction, update the help content, the support agent's knowledge and the release notes in the same change.
4. **Push to the project's dev deployment** when it has one, so the operator can watch it build. Pushing to anything outside this machine is still governed by commandment IX. **In a sandbox where a pipeline pushes for you** (a builder agent, a CI runner): commit on your branch with real messages and stop; the pipeline runs the gate and ships it.

## The report
Every summary has the same shape, in this order:

1. **What changed, for the person using the thing.** Product terms, not file names. "Signups now say when each person joined," not "edited users.tsx."
2. **Where it is.** The branch name, and the deployment or version it lands in.
3. **How it was verified**, in a line: what was run or observed, and what it showed. If something was not verified, say so.
4. **What you decided** on the operator's behalf. Usually fine, but surfaced, not buried.
5. **What's stuck and needs the operator.** Last, so it is the thing they read before replying. If nothing, say nothing.

Keep it short. A report the operator has to dig through is one they stop reading.
