"""dispatch — decide what a message to @Moses actually is, and handle the cheap cases for free.

Three kinds of message arrive:

  question — "what are the commandments", "who owns the backups", "is anyone behind"
             Answered from the cached corpus on the existing path. No tools, no agent session.

  capture  — "add X to my list", "remember that Y", "log this idea"
             Handled RIGHT HERE with string handling and a file append. No model call at all.
             Appending a line to a markdown file does not need a language model, and paying for one
             would be a bad trade every single time: slower, costlier, and able to get it wrong.

  work     — "research X", "look into Y"
             Needs tools and a real agent session, metered against the budget.

Classification is deliberately boring — explicit leading verbs, not intent inference. A regex that
misses falls through to `question`, which answers helpfully and costs little. An intent classifier
that guesses wrong spends money doing the wrong thing. Cheap failure beats clever failure.
"""

from __future__ import annotations

import sys as _sys, pathlib as _pl  # noqa: E401
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent / "lib"))
import moses_env as _env  # noqa: E402 — whose home, where Moses lives, the operator's settings
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

Kind = Literal["capture", "research", "question"]

MCP_DIR = str(_env.ROOT / "mcp")
MEMORY = _env.MEMORY

# "add X to my list" / "remember X" / "log the idea that X" — the closing target is optional, and
# the destination is inferred from which word was used (todo vs idea).
_CAPTURE = re.compile(
    r"""^\s*(?:add|remember|note|log|capture|jot\s+down|put)\s+
        (?:(?:this|that)\s+)?
        (?P<body>.+?)
        (?:\s+(?:to|on|in)\s+(?:my\s+|the\s+)?(?P<target>to-?do(?:\s*list)?|list|ideas?(?:\s*list)?|backlog))?
        \s*$""",
    re.I | re.X | re.S,
)
_RESEARCH = re.compile(
    r"^\s*(?:research|look\s+into|dig\s+into|investigate|find\s+out(?:\s+about)?|"
    r"read\s+up\s+on|what'?s\s+the\s+state\s+of)\s+(?P<body>.+)$",
    re.I | re.S,
)
# An idea framed as a thought rather than an instruction: "I have an idea — should we X".
_IDEA = re.compile(r"^\s*i(?:'ve| have)?\s+(?:an?\s+)?(?:idea|thought)\b[\s,:—-]*(?P<body>.+)$", re.I | re.S)


@dataclass(frozen=True)
class Intent:
    kind: Kind
    body: str = ""
    target: str = ""     # "todo" or "ideas", for capture


def classify(text: str) -> Intent:
    text = text.strip()
    if not text:
        return Intent("question")

    if m := _RESEARCH.match(text):
        return Intent("research", m.group("body").strip())

    if m := _IDEA.match(text):
        # An idea stated as a thought is captured, not researched — researching something Brad was
        # only thinking out loud about spends money he didn't ask to spend. He can say "research it"
        # next, and often does.
        return Intent("capture", m.group("body").strip(), "ideas")

    if m := _CAPTURE.match(text):
        body = m.group("body").strip().strip('"').strip("'")
        target = (m.group("target") or "").lower()
        # "add to my list" names a destination and no item. The body group happily swallows the
        # destination phrase, so an emptiness check alone doesn't catch it — recognize the phrase.
        if re.fullmatch(r"(?:to|on|in)\s+(?:my\s+|the\s+)?(?:to-?do|list|ideas?|backlog)(?:\s*list)?",
                        body, re.I):
            return Intent("question")
        if len(body) < 3:
            return Intent("question")
        dest = "ideas" if "idea" in target else "todo"
        return Intent("capture", body, dest)

    return Intent("question")


def capture(intent: Intent, memory: Path = MEMORY) -> str:
    """Append to the todo or idea log. Returns what to say back in Slack.

    These files live in the memory corpus on purpose: they inherit the 02:30 Reserve backup and its
    iDrive off-site leg, and Moses already reads the whole corpus per question — so the next "what's
    on my list?" sees it with no extra wiring and no second store to keep in sync.
    """
    # THERE IS NO GENERAL TO-DO LIST ANY MORE. Brad retired it on 2026-08-28: a flat file nothing
    # cross-checked accumulated finished work and was read back to him as outstanding. Tasks hang off
    # a project now. This path stays reachable and REFUSES with somewhere to go, rather than being
    # deleted — "Moses, add X to my list" is a thing he says, and silence would look like a bug.
    if intent.target != "ideas":
        return ("There's no general to-do list any more — tasks live on a project. Tell me which one "
                "(\"add it to Viatica\") and I'll file it there, or say it as an idea and I'll "
                "capture it for later.")

    # ONE STORE, AND IT IS THE REGISTRY. This wrote to memory/ideas.md, a flat file beside the
    # Projects page rather than in it — and it died exactly the way the to-do list above died: two
    # entries, untouched for a month, while everything real happened on the board. Brad, 2026-09-09:
    # "kill ideas.md and roll ideas into the Projects page."
    #
    # An idea captured in Slack with no project named goes to the registry INBOX and shows on the
    # board as unfiled. Asking "which project?" before writing it down is how a thought said in
    # passing gets lost, which is the whole thing capture exists to prevent.
    try:
        import sys
        if MCP_DIR not in sys.path:
            sys.path.insert(0, MCP_DIR)
        import projects
        return projects.add_inbox_idea(intent.body)
    except Exception as e:                                          # noqa: BLE001
        # Never swallow the thought silently — say it did not land, so it can be said again.
        return f"I could not write that down ({type(e).__name__}: {e}). Say it again in a moment?"


def file_learning(body: str, source: str = "Atlas", link: str = "",
                  memory: Path = MEMORY) -> tuple[bool, int]:
    """Append something Moses learned to the look-into-further list. Returns (added, open_count).

    WHY THIS EXISTS (Brad, 2026-08-13): *"The ACTUAL point for Atlas and Moses to compare notes is
    that Atlas is now a resource to bounce things off of. There's a lot of good and bad lessons to
    share. And when Moses DOES learn something valuable, I need to know about it."*

    The entertainment was the surface. **The channel is a peer-review channel** — two agents built by
    different people against different problems, comparing notes — and the output that matters is the
    small number of things worth Brad chasing down. Without somewhere to put those they scroll away,
    which is the same failure the deferred-decisions file exists to prevent.

    ATTRIBUTED ON PURPOSE. "Atlas said X" carries different weight than "Moses thinks X": one is
    outside evidence, the other is Brad's own agent agreeing with itself. Provenance is the point, so
    the source is recorded, and the Slack permalink goes with it because the surrounding conversation
    is usually where the value actually is.

    Lives in the memory corpus like ideas.md and todo.md: it inherits the 02:30 backup and its iDrive
    off-site leg, and Moses reads the corpus back, so nothing else needs wiring.
    """
    body = " ".join((body or "").split())
    if not body:
        return False, 0
    path = memory / "look-into-further.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "# Look into further\n\n"
            "Things Moses picked up from another agent that looked worth Brad's time — filed as they\n"
            "happened, with who said it and a link back to the conversation.\n"
            "`- [ ]` is open, `- [x]` is done or dismissed.\n\n",
            encoding="utf-8",
        )
    existing = path.read_text(encoding="utf-8")
    if body.lower() in existing.lower():
        return False, _open_count(existing)

    tail = f" <{link}|context>" if link else ""
    line = f"- [ ] {body}  _(from {source}, {date.today().isoformat()})_{tail}\n"
    updated = existing.rstrip("\n") + "\n" + line
    path.write_text(updated, encoding="utf-8")
    return True, _open_count(updated)


def _open_count(text: str) -> int:
    """Count open items by matching line STARTS.

    A substring count over the whole file also counts the `- [ ]` that appears inside the header's
    own explanation of the format, which silently inflated every total by one.
    """
    return sum(1 for ln in text.splitlines() if ln.lstrip().startswith("- [ ]"))


# ── Routing deliberately lives in the model, not here ────────────────────────
# There was a keyword-overlap `route()` in this file. It was deleted rather than tuned, because it
# was measurably worse than what already exists: it sent "stripe revenue and fees this month" to
# Moses (matching on the stopword "this") and reported a support ticket as unowned, while Moses's
# own Slack path already carries the full roster in its prompt and is instructed to answer ownership
# questions from it. Keyword overlap cannot tell a distinctive term from a common one; the model
# reading the same roster gets it right without a threshold to tune.
#
# So a routing question is just a question, and takes the existing cheap cached path. The lesson is
# worth keeping: don't rebuild in regex what the model in front of you already does better.

