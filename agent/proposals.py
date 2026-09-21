"""proposals — Moses proposes a task; Brad confirms it; deterministic code files it.

Brad, 2026-08-14, after watching a real exchange produce two good tasks and lose both: Moses said
*"I can't commit them to the list from here (write access isn't granted in this channel), so run me
in Claude Code and I'll file them verbatim."* Honest, and a dead end — the thinking happened in the
channel and evaporated there.

THE MODEL NEVER GETS A WRITE TOOL. It ends a message with `PROPOSE: <task>`; that line is lifted out
before posting and held here. Nothing is written until Brad confirms, and the write itself is done by
`dispatch.capture`, the same deterministic path `"Moses, add X to my list"` already uses. **The gate
is mechanical: a human types a confirmation and code matches it.** The model does not get to decide
it heard a yes.

ONLY BRAD CONFIRMS (his decision, 2026-08-14). It is his list. Anyone may still mute Moses — Jon did
exactly that mid-session and it was the right call — but filing work is the owner's.

THE CONFIRMATION VOCABULARY IS COPIED FROM A REAL FAILURE, not invented. Watching Atlas's equivalent
gate in the same thread:
  * Jon typed **"affirm"** three times where the matcher wanted **"confirm"**, losing two round trips
    to vocabulary. So the verb set here is deliberately generous.
  * Jon then hit two pending items and asked for the obvious thing: *"we should be able to
    multi-affirm when multiple tasks are proposed as I may do this verbally."* So `confirm all` works.
  * With exactly one pending, a bare "yes" is enough — an id is only needed to disambiguate.

MATCHING IS WHOLE-MESSAGE, and that is the safeword's lesson applied again. "we should stand down the
old Pi service" must not mute him, and "yes, that's a good point" must not file a task. A confirmation
is a message whose entire content is the confirmation.
"""

from __future__ import annotations

import sys as _sys, pathlib as _pl  # noqa: E401
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent / "lib"))
import moses_env as _env  # noqa: E402 — whose home, where Moses lives, the operator's settings
import json
import os
import re
import time
import uuid
from pathlib import Path

# Where the project registry lives. Overridable so this is not a second hardcoded path to maintain
# when the tree moves — the last three deployment breaks were all a path that only existed in one of
# the two trees.
MCP_DIR = os.environ.get("MOSES_MCP_DIR", str(_env.ROOT / "mcp"))

STATE = Path(os.environ.get("MOSES_STATE", "/var/lib/moses"))

# Reactions that count as a yes, on the proposal message itself.
# ── Whose "yes" was that? ────────────────────────────────────────────────────────
#
# The bare-affirmation matcher below deliberately skips the addressing check, and the reasoning was
# sound: "yes" and "confirm all" are answers, not commands, and nobody re-says a bot's name to agree
# with it. That held while Moses was the only thing talking to Brad in the channel.
#
# On 2026-08-22 Jon opened Atlas up to Brad directly. Atlas offered to take on Viatica work, Brad
# replied "Sounds good!" — to Atlas — and Moses filed his own oldest pending proposal, for work that
# had already shipped the day before. The guard was not wrong; ITS PREMISE EXPIRED. In a channel
# with two agents, a bare affirmation is ambiguous by default.
#
# So a bare "yes" is only taken as MINE when nothing else has spoken to Brad in between. If another
# machine has posted since the proposal, the affirmation is ambiguous and Moses asks instead of
# assuming — cheap, and the failure it prevents is filing work nobody asked for.
def confirmation_is_for_me(history: list[dict], proposal_ts: str, *,
                           is_self, is_machine) -> bool:
    """`history` is newest-first, as `recent()` returns it. `proposal_ts` is when Moses proposed.

    True when no OTHER machine has spoken since the proposal. Humans in between are fine — Brad and
    Jon talking does not make an agreement ambiguous; a second agent addressing Brad does.
    """
    if not proposal_ts:
        return True                      # nothing to be ambiguous against
    try:
        cutoff = float(proposal_ts)
    except (TypeError, ValueError):
        return True
    for m in history:
        try:
            ts = float(m.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ts <= cutoff:
            break                        # reached the proposal; nothing else intervened
        if is_machine(m) and not is_self(m):
            return False                 # another agent spoke to Brad after I proposed
    return True


CONFIRM_REACTIONS = {"white_check_mark", "heavy_check_mark", "ballot_box_with_check", "+1",
                     "thumbsup", "ok_hand", "heavy_plus_sign"}

_VERB = (r"(?:confirm|affirm|approve|accept|file|do it|file it|ship it|make it so|go|going|"
         r"yes|yep|yeah|yup|ok|okay|agreed|sounds good|please do)")
# Whole message only. A bare verb, optionally with an id or "all".
CONFIRM = re.compile(rf"^\s*{_VERB}(?:\s+(all|[0-9a-f]{{6,8}}))?\s*[.!]*\s*$", re.I)
# "confirm all" said the other way round, e.g. "all confirmed".
CONFIRM_ALL = re.compile(rf"^\s*(?:all\s+{_VERB}(?:ed)?|{_VERB}\s+all)\s*[.!]*\s*$", re.I)
DISMISS = re.compile(r"^\s*(?:no|nope|drop it|forget it|cancel|dismiss|skip it|not that one)"
                     r"(?:\s+([0-9a-f]{6,8}))?\s*[.!]*\s*$", re.I)


# ── Is this the same task, said differently? ────────────────────────────────
# Exact-match dedup let three proposals through in one second on 2026-08-15, all asking for the same
# go-live checklist in different words. A prompt instruction to "check the list first" is the weakest
# possible layer, so the check is mechanical: it holds whether the model looks or not.
#
# THE METRIC IS CONTAINMENT, NOT SIMILARITY, and that was measured rather than guessed. Jaccard
# (intersection over UNION) scored those three real duplicates at 0.38–0.59 and scored a proposal
# against the far longer to-do entry that already covered it at 0.10 — because dividing by the union
# punishes a short text for being short. Overlap (intersection over the SMALLER side) asks the
# question actually being asked: is this already contained in something we have?
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "at", "by", "with", "that", "this",
    "is", "are", "be", "it", "as", "from", "so", "every", "each", "must", "should", "plus", "during",
    "which", "not", "do", "does", "than", "then", "when", "all", "any", "into", "out", "up",
}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in _STOP and len(w) > 2}


def similarity(a: str, b: str) -> float:
    """Overlap coefficient: how much of the SMALLER text is contained in the larger.

    1.0 means one is entirely covered by the other; 0.0 means nothing in common.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


# TWO THRESHOLDS, because the measured data does not support one. Against the real 2026-08-15 case:
#
#   the three duplicate proposals, against each other     0.57  0.76  0.83
#   a duplicate against the to-do entry already covering it     0.36 – 0.43
#   an unrelated proposal against everything else               0.21
#
# So near-identical wording separates cleanly and can be REFUSED outright. Overlap with an existing
# to-do does not — 0.36 is too near 0.21 to be sure, and the cost of a wrong call there is a proposal
# Brad never sees, which is worse than one he dismisses in a word. That case is FLAGGED instead: the
# proposal still reaches him, carrying the entry it resembles, and he decides.
DUPLICATE_AT = float(os.environ.get("MOSES_PROPOSAL_DUP_AT", "0.55"))
RELATED_AT = float(os.environ.get("MOSES_PROPOSAL_RELATED_AT", "0.30"))


def open_todos(memory=None) -> list[str]:
    """Work already tracked, so a proposal can be checked against it.

    Reads the PROJECT REGISTRY. Until 2026-08-28 this read a flat todo.md, which nothing cross-checked
    and which had accumulated four completed items — so a proposal could be flagged as duplicating
    something finished weeks earlier. Brad retired that file: every task now hangs off a project, and
    this reads the same place the dashboard does. `memory` is still honored so tests can redirect it.
    """
    base = memory or os.environ.get("MOSES_MEMORY_DIR", str(_env.MEMORY))
    try:
        d = json.loads((Path(base) / "projects.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[str] = []
    for p in d.get("projects", []):
        out += [m.get("title", "") for m in (p.get("milestones") or []) if not m.get("done")]
        # ACCEPTED ideas only. A proposal is not tracked work yet — counting one would let a
        # pending suggestion be reported as something already on the books, and (worse) let a
        # proposal match itself as "related to existing work" the moment it was captured.
        out += [i.get("title", "") for i in (p.get("ideas") or [])
                if i.get("state") != "proposed"]
    return [t for t in out if t]


def duplicate_of(task: str) -> dict | None:
    """A PENDING proposal saying the same thing. Refusable — the wording is near-identical."""
    best, score = None, 0.0
    for it in _load():
        s = similarity(task, it["task"])
        if s >= DUPLICATE_AT and s > score:
            best, score = it, s
    return best


def related_todo(task: str, memory=None) -> tuple[str, float] | None:
    """An open to-do covering similar ground. NOT refusable — surfaced for Brad to judge."""
    best, score = None, 0.0
    for existing in open_todos(memory):
        s = similarity(task, existing)
        if s >= RELATED_AT and s > score:
            best, score = existing, s
    return (best, score) if best else None


def _registry():
    """The project registry — the single store. Imported lazily and by absolute path.

    THERE IS NO LONGER A proposals.json. It lived under MOSES_STATE, which resolves to
    /var/lib/moses for the root service and ~/.local/state/moses for anything run as brad, so TWO
    stores existed and one went unread for 17 days — holding, among other things, a Stripe go-live
    checklist filed five days before Viatica took real money. A proposal is an unconfirmed idea, so
    it now lives on its project like every other piece of work.
    """
    import sys
    if MCP_DIR not in sys.path:
        sys.path.insert(0, MCP_DIR)
    import projects as registry
    return registry


def _load() -> list[dict]:
    try:
        # NORMALIZED AT THE BOUNDARY. The registry calls the text `title`; every caller here and in
        # listener.py calls it `task`. Translating in one place beats renaming a field across two
        # modules and every test — and beats the alternative, which is both names half-working.
        return [dict(i, task=i.get("title", "")) for i in _registry().pending_proposals()]
    except Exception:
        # Reading must never throw into a Slack turn. An empty list here means "could not read",
        # and every caller that ACTS on emptiness says so rather than treating it as "nothing due".
        return []


def pending() -> list[dict]:
    return _load()


def add(task: str, channel: str = "", by: str = "Moses", memory=None, project: str = "") -> dict:
    """Hold a proposed task. Returns the stored item, including its short id."""
    task = " ".join((task or "").split())
    if not task:
        return {}
    items = _load()
    for it in items:                       # exact repeat of something already pending
        if it["task"].lower() == task.lower():
            return it
    dup = duplicate_of(task)
    if dup:
        return dict(dup, duplicate=True)    # already pending in near-identical words
    project = (project or "").strip()
    item = {"id": uuid.uuid4().hex[:8], "task": task, "by": by, "channel": channel,
            "project": project, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "msg_ts": ""}
    rel = related_todo(task, memory)
    if rel:
        item["related"] = rel[0]            # posted with the proposal; Brad decides, code does not
    if not project:
        # A PROPOSAL WITHOUT A PROJECT IS NOT STORED, because there is nowhere for it to live that
        # is not a second store — and a second store is the bug. The caller asks which project and
        # proposes again. Returned unsaved so the channel still shows it and nothing is lost mid-turn.
        return dict(item, unfiled=True)
    try:
        reg = _registry()
        if memory is not None:
            reg.REGISTRY = Path(memory) / "projects.json"
        reg.propose(project, task, id=item["id"], channel=channel, msg_ts="",
                    by=by, at=item["at"], related=item.get("related", ""))
    except Exception as e:
        return dict(item, unfiled=True, error=f"{type(e).__name__}: {e}")
    return item


def _mutate(fn) -> None:
    """Edit proposal entries in place inside the registry. One writer, one file."""
    try:
        reg = _registry()
        d = reg.load()
        changed = False
        for proj in d.get("projects", []):
            for entry in (proj.get("ideas") or []):
                if entry.get("state") == reg.PROPOSED and fn(proj, entry):
                    changed = True
        if changed:
            reg.save(d)
    except Exception:
        pass


def attach_message(pid: str, ts: str) -> None:
    """Remember which Slack message carried the proposal, so a reaction on it can confirm it."""
    def f(_proj, e):
        if e.get("id") == pid:
            e["msg_ts"] = ts
            return True
        return False
    _mutate(f)


def by_message(ts: str) -> dict | None:
    return next((it for it in _load() if it.get("msg_ts") and it["msg_ts"] == ts), None)


def resolve(text: str) -> tuple[str, list[dict], str]:
    """Interpret a message against what is pending.

    Returns (action, items, note) where action is 'confirm' | 'dismiss' | 'ambiguous' | 'none'.
    'none' means this was ordinary conversation — the common case, and it must stay cheap and silent.
    """
    items = _load()
    if not items:
        return "none", [], ""

    if CONFIRM_ALL.match(text or ""):
        return "confirm", items, ""

    m = CONFIRM.match(text or "")
    if m:
        arg = (m.group(1) or "").lower()
        if arg == "all":
            return "confirm", items, ""
        if arg:
            hit = [it for it in items if it["id"].startswith(arg)]
            return ("confirm", hit, "") if hit else ("none", [], f"no pending proposal {arg}")
        if len(items) == 1:
            return "confirm", items, ""
        # More than one pending and no id — say which, rather than guessing. Guessing here files
        # the wrong task, and a wrong task on Brad's list is worse than one more round trip.
        return "ambiguous", items, ""

    d = DISMISS.match(text or "")
    if d:
        arg = (d.group(1) or "").lower()
        if arg:
            hit = [it for it in items if it["id"].startswith(arg)]
            return ("dismiss", hit, "") if hit else ("none", [], "")
        if len(items) == 1:
            return "dismiss", items, ""
        return "ambiguous", items, ""

    return "none", [], ""


# ── An explicit instruction naming ids ──────────────────────────────────────
# Brad typed this on 2026-08-15 and NOTHING happened, on either agent:
#
#     confirm `0c1c831f`, dismiss `dddf1e0f` and dismiss `4f9c02df`
#
# Whole-message matching refused it, correctly by its own rules and uselessly by his. He got no
# reply either, so a reasonable instruction looked exactly like a working one. That is the lesson
# Atlas had already named a day earlier: if the bot's own message tells a human what to say, the
# matcher has to accept it — and every proposal Moses posts prints an id next to the task, so ids
# are the vocabulary he taught. He just never advertised them.
#
# THE SAFETY PROPERTY IS UNCHANGED, and it is worth being precise about why. The danger was never
# ids; it was "yes, that's a good point" filing a task. So this accepts a message only when it
# consists of NOTHING BUT verbs, ids and connective filler. One ordinary word anywhere and it is
# conversation again. Prose cannot reach this path, because prose contains prose.
_CONFIRM_VERB = re.compile(r"^(?:confirm|affirm|approve|accept|file|keep)(?:s|ed|ing)?$", re.I)
_DISMISS_VERB = re.compile(r"^(?:dismiss|drop|discard|cancel|delete|remove|forget|skip)"
                           r"(?:es|s|ed|ing)?$", re.I)
_ID = re.compile(r"^[0-9a-f]{6,8}$", re.I)
# Words that carry no instruction and may appear between actions. Anything NOT in this set, and not
# a verb or an id, means the message is prose and this parser must decline it.
_FILLER = {"and", "then", "also", "plus", "the", "one", "ones", "it", "them", "both", "please",
           "task", "tasks", "proposal", "proposals", "id", "ids", "but", "not"}


def parse_actions(text: str) -> dict | None:
    """Read an explicit "confirm X, dismiss Y" instruction. None if this is not one.

    Returns {"confirm": [id...], "dismiss": [id...], "unknown": [id...]} with ids resolved against
    what is actually pending, so the caller can say which ones matched nothing instead of silently
    doing a partial job.
    """
    raw = (text or "").replace("`", " ").replace("*", " ")
    # Split on whitespace and the punctuation that separates clauses. Keeps ids and words intact.
    tokens = [t for t in re.split(r"[\s,;:.!?()\[\]]+", raw) if t]
    if not tokens:
        return None

    out = {"confirm": [], "dismiss": [], "unknown": []}
    verb = None
    saw_id = False
    known = {it["id"] for it in _load()}
    for tok in tokens:
        if _CONFIRM_VERB.match(tok):
            verb = "confirm"
            continue
        if _DISMISS_VERB.match(tok):
            verb = "dismiss"
            continue
        if _ID.match(tok):
            # A hex-shaped token before any verb is not an instruction — it is someone quoting an
            # id mid-sentence, and guessing an action for it would be inventing intent.
            if verb is None:
                return None
            saw_id = True
            tok = tok.lower()
            if tok in known:
                if tok not in out[verb]:
                    out[verb].append(tok)
            elif tok not in out["unknown"]:
                out["unknown"].append(tok)
            continue
        if tok.lower() in _FILLER:
            continue
        return None                      # a real word: this is conversation, not an instruction

    if not saw_id:
        return None                      # bare verbs are the existing resolve()'s job, not this one
    # An id can't be both. If it appears under each, the later instruction is the one that meant it
    # — but that is ambiguous enough to be worth refusing outright rather than picking.
    if set(out["confirm"]) & set(out["dismiss"]):
        return None
    return out


def drop(ids: list[str]) -> None:
    """Dismiss. The entry is REMOVED from its project — a dismissed proposal is not filed work."""
    wanted = set(ids)
    try:
        reg = _registry()
        d = reg.load()
        changed = False
        for proj in d.get("projects", []):
            keep = [e for e in (proj.get("ideas") or [])
                    if not (e.get("state") == reg.PROPOSED and e.get("id") in wanted)]
            if len(keep) != len(proj.get("ideas") or []):
                proj["ideas"] = keep
                changed = True
        if changed:
            reg.save(d)
    except Exception:
        pass


def file_tasks(items: list[dict], dispatch_mod, memory=None) -> list[str]:
    """File confirmed tasks against their project, and remove them from pending.

    THE DESTINATION IS A PROJECT, not a list. Until 2026-08-28 this wrote to a flat todo.md that
    nothing cross-checked; it accumulated finished work and was read back as outstanding. Brad:
    "no more to-do list stored in some arbitrary location... if it's a to-do list, it needs to be
    tied to a Project."

    A confirmed task becomes an IDEA on that project rather than a milestone. Confirming means "worth
    doing", not "committed to this release" — promotion to a milestone is a separate, deliberate act,
    and collapsing the two would put unplanned work into the ratio the dashboard reports.

    A task with no project is NOT filed and NOT dropped. It stays pending and says why, because
    losing something Brad just approved is worse than making him name the project.

    Returns `(filed, problems)`. BOTH halves are reported by the caller. Returning only the filed
    list meant "nothing was filed" and "everything worked" were the same empty list, and the reaction
    path then said nothing at all — the precise shape of the original failure, where Brad's
    instruction looked identical whether it worked or not.

    `dispatch_mod` is still accepted so every caller and test keeps its signature; it is unused now
    that nothing writes to the corpus files.
    """
    import sys
    # The registry lives in the mcp tree, and this module is deployed to /opt without it. An absolute
    # path rather than a relative one for exactly that reason: /opt/moses/proposals.py has no sibling
    # mcp directory, and the service can read here (ProtectHome=read-only) and write the registry
    # (ReadWritePaths=/home/brad/.claude). Both verified against the running unit, not assumed.
    if MCP_DIR not in sys.path:
        sys.path.insert(0, MCP_DIR)
    try:
        import projects as registry
    except Exception as e:
        # NEVER silently. A failed import used to return [], which the caller could not tell apart
        # from "nothing needed filing" — so a confirmed task vanished with no message. The items stay
        # pending either way; what changes is that Brad is told.
        return [], [f"could not reach the project registry ({type(e).__name__}) — still pending"]

    if memory is not None:
        registry.REGISTRY = Path(memory) / "projects.json"

    filed, problems = [], []
    for it in items:
        project = (it.get("project") or "").strip()
        if not project:
            # Cannot happen for anything stored — add() refuses to store a project-less proposal —
            # but a caller can still hand one in, and losing something Brad just approved is worse
            # than making him name the project.
            problems.append(f"_{it['task'][:60]}_ has no project — tell me which one and I'll "
                            f"propose it again.")
            continue
        try:
            # CONFIRMING IS A STATE FLIP, not a copy. It used to read one store and write another,
            # which is how a confirmed task could be dropped from pending and never arrive. There is
            # one record now; it changes state in place, so it cannot be in neither place.
            registry.accept_proposal(project, it["number"]) if it.get("number") else _accept_by_id(
                registry, project, it["id"])
            filed.append(f"{it['task']}  → {project}")
        except Exception as e:
            problems.append(f"_{it['task'][:60]}_ could not be confirmed against `{project}` "
                            f"({type(e).__name__}: {e}) — still awaiting you")
    return filed, problems


def _accept_by_id(registry, project: str, pid: str) -> None:
    """Accept by short id rather than position — positions shift when something else is dismissed."""
    d = registry.load()
    p = registry.find(d, project)
    for n, e in enumerate(p.get("ideas") or [], 1):
        if e.get("id") == pid and e.get("state") == registry.PROPOSED:
            return registry.accept_proposal(project, n)
    raise registry.ProjectError(f"no pending proposal {pid} on {project}")


def summary() -> str:
    items = _load()
    if not items:
        return "nothing pending"
    return " · ".join(f"`{it['id']}` {it['task'][:60]}" for it in items)
