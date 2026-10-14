---
name: delegate
description: When to use sub-agents to finish faster and when not to, how to brief them, and how to report what they did. The operator has authorized starting sub-agents without asking whenever these rules say it is the faster path. Use before any broad search, multi-file change, long log or test dig, or independent review, and whenever deciding whether to spawn, fork or work inline.
---

# Delegate when it is faster, not because it is available

**Standing authorization:** the operator has authorized starting sub-agents, including several in parallel, without asking first, whenever the rules below say delegation is the faster path. Treat this skill as that request. Everything else still applies: commandment IX governs what a sub-agent may do, exactly as it governs you.

## Delegate when
- **Two or more pieces of work are independent.** Split by file, never by topic, and name each agent's files and its off-limits lines. Parallel work is the only real wall-clock win.
- **A broad read-only sweep** where you need the conclusion, not the file dumps (many files, several naming conventions, "where is X used"). Use `moses:scout`. The dumps stay out of the main context, so every later turn re-sends less.
- **Output is long and only its summary matters:** a log dig, a full test run, a dependency audit.
- **A review.** A fresh context sees what yours has stopped seeing. Use `moses:reviewer`.

## Work inline when
- It is a quick, targeted change. A fresh sub-agent starts cold and spends minutes gathering context you already have.
- The steps depend on each other: plan, then build, then test in sequence.
- It needs back-and-forth with the operator.
- **The brief would be longer than the work.**
- It touches money, credentials or production. Keep that in front of the operator.

## Fork or fresh
- **Fork** when the side task needs what this conversation already knows. It inherits the context and reuses its cache, so it costs less than a fresh agent for the same job.
- **Fresh agent** when a clean slate is the point (a review), or the task needs none of the conversation.

## The brief is the work
A fresh agent sees nothing of this conversation. **Verify every fact before you put it in a brief**: a wrong id or path in a brief becomes the agent's wrong work. Include the goal, the exact files it owns, what is off limits, how to verify, and what to return.

For edits, use `moses:builder` (a separate git worktree per agent). Worktree isolation covers the current repo only; for another repo, tell the agent to `git -C <repo> worktree add` and work only there. **Never let a sub-agent merge into a tree that live services run from.**

## Its report is a claim
The operator never sees a sub-agent's report; you relay it. **Check what it says before you repeat it.** Re-run its test, read its diff, spot-check its findings. Report outcomes in the ship-and-report shape, and say which parts you verified yourself.

## Cost
Sub-agent tokens count against the same usage limits as the main session. Delegation buys time and a leaner main context, not savings. Several agents each returning long reports can cost more context than doing the work inline, so ask for summaries.
