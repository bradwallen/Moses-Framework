# Knight's target registry

One file per repository Knight is allowed to work on. **A target that is not in this directory does
not exist**: `knight start --target <name>` refuses by name and lists what it does know, the same
shape as `moses-remediate`'s operation allowlist. Adding a repo here is a deliberate, reviewable act
— which is the point. Knight is a software developer, not a shell.

Every target answers the same four questions:

| Field | What it settles |
|---|---|
| `KT_SOURCE` | which repository he clones from — his sandbox is a clone of it, never the original |
| `KT_PREP` | what has to exist in the clone before an agent can build and test (deps, generated code) |
| `KT_GATE` | the objective check the **runner** runs after he commits. Green is the only thing that ships work |
| `KT_PUSH_MODE` | where green work lands: `direct` onto the mainline, or `branch` for a human to merge |

## The line of demarcation

`KT_PUSH_MODE` is the gate between "Knight ships this himself" and "Knight proposes this".

- **`direct`** — the product. Viatica has a real test suite, a type checker, and a deploy that
  refuses a red build. Knight's work lands on `master` and Railway builds it. This is proven and
  stays as it is.
- **`branch`** — Knight's own toolchain. Moses and the framework are what *run* Knight, so a change
  there can break the thing that would have caught the change. Green work is pushed as
  `knight/<slug>` and waits for a human. Nothing about the gate is weaker; only the landing.

That distinction is deliberate and is the whole reason a registry exists rather than a flag: it is
recorded per repository, not decided per job by whoever is typing.

## Adding a project

Copy the nearest existing file, fill in the four fields, write `<name>.brief` (what this codebase is
and how to work in it — the generic instructions about reports and scope are in `bin/knight` and
apply everywhere), then prove it:

    knight targets            # it appears, with its gate and landing mode
    knight doctor             # its source and push target are REACHED, not just named

**New projects start under version control on day one**, so this file is always fillable.
