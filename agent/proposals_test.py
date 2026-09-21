#!/usr/bin/env python3
"""Pin the proposal gate. Run: python3 proposals_test.py

Moses proposes; Brad confirms; deterministic code files. The tests that matter are the ones keeping
the gate MECHANICAL — the model must never be able to file a task, and ordinary conversation must
never look like a confirmation.

The confirmation vocabulary is not invented. It is copied from watching Atlas's equivalent gate fail
in #the_4_horsemen on 2026-08-14: Jon typed "affirm" three times at a matcher that wanted "confirm",
then asked for multi-confirm because he was doing it verbally.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["MOSES_STATE"] = tempfile.mkdtemp(prefix="moses-prop-")

import dispatch     # noqa: E402
import proposals    # noqa: E402

results = []


def check(label, cond):
    results.append((label, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + label)

# ISOLATED ON THE REGISTRY, because that is where proposals now live. These used to unlink a
# `proposals.json`; there is no longer one. Pointing the registry at a temp file exercises the real
# store rather than a stand-in, and guarantees a test can never touch Brad's actual projects.
import json as _json, tempfile as _tf, pathlib as _pl, sys as _sys
_sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "mcp"))
import projects as _registry_mod

_TESTREG = _pl.Path(_tf.mkdtemp()) / "projects.json"


def _fresh_registry():
    """A registry holding just the two projects the tests propose against."""
    _TESTREG.write_text(_json.dumps({"projects": [
        {"id": "viatica", "name": "Viatica", "rank": 1, "status": "active",
         "stage": "building", "milestones": [{"title": "already committed", "done": False}],
         "ideas": []},
        {"id": "moses", "name": "Moses", "rank": 2, "status": "active",
         "stage": "building", "milestones": [], "ideas": []},
    ]}), encoding="utf-8")
    _registry_mod.REGISTRY = _TESTREG


_fresh_registry()


def reset(*tasks):
    _fresh_registry()
    return [proposals.add(t, "C_CHAT", project="viatica") for t in tasks]


# ── Holding a proposal ──────────────────────────────────────────────────────
reset()
it = proposals.add("Make probes assert their coverage", "C_CHAT", project="viatica")
check("a proposal is held with a short id", it and len(it["id"]) == 8)
check("it is pending, not filed", len(proposals.pending()) == 1)
same = proposals.add("make probes assert THEIR coverage", "C_CHAT", project="viatica")
check("the same task is not queued twice", same["id"] == it["id"] and len(proposals.pending()) == 1)
check("an empty proposal is refused", proposals.add("   ", "C_CHAT", project="viatica") == {})

# ── JON'S VOCABULARY. Every one of these was a real miss or a real ask. ─────
for word in ["confirm", "affirm", "approve", "accept", "yes", "yep", "yeah", "ok", "okay",
             "do it", "file it", "ship it", "make it so", "agreed", "go", "sounds good"]:
    reset("one task")
    action, items, _ = proposals.resolve(word)
    check(f"VOCAB {word!r} confirms when one is pending", action == "confirm" and len(items) == 1)

reset("one task")
check("VOCAB trailing punctuation is fine", proposals.resolve("confirm!")[0] == "confirm")
check("VOCAB case does not matter", proposals.resolve("CONFIRM")[0] == "confirm")

# ── Ordinary conversation must never file anything ─────────────────────────
reset("one task")
for text in ["yes, that's a good point", "ok so what about the other thing",
             "I'll confirm that with Jon later", "go ahead and look at the logs",
             "affirm 32be753c is not this id", "do it the other way instead",
             "agreed, but let's wait", ""]:
    action, _, _ = proposals.resolve(text)
    check(f"SAFE {text[:38]!r} does NOT file", action == "none")

# Nothing pending means nothing can ever match — the cheap common case.
_fresh_registry()
check("SAFE with nothing pending, 'confirm' is inert", proposals.resolve("confirm")[0] == "none")

# ── Multiple pending: Jon's exact complaint ────────────────────────────────
a, b = reset("first task", "second task")
action, items, _ = proposals.resolve("confirm")
check("AMBIGUOUS a bare yes with two pending does not guess", action == "ambiguous")
action, items, _ = proposals.resolve("confirm all")
check("MULTI 'confirm all' takes both", action == "confirm" and len(items) == 2)
reset("first task", "second task")
check("MULTI 'all confirmed' also works", proposals.resolve("all confirmed")[0] == "confirm")
# Re-capture the ids: reset() regenerates them, so the earlier `a` is stale by now. (Caught by this
# very test failing — the fixture was describing a state that no longer existed.)
a, b = reset("first task", "second task")
action, items, _ = proposals.resolve(f"confirm {a['id']}")
check("BY-ID naming one picks exactly that one",
      action == "confirm" and len(items) == 1 and items[0]["task"] == "first task")
action, items, _ = proposals.resolve("confirm deadbeef")
check("BY-ID an unknown id files nothing", action == "none")

# ── Dismissal ──────────────────────────────────────────────────────────────
reset("a task nobody wants")
action, items, _ = proposals.resolve("no")
check("DISMISS 'no' drops it", action == "dismiss" and len(items) == 1)
reset("a task nobody wants")
check("DISMISS 'forget it' drops it", proposals.resolve("forget it")[0] == "dismiss")

# ── Filing goes through the DETERMINISTIC path ─────────────────────────────
import pathlib  # noqa: E402
# Passed explicitly: capture() binds its default at import, so setting dispatch.MEMORY here would
# do nothing and this test would have been writing to Brad's real to-do list.
# CHANGED 2026-08-28: a confirmed task lands on its PROJECT, not on a flat list. Brad retired
# todo.md after it accumulated four finished items and was read back to him as outstanding.
mem = _TESTREG.parent
items = reset("Make probes assert their coverage", "Amend the peer-chat charter")
filed, problems = proposals.file_tasks(items, dispatch, memory=mem)
check("FILE both tasks are written", len(filed) == 2)
check("FILE and a clean run reports no problems", problems == [])
_reg = json.loads((mem / "projects.json").read_text())["projects"][0]
_ideas = [i["title"] for i in _reg.get("ideas") or []]
check("FILE they land on the project verbatim",
      "Make probes assert their coverage" in _ideas and "Amend the peer-chat charter" in _ideas)
# Confirming means "worth doing", not "committed to a release" — otherwise unplanned work lands in
# the ratio the dashboard reports and 1/1 stops meaning anything.
check("FILE they arrive as ideas, not as milestones", len(_reg["milestones"]) == 1)
check("FILE pending is emptied once filed", proposals.pending() == [])

# A PROJECT-LESS PROPOSAL IS NEVER STORED. It used to be parked in a pending queue until somebody
# named a project — and that queue was the bug: two of them existed under two MOSES_STATE values and
# one went unread for 17 days. There is nowhere for a homeless proposal to live that is not a second
# store, so it is refused at capture and the caller asks which project.
_fresh_registry()
homeless = proposals.add("A task with no home", "C_CHAT")
check("FILE a proposal with no project is refused at capture", homeless.get("unfiled") is True)
check("FILE and nothing is stored anywhere", proposals.pending() == [])
check("FILE but the text survives so the channel can still show it",
      homeless.get("task") == "A task with no home")
# Handed one anyway, filing still refuses it and SAYS SO rather than returning a silent empty list.
_, problems = proposals.file_tasks([homeless], dispatch, memory=mem)
check("FILE and Brad is TOLD why, not left with silence",
      len(problems) == 1 and "no project" in problems[0])

# An unreachable registry must also speak up rather than look like success.
_saved_dir = proposals.MCP_DIR
import sys as _sys
_saved_path = list(_sys.path)
# Repointing MCP_DIR alone did NOT reproduce it: the real mcp directory was still on sys.path from
# the successful call above, so the import kept working and the test failed — correctly. Making a
# thing unreachable means removing every route to it, not just the one you were thinking of.
proposals.MCP_DIR = "/nonexistent-mcp-dir"
_sys.path[:] = [d for d in _sys.path if "moses/mcp" not in d]
_stashed = _sys.modules.pop("projects", None)
try:
    homeless = proposals.add("A task proposed while the registry is missing", "C_CHAT",
                             project="viatica")
    check("FILE an unreachable registry fails at CAPTURE, not silently later",
          homeless.get("unfiled") is True and "error" in homeless)
    check("FILE and the wording survives so the channel can still show it",
          "registry is missing" in homeless.get("task", ""))
    f2, p2 = proposals.file_tasks([homeless], dispatch, memory=mem)
    check("FILE filing it anyway is reported, not swallowed",
          f2 == [] and len(p2) == 1)
finally:
    proposals.MCP_DIR = _saved_dir
    _sys.path[:] = _saved_path
    if _stashed is not None:
        _sys.modules["projects"] = _stashed
    _fresh_registry()
_fresh_registry()

# ── Reactions ──────────────────────────────────────────────────────────────
check("REACT a checkmark counts", "white_check_mark" in proposals.CONFIRM_REACTIONS)
check("REACT a thumbs-up counts", "+1" in proposals.CONFIRM_REACTIONS)
check("REACT an unrelated emoji does not", "popcorn" not in proposals.CONFIRM_REACTIONS)

it = reset("a task to confirm by reaction")[0]
proposals.attach_message(it["id"], "1234.5678")
check("REACT a proposal can be found by the message that carried it",
      (proposals.by_message("1234.5678") or {}).get("id") == it["id"])
check("REACT an unrelated message finds nothing", proposals.by_message("9999.0000") is None)

# ── DEDUPE: the 2026-08-15 incident, using the messages that actually caused it ─────────────────
#
# Three proposals landed in one second, all asking for the same go-live checklist in different
# words, while GO-LIVE 1–10 already covered the ground in more detail. These are the real strings
# out of proposals.json, not paraphrases — a fixture that invents its own shape has already been
# wrong once in this project and cost a day.
DUP_A = ("Write a Stripe live-mode go-live checklist enumerating every dashboard setting that must "
         "be re-created in live mode (they do not carry over from sandbox), plus the three "
         "unexercised referral API calls to verify during the same run.")
DUP_B = ("Write a Viatica go-live checklist covering every Stripe dashboard setting that must be "
         "reconfigured in live mode (none carry over from sandbox) plus the DNS cutover steps from "
         "Hostinger parking to Railway, and verify the three unexercised referral Stripe calls "
         "against the live account during that run.")
DUP_C = ("Write a go-live checklist covering the Viatica cutover — every Stripe dashboard setting "
         "that must be re-created in live mode, the viatica.travel DNS switch from Hostinger to "
         "Railway, and verification of the three unexercised referral Stripe calls against the "
         "live account.")
UNRELATED = ("Extend the probe coverage assertion so each check emits its identity and scope "
             "alongside coverage, not coverage alone — so a check that runs against the wrong "
             "target is distinguishable from one that ran correctly.")

reset(DUP_A)
b = proposals.add(DUP_B, "C_CHAT", project="viatica")
check("DEDUPE the second wording is caught as a duplicate", b.get("duplicate") is True)
c = proposals.add(DUP_C, "C_CHAT", project="viatica")
check("DEDUPE the third wording is caught too", c.get("duplicate") is True)
check("DEDUPE only the first one is pending", len(proposals.pending()) == 1)

# THE INVERSE, which is the more dangerous failure: a dedupe that eats real work is a proposal Brad
# never sees. Unrelated work must still get through, next to a near-duplicate of something else.
u = proposals.add(UNRELATED, "C_CHAT", project="viatica")
check("DEDUPE unrelated work is NOT swallowed", not u.get("duplicate") and len(proposals.pending()) == 2)

# The threshold has to actually separate them, not merely order them — a margin of nothing is a
# coin toss on the next differently-worded pair.
worst_dup = min(proposals.similarity(DUP_A, DUP_B), proposals.similarity(DUP_A, DUP_C),
                proposals.similarity(DUP_B, DUP_C))
best_false = max(proposals.similarity(UNRELATED, d) for d in (DUP_A, DUP_B, DUP_C))
check(f"DEDUPE duplicates ({worst_dup:.2f}) clear the bar ({proposals.DUPLICATE_AT})",
      worst_dup >= proposals.DUPLICATE_AT)
check(f"DEDUPE unrelated ({best_false:.2f}) is nowhere near it", best_false < proposals.DUPLICATE_AT)
check("DEDUPE there is real daylight between them", worst_dup - best_false > 0.25)

# Containment, not similarity. Jaccard scored a short proposal against the long to-do entry that
# already covered it at 0.10 — dividing by the union punishes a short text for being short. This is
# the property that made the metric wrong, so it is pinned.
short, long = "Rotate the Stripe webhook signing secret", \
              ("Rotate the Stripe webhook signing secret " + "and then " * 40 + "verify deliveries")
check("METRIC a short task contained in a long one scores high", proposals.similarity(short, long) > 0.9)

# ── RELATED TO-DOS: surfaced, never suppressed ─────────────────────────────────────────────────
# Overlap with an open to-do measured 0.36–0.43 against 0.21 for unrelated work — too tight to
# refuse on. So it is shown WITH the proposal and Brad decides. Refusing here would silently drop
# work; that is the failure mode this whole gate exists to avoid.
todo_dir = tempfile.mkdtemp(prefix="moses-todo-")
# Tracked work is read from the PROJECT REGISTRY now. A done milestone must not count — that is
# exactly what went wrong with the flat file: finished work read back as outstanding.
open(os.path.join(todo_dir, "projects.json"), "w").write(json.dumps({"projects": [
    {"id": "viatica", "name": "Viatica", "milestones": [
        {"title": DUP_C, "done": False},
        {"title": "something already done", "done": True},
    ]},
]}))

_fresh_registry()
it = proposals.add(DUP_A, "C_CHAT", memory=todo_dir, project="viatica")
check("RELATED a proposal covered by an open to-do is still HELD, not dropped",
      it.get("id") and not it.get("duplicate"))
check("RELATED it carries the to-do it resembles", it.get("related", "").startswith("Write a go-live"))

_fresh_registry()
clean = proposals.add(UNRELATED, "C_CHAT", memory=todo_dir, project="viatica")
check("RELATED unrelated work gets no misleading pointer", "related" not in clean)

check("RELATED only OPEN items are considered", len(proposals.open_todos(todo_dir)) == 1)
check("RELATED a missing to-do file degrades to empty", proposals.open_todos("/nonexistent") == [])


# ── EXPLICIT IDS: the instruction Brad typed that did nothing ──────────────────────────────────
a, b, c = reset("first task", "second task", "third task")
ids = (a["id"], b["id"], c["id"])

got = proposals.parse_actions(f"confirm `{ids[0]}`, dismiss `{ids[1]}` and dismiss `{ids[2]}`")
check("IDS Brad's exact 2026-08-15 line is understood",
      got and got["confirm"] == [ids[0]] and sorted(got["dismiss"]) == sorted(ids[1:]))

check("IDS backticks are optional",
      (proposals.parse_actions(f"confirm {ids[0]} {ids[1]}") or {}).get("confirm") == [ids[0], ids[1]])
check("IDS one verb can carry several ids",
      len((proposals.parse_actions(f"dismiss {ids[0]}, {ids[1]}, {ids[2]}") or {})["dismiss"]) == 3)
check("IDS case is ignored",
      (proposals.parse_actions(f"CONFIRM {ids[0].upper()}") or {}).get("confirm") == [ids[0]])
check("IDS 'drop' and 'delete' mean dismiss",
      (proposals.parse_actions(f"drop {ids[0]}") or {})["dismiss"] == [ids[0]]
      and (proposals.parse_actions(f"delete {ids[1]}") or {})["dismiss"] == [ids[1]])

# An id that is not pending must be REPORTED, never silently skipped. Silence is the whole bug:
# Brad's instruction looked identical whether it worked or not.
got = proposals.parse_actions(f"confirm {ids[0]} deadbeef")
check("IDS an unknown id is reported, not swallowed",
      got and got["confirm"] == [ids[0]] and got["unknown"] == ["deadbeef"])

check("IDS a contradiction is refused rather than guessed",
      proposals.parse_actions(f"confirm {ids[0]} dismiss {ids[0]}") is None)
check("IDS an id with no verb is not an instruction", proposals.parse_actions(f"{ids[0]}") is None)
check("IDS a verb with no id falls through to the bare matcher",
      proposals.parse_actions("confirm") is None)

# ── THE SAFETY PROPERTY: prose must never reach this path ──────────────────────────────────────
# The original whole-message rule existed because "yes, that's a good point" must not file a task.
# Loosening it to accept ids must not loosen it to accept sentences — so one ordinary word anywhere
# makes it conversation again. These are the cases that would be a real incident.
for prose in [
    f"I'll confirm {ids[0]} with Jon before we file it",
    f"we should probably drop {ids[0]} because the API changed",
    f"did you confirm {ids[0]} yesterday?",
    f"confirm {ids[0]} looks wrong to me",
    "confirm that the backup finished",
    f"the deploy hash is {ids[0]} by the way",
    "drop the old Pi service and confirm it stopped",
    f"remove {ids[0]} from the roster file",
]:
    check(f"SAFE prose is not an instruction: {prose[:44]!r}", proposals.parse_actions(prose) is None)

# Still no path for anyone but Brad, and no path from a bare word to a filing — those gates are the
# listener's and resolve()'s, and they are unchanged. Pinned here so a future edit cannot quietly
# route around them by way of this parser.
check("SAFE the parser itself never writes anything",
      len(proposals.pending()) == 3)


# ── State handling ─────────────────────────────────────────────────────────
_TESTREG.write_text("not json at all")
check("STATE a corrupt file degrades to empty, it does not crash", proposals.pending() == [])

passed = sum(1 for _, ok in results if ok)
failed = len(results) - passed
print(f"\n  {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
