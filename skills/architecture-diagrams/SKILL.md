---
name: architecture-diagrams
description: Commandment V as a procedure — create or update a project's docs/ARCHITECTURE.md with text diagrams of its components and flows. Use when adding or removing a service, host, route, job, external caller or data store, when changing how requests flow, or when a project has no architecture doc.
---

# Every project carries its architecture and flow diagrams

The doc lives at `docs/ARCHITECTURE.md` in the project's repo, **as text** (ASCII or Mermaid) so it renders anywhere: a terminal, a diff, GitHub, a code review.

## When to touch it
In the **same change** that does any of these:
- adds, moves or removes a service, host, worker, cron or timer
- adds an external caller or a new integration (webhooks, APIs, email, queues)
- changes where a request goes, or which component owns a piece of data
- retires something: delete it from the diagram, don't leave it drawn

A component in no document is invisible to every check that validates what the docs claim. That is how a running service ends up owned by nobody.

## What it must contain
1. **Components:** each process or service, where it runs, and what it is for, in one line.
2. **Flows:** the main request and data paths, drawn. One diagram per flow beats one diagram of everything.
3. **Every caller of every address.** For each endpoint or host, who calls it. This is the list you need when you move or retire it.
4. **Where config and secrets come from:** names and locations, never values.
5. **What degrades:** which pieces are enhancements and fail quietly, and which are core and fail loudly (commandment VI).

## How to check it
Read the doc against the running thing, not against memory: list the services, the routes, the timers, and confirm each one appears. Anything running that is not drawn is drift, so add it or account for it.
