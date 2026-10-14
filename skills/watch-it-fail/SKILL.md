---
name: watch-it-fail
description: Commandment VIII as a procedure — prove a guard works by breaking it on purpose and keeping that as a test. Use when building or changing any guard, gate, check, scanner, alert, validation, permission rule, hook or test, and when reviewing one.
---

# Every guard ships with a way to watch it fail

A backstop nobody has seen go red is a claim, not a mechanism.

## The procedure
1. **Feed it the thing it exists to catch.** A real-shaped token for a secret scanner, a real bad request for a validator, a fixture of the exact failure that prompted the guard.
2. **Check the behavior, not the declaration.** Did the commit actually land? Did the request actually get refused? Not "the command exited 0": a pipeline reports its last command's status, and `| head` always succeeds.
3. **Weaken the guard and confirm the test goes red,** naming the failing case. A test that passes against a broken guard proves nothing.
4. **Feed it correct work and confirm it stays quiet.** A guard that fires on correct work gets switched off, and a switched-off guard protects nothing. The false positive is the more dangerous failure.
5. **Keep both as a test that runs on a schedule**, not a one-time demonstration. Log every real firing so its rate is visible rather than assumed.
6. **Fail safe on the right side.** Decide out loud whether a broken guard fails open (never wedge the work) or closed (never let the danger through), and test that path too.

## When reviewing
**Say what the review did not look at.** A review's scope is the thing that lies: a clean report over an unexamined surface is not a clean report. For anything public by design, review it against the promise on the screen, not only against an attacker.

**A finding is fixed or it is refused. It is never merely filed.**
