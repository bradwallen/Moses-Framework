---
name: delegate
description: How to use sub-agents once the decision is made: fork or fresh, which agent, how to brief them, and how to report what they did. Whether to delegate is decided by the trigger in the always-on hook text (independent subtasks), not here. Use before spawning or forking any sub-agent.
---

# Delegate well

**Standing authorization:** the operator has authorized starting sub-agents, including several in parallel, without asking first, whenever the delegation trigger in the always-on hook text applies. Treat this skill as the how-to for that. Everything else still applies: commandment IX governs what a sub-agent may do, exactly as it governs you.

## Fork or fresh
- **Fork** when the side task needs what this conversation already knows. It inherits the context and reuses its cache, so it costs less than a fresh agent for the same job.
- **Fresh agent** when a clean slate is the point (a review: use `moses:reviewer`), or the task needs none of the conversation.

## The brief is the work
A fresh agent sees nothing of this conversation. **Verify every fact before you put it in a brief**: a wrong id or path in a brief becomes the agent's wrong work. Include the goal, the exact files it owns, what is off limits, how to verify, and what to return. Split parallel work by file, never by topic.

For read-only sweeps, use `moses:explore`: the file dumps stay out of the main context. For edits, use `moses:builder` (a separate git worktree per agent). Worktree isolation covers the current repo only; for another repo, tell the agent to `git -C <repo> worktree add` and work only there. **Never let a sub-agent merge into a tree that live services run from.**

## Its report is a claim
The operator never sees a sub-agent's report; you relay it. **Check what it says before you repeat it.** Re-run its test, read its diff, spot-check its findings. Report outcomes in the ship-and-report shape, and say which parts you verified yourself.

## Cost
Sub-agent tokens count against the same usage limits as the main session. Delegation buys time and a leaner main context, not savings. Several agents each returning long reports can cost more context than doing the work inline, so ask for summaries.
