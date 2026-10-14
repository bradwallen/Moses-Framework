---
name: memory-hygiene
description: Commandment I as a procedure — when and how to write, update, link and delete memory notes so the corpus stays worth reading. Use when a decision, a gotcha or a hard-won fact should outlive the session, when the operator says to remember something, or when a recalled note turns out to be wrong.
---

# Every project remembers

## What earns a note
Write down what was **not obvious**: a decision and the reason behind it, a gotcha, the thing that ate an afternoon, a preference the operator stated. **Not** what the repo, the code or the git log already says. A note restating the code goes stale the day the code changes.

## How to write one
1. **Search first.** If a note already covers it, update that file. A near-twin splits the truth in two.
2. **One fact per file**, with frontmatter: `name` (kebab-case slug), `description` (the one line used to decide relevance), and `type` (user, feedback, project, reference).
3. **For feedback and project notes, give the why.** End with **Why:** and **How to apply:** lines. A rule without its reason can't be applied to the edge case it was written for.
4. **Absolute dates.** "Yesterday" means nothing next month.
5. **Link related notes** with `[[their-name]]`.
6. **Add one line to the index** (`MEMORY.md`): title, file, and a hook. Never the content itself.

## How to keep it honest
- **A note is a record, not state.** It was true when written. Before reporting status from a note, read the live thing: the repo, the service, the endpoint.
- **Say what the note says, not that it's right.** "My notes say X as of <date>" is honest; "X is true" from a note is a claim you haven't verified.
- **Delete what turns out to be wrong**, and trim what stopped mattering. A corpus nobody trims stops being worth reading.
- **Never write secrets into a note** (commandment II), and never write what only matters to this conversation.
