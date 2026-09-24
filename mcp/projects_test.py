#!/usr/bin/env python3
"""Ideas are candidates, and must never be counted as commitments.

    python3 mcp/projects_test.py

WHY THIS EXISTS: Viatica reached 12/12 milestones and stage `shipped`, so everything anyone thought
of next had nowhere to live but a plan file that the next plan overwrote. Ideas fix that — but the
moment they are counted alongside milestones, "12/12" stops meaning "we finished what we set out to
do", and that ratio is the one number the dashboard shows. The separation IS the feature, so it is
the thing under test.

Runs against a temp registry. This module's real one feeds the standup, the MCP tools and the status
report; a test that wrote to it would be a test that breaks all three.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import projects  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="projects-test-")) / "projects.json"
projects.REGISTRY = _TMP

fails = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  — {detail}"))
    if not cond:
        fails.append(label)


def seed(**over):
    p = {"id": "demo", "name": "Demo", "rank": 1, "status": "active", "stage": "shipped",
         "milestones": [{"title": "a", "done": True}, {"title": "b", "done": True}]}
    p.update(over)
    _TMP.write_text(json.dumps({"projects": [p]}), encoding="utf-8")


def only():
    return json.loads(_TMP.read_text(encoding="utf-8"))["projects"][0]


print("an idea is not a milestone")
seed()
before = projects.brief()
projects.add_idea("demo", "Donations and a supporters page", "display only, never processed")
p = only()
check("the milestone list is untouched", len(p["milestones"]) == 2, str(p["milestones"]))
check("the ratio still reads 2/2", "2/2" in projects.brief(), projects.brief())
check("and the idea is counted separately", "2/2+1" in projects.brief(), projects.brief())
check("the note survives", p["ideas"][0].get("note") == "display only, never processed")
check("it is dated", bool(p["ideas"][0].get("addedAt")))

print("\nan idea does not change where the project is in its life")
check("a shipped project stays shipped", only()["stage"] == "shipped", only()["stage"])
seed(stage="idea", milestones=[])
projects.add_idea("demo", "something")
check("and an idea-stage project is not promoted by having an idea",
      only()["stage"] == "idea", only()["stage"])

print("\npromotion is the moment it becomes a commitment")
seed()
projects.add_idea("demo", "Group trips", "the Jamaica trip is the acceptance test")
projects.promote_idea("demo", 1)
p = only()
check("it is now a milestone", [m["title"] for m in p["milestones"]][-1] == "Group trips")
check("carrying its reasoning", p["milestones"][-1].get("note") == "the Jamaica trip is the acceptance test")
check("and it has left the candidate list", not p.get("ideas"))
check("the ratio now counts it", "2/3" in projects.brief(), projects.brief())

print("\nremoving one drops only that one")
seed()
for t in ("one", "two", "three"):
    projects.add_idea("demo", t)
projects.remove_idea("demo", 2)
check("the right idea went", [i["title"] for i in only()["ideas"]] == ["one", "three"],
      str([i["title"] for i in only()["ideas"]]))

print("\na project with no ideas is unchanged")
seed()
check("brief renders exactly as it did before any of this", projects.brief() == before,
      f"{projects.brief()!r} != {before!r}")
check("and the dashboard says nothing about ideas", "ideas (" not in projects.dashboard())

print("\nbad input is refused, not guessed at")
seed()
for bad, why in ((lambda: projects.add_idea("demo", "   "), "a blank idea"),
                 (lambda: projects.promote_idea("demo", 9), "promoting one that isn't there"),
                 (lambda: projects.remove_idea("demo", 0), "removing index zero")):
    try:
        bad()
        check(f"{why} is refused", False, "it was accepted")
    except projects.ProjectError:
        check(f"{why} is refused", True)

print("\nthe retired to-do list cannot come back")
# A SOURCE-LEVEL guard, deliberately. answer_test already checks at runtime that no todo.md appears,
# but that only covers the paths those fixtures walk. What actually retired the list is that no code
# anywhere still names it as somewhere to WRITE — and the dead branch in _capture proved that a
# path can survive its last caller and stay perfectly capable of rebuilding the file. Mentioning
# todo.md in a comment or in the retired file's own name is fine; opening it for writing is not.
import ast as _ast, pathlib as _pl
_root = _pl.Path(__file__).resolve().parent.parent


def _live_strings(path):
    """Every string literal that is real code, with docstrings and bare string statements dropped.

    Grepping the raw text was tried first and reported four false hits — all of them PROSE, in the
    docstrings that explain why the list was retired. A guard that fires on the comment describing
    the fix is one that gets deleted, so this parses. A string that is actually used, including
    `MEMORY / "todo.md"`, is exactly what survives.
    """
    tree = _ast.parse(path.read_text(encoding="utf-8"))
    skip = set()
    for node in _ast.walk(tree):
        # Docstrings, plus any statement that is nothing but a string — the comment convention.
        if isinstance(node, _ast.Expr) and isinstance(node.value, _ast.Constant) \
                and isinstance(node.value.value, str):
            skip.add(id(node.value))
    return [(n.lineno, n.value) for n in _ast.walk(tree)
            if isinstance(n, _ast.Constant) and isinstance(n.value, str) and id(n) not in skip]


_offenders = []
# Production modules only. A test naming todo.md to assert it is GONE is the retirement working,
# not a leak — answer_test.py does exactly that on purpose.
# The one-off MIGRATION is exempt, by name. Its entire job is reading the retired stores and moving
# what it finds onto projects — a guard that fires on the tool that performed the retirement is a
# guard that gets switched off. Exempted explicitly rather than by loosening the pattern, so any
# OTHER module naming the old store still fails.
_MIGRATIONS = {"migrate-proposals-to-projects.py"}
_modules = [f for f in sorted(list((_root / "mcp").glob("*.py")) + list((_root / "agent").glob("*.py")))
            if not f.name.endswith("_test.py") and f.name not in _MIGRATIONS]
check("the scan is looking at real modules", len(_modules) > 10, f"only {len(_modules)} found")
for _f in _modules:
    for _line, _val in _live_strings(_f):
        if "todo.md" in _val and "retired" not in _val:
            _offenders.append(f"{_f.name}:{_line}")
check("no module still names the flat to-do file in code", not _offenders, ", ".join(_offenders))

# And the guard is watching something — prove it can see a live reference, not just absent ones.
_probe = _pl.Path(__file__).parent / ".todo_probe.py"
_probe.write_text('P = "todo.md"\n"""a docstring naming todo.md must NOT trip it"""\n', encoding="utf-8")
try:
    _hits = [v for _, v in _live_strings(_probe) if "todo.md" in v]
    check("and it fires on a real reference while ignoring prose", _hits == ["todo.md"], str(_hits))
finally:
    _probe.unlink()


print("\nthe proposal lifecycle lives on the project, and nowhere else")
seed()
projects.propose("demo", "a suggestion awaiting Brad", id="aa11bb22", channel="C1", by="Moses")
w = projects.pending_proposals()
check("a proposal is stored on its project", len(w) == 1 and w[0]["id"] == "aa11bb22")
check("and carries where it came from", w[0].get("channel") == "C1")

# THE RATIO IS THE WHOLE REASON THIS IS A STATE AND NOT A LIST. A suggestion nobody has agreed to
# must never read as progress, and must never read as an accepted candidate either.
check("a proposal is NOT counted as an idea", "ideas (" not in projects.dashboard())
check("but it IS impossible to miss", "AWAITING YOUR CONFIRM" in projects.dashboard())
check("and the brief marks it apart from ideas", "?1" in projects.brief())

projects.accept_proposal("demo", 1)
check("confirming flips it to an idea in place", projects.pending_proposals() == [])
check("and it is now counted as one", "ideas (1" in projects.dashboard())
check("the milestone ratio is untouched by any of it", "0/0" in projects.brief() or "—" in projects.brief())

seed()
projects.propose("demo", "one to dismiss", id="dd33ee44")
before = len(projects.pending_proposals())
projects.remove_idea("demo", 1)
check("dismissing removes it entirely", before == 1 and projects.pending_proposals() == [])

seed()
try:
    projects.accept_proposal("demo", 1)
    check("confirming something that isn't there is refused", False, "it was accepted")
except projects.ProjectError:
    check("confirming something that isn't there is refused", True)

projects.add_idea("demo", "an ordinary idea")
try:
    projects.accept_proposal("demo", 1)
    check("confirming an already-accepted idea is refused", False, "it was accepted twice")
except projects.ProjectError:
    check("confirming an already-accepted idea is refused", True)

print("\nthere is no second proposal store")
# The bug this replaces: proposals.json existed under MOSES_STATE, which resolves differently for the
# root service and for anything run as brad. Two stores, one unread for 17 days. A module writing a
# file called proposals.json anywhere is that bug returning.
_offenders = []
for _f in _modules:
    for _line, _val in _live_strings(_f):
        if "proposals.json" in _val and "migrated" not in _val:
            _offenders.append(f"{_f.name}:{_line}")
check("no module writes a proposals.json any more", not _offenders, ", ".join(_offenders))

# ── Where a milestone IS, not just whether it is finished ────────────────────
#
# `{title, done}` had two states and the estate has more. The cost was concrete: Brad's group-trips
# request lived only in a plan file because "being built" had nowhere to go, and travel-day mode was
# ticked as shipped when it was in early access — a beta test, not a release.
print("\nwhere a milestone is")
_L = Path(tempfile.mkdtemp(prefix="projects-lane-")) / "projects.json"
_L.write_text(json.dumps({"projects": [], "inbox": []}))
projects.REGISTRY = _L
projects.add("Lanes", rank=1)
# Lanes is about which column a milestone sits in, not about demand — so it answers the demand
# gate's one question for housekeeping and gets on with it. (The gate itself is tested at the end.)
projects.update("lanes", "kind", "internal")
projects.add_milestone("lanes", "one")
projects.add_milestone("lanes", "two")
projects.add_milestone("lanes", "three")

check("a milestone with no state is 'next'", projects.milestone_lane({"done": False}) == "next")
check("done outranks any state",
      projects.milestone_lane({"done": True, "state": "building"}) == "done")
check("an unknown state falls back to 'next' rather than vanishing off the board",
      projects.milestone_lane({"done": False, "state": "nonsense"}) == "next")

projects.set_milestone_state("lanes", 1, "building")
projects.set_milestone_state("lanes", 2, "ea")
_ms = projects.find(projects.load(), "lanes")["milestones"]
check("building is recorded", projects.milestone_lane(_ms[0]) == "building")
check("early access is recorded", projects.milestone_lane(_ms[1]) == "ea")
check("and the untouched one is still next", projects.milestone_lane(_ms[2]) == "next")

check("neither counts as done — early access is a beta test, not a release",
      sum(1 for m in _ms if m.get("done")) == 0, str(_ms))

projects.complete_milestone("lanes", 1)
_ms = projects.find(projects.load(), "lanes")["milestones"]
check("finishing something clears where it was, so it cannot sit in two columns",
      _ms[0].get("state") is None and projects.milestone_lane(_ms[0]) == "done", str(_ms[0]))

try:
    projects.set_milestone_state("lanes", 1, "building")
    check("a done milestone refuses to be moved back without an undo", False)
except projects.ProjectError:
    check("a done milestone refuses to be moved back without an undo", True)
try:
    projects.set_milestone_state("lanes", 3, "nonsense")
    check("an unknown state is refused at the door", False)
except projects.ProjectError:
    check("an unknown state is refused at the door", True)

# ── The inbox: capture with no project named ─────────────────────────────────
# ideas.md was the cross-project capture file and it died — 2 entries, untouched for a month. The
# replacement has to keep capture free: refusing until somebody names a project is how a thought
# said in passing gets lost.
print("\ncapture with no project")
projects.add_inbox_idea("something I thought of in the car")
check("an unfiled idea lands in the registry", len(projects.inbox()) == 1, str(projects.inbox()))
projects.add_inbox_idea("something I thought of in the car")
check("and is not written twice", len(projects.inbox()) == 1, str(projects.inbox()))
check("an unfiled idea is NOT counted against any project",
      "0/3" in projects.brief() or "1/3" in projects.brief(), projects.brief())
projects.claim_inbox_idea(1, "lanes")
check("claiming it moves it onto the project", not projects.inbox()
      and len(projects.find(projects.load(), "lanes").get("ideas", [])) == 1)
try:
    projects.add_inbox_idea("hi")
    check("too short to be an idea is refused", False)
except projects.ProjectError:
    check("too short to be an idea is refused", True)


# ── Item codes: V14 instead of the item's full title ─────────────────────────
# Brad, 2026-09-14. Items used to be addressed by list position, which shifts whenever something above
# is dropped or promoted, so "idea 3" named a different idea after every change. A code has to name
# one item for good.
print("\nevery item has a code that never moves")
projects.REGISTRY = _TMP          # the lanes section above pointed it at its own file
_TMP.write_text(json.dumps({"projects": [
    {"id": "viatica", "name": "Viatica", "rank": 1, "status": "active", "stage": "building",
     "last_change": {"at": "2026-09-01T00:00:00", "by": "cli"},
     "milestones": [{"title": "m-one", "done": True}, {"title": "m-two", "done": False}],
     "ideas": [{"title": "i-one", "state": "idea"}, {"title": "i-two", "state": "idea"},
               {"title": "i-three", "state": "proposed", "id": "abc12345"}]},
    {"id": "venture", "name": "Venture thing", "rank": 2, "status": "parked", "stage": "idea"},
]}), encoding="utf-8")


def _v():
    return projects.find(projects.load(), "viatica")


def _refs(p):
    return [i.get("ref") for i in (p.get("milestones") or []) + (p.get("ideas") or [])]


check("milestones are numbered before ideas, from 1", _refs(_v()) == ["V1", "V2", "V3", "V4", "V5"], str(_refs(_v())))
_keys = {p["id"]: p.get("key") for p in projects.load()["projects"]}
check("two projects starting with the same letter get different codes",
      _keys["viatica"] == "V" and _keys["venture"] not in ("V", "", None, projects.INBOX_KEY), str(_keys))

# Numbering is bookkeeping, not an update. If it counted, the day codes arrived every entry would look
# freshly updated, and moses-drift would stop seeing the stale ones.
projects.save(projects.load())
check("writing the codes does not count as updating the project",
      _v()["last_change"]["at"] == "2026-09-01T00:00:00", str(_v().get("last_change")))
check("and they are on disk now",
      json.loads(_TMP.read_text(encoding="utf-8"))["projects"][0]["milestones"][0].get("ref") == "V1")

projects.remove_idea("viatica", "V3")                                  # i-one, by its code
check("an item is found by its code", [i["title"] for i in _v()["ideas"]] == ["i-two", "i-three"],
      str(_v()["ideas"]))
check("dropping one moves nobody else's code", _refs(_v()) == ["V1", "V2", "V4", "V5"], str(_refs(_v())))
projects.add_idea("viatica", "i-four")
check("a new item never reuses a dropped number", _v()["ideas"][-1]["ref"] == "V6", str(_v()["ideas"][-1]))

projects.promote_idea("", "v4")                                        # project read off the code; any case
_ms = _v()["milestones"]
check("a promoted idea keeps its code", _ms[-1]["title"] == "i-two" and _ms[-1]["ref"] == "V4", str(_ms[-1]))
projects.complete_milestone("viatica", "V4")
check("and is still found by it as a milestone", _v()["milestones"][-1]["done"] is True, str(_v()["milestones"]))
check("the old way, by position, still works",
      "done" in projects.complete_milestone("viatica", 2) and _v()["milestones"][1]["done"] is True)

check("a proposal can be found by its code", (projects.find_proposal("v5") or {}).get("title") == "i-three")
projects.accept_proposal("viatica", "V5")
check("and confirmed by it", next(i for i in _v()["ideas"] if i["ref"] == "V5")["state"] == "idea")

for _label, _call in [
    ("an idea's code given to a milestone call is refused, saying what it is",
     lambda: projects.complete_milestone("viatica", "V5")),
    ("a dropped code is refused, not quietly mapped to something else",
     lambda: projects.remove_idea("viatica", "V3")),
]:
    try:
        _call()
        check(_label, False, "it went through")
    except projects.ProjectError:
        check(_label, True)

# Another project's code can never match here, so refusal alone proves nothing. What matters is that
# the answer says whose code it is, so the person asking can fix it.
try:
    projects.remove_idea("viatica", f"{_keys['venture']}1")
    check("another project's code is refused, saying whose it is", False, "it went through")
except projects.ProjectError as _e:
    check("another project's code is refused, saying whose it is", "its code is V" in str(_e), str(_e))

check("a code can be looked up on its own", "i-four" in projects.item("V6") and "Viatica" in projects.item("V6"),
      projects.item("V6"))
check("the dashboard shows each item's code", "V6 i-four" in projects.dashboard(), projects.dashboard())

projects.add_inbox_idea("an unfiled thought")
check("an inbox item gets an inbox code", projects.inbox()[0].get("ref") == "IN1", str(projects.inbox()))
projects.claim_inbox_idea("IN1", "viatica")
check("claimed by that code, it takes the project's next number", _v()["ideas"][-1]["ref"] == "V7",
      str(_v()["ideas"][-1]))

# The case the stored counter exists for: drop the HIGHEST code, then add. Counting from the codes still
# present would hand V7 out again, and an old "V7" in a conversation would then name the wrong thing.
projects.remove_idea("", "V7")
projects.add_idea("viatica", "i-five")
check("dropping the newest item does not free its number", _v()["ideas"][-1]["ref"] == "V8",
      str(_v()["ideas"][-1]))


# ── The demand gate ─────────────────────────────────────────────────────────
#
# The gate refuses to let a BUSINESS start building until someone has said who pays, what was
# observed, and what would prove it wrong. It exists because this estate once ran months of
# correctness guards with not one instrument aimed at demand.
#
# Both halves are under test, and the second is the one that matters more. A gate that fires on
# housekeeping gets switched off within a week, and a switched-off gate protects nothing.
seed(id="biz", name="A Business", stage="milestones", kind="", milestones=[{"title": "m1", "done": False}])

try:
    projects.update("biz", "stage", "building")
    check("a project with no kind cannot start building", False, "it went through")
except projects.ProjectError as _e:
    check("a project with no kind cannot start building",
          "business" in str(_e) and "internal" in str(_e), str(_e))

projects.update("biz", "kind", "business")
try:
    projects.update("biz", "stage", "building")
    check("a business with no demand answers cannot start building", False, "it went through")
except projects.ProjectError as _e:
    check("a business with no demand answers cannot start building",
          all(f in str(_e) for f, _ in projects.DEMAND_FIELDS), str(_e))

# Partly answered is still refused, and the refusal must name WHICH ones are missing — a gate that
# says only "no" teaches people to guess.
projects.update("biz", "who_pays", "a travel agent who books group trips")
try:
    projects.update("biz", "stage", "building")
    check("a half-answered business is refused, naming what is missing", False, "it went through")
except projects.ProjectError as _e:
    check("a half-answered business is refused, naming what is missing",
          "evidence" in str(_e) and "kill_test" in str(_e) and "who_pays" not in str(_e), str(_e))

projects.update("biz", "evidence", "they email a PDF to every client today")
projects.update("biz", "kill_test", "ask what they would pay; 'nothing' kills it")
projects.update("biz", "stage", "building")
check("answered, it goes through", projects.find(projects.load(), "biz")["stage"] == "building")

# THE FALSE-POSITIVE HALF. Housekeeping answers one question and is never asked the other three.
seed(id="house", name="Housekeeping", stage="milestones", kind="internal",
     milestones=[{"title": "m1", "done": False}])
projects.update("house", "stage", "building")
check("housekeeping walks straight through", projects.find(projects.load(), "house")["stage"] == "building")

# The other door: marking a milestone "building" IS starting to build.
seed(id="biz2", name="Another Business", stage="milestones", kind="business",
     milestones=[{"title": "m1", "done": False}])
try:
    projects.set_milestone_state("biz2", 1, "building")
    check("the milestone door carries the same gate", False, "it went through")
except projects.ProjectError as _e:
    check("the milestone door carries the same gate", "who_pays" in str(_e), str(_e))
check("a refused milestone is left untouched",
      not projects.find(projects.load(), "biz2")["milestones"][0].get("state"),
      str(projects.find(projects.load(), "biz2")["milestones"][0]))

# And it does not fire twice: a project already building is asked nothing when another milestone
# starts. The question is "should this be built", which is answered once, not per milestone.
seed(id="biz3", name="Already Building", stage="building", kind="business",
     milestones=[{"title": "m1", "done": False}, {"title": "m2", "done": False}])
projects.set_milestone_state("biz3", 2, "building")
check("a project already building is not re-interrogated",
      projects.find(projects.load(), "biz3")["milestones"][1].get("state") == "building")

# Visible, not merely stored.
seed(id="biz4", name="Shown Business", stage="building", kind="business", who_pays="a travel agent",
     evidence="they email PDFs today", kill_test="ask what they would pay", milestones=[])
_dash = projects.dashboard()
check("the dashboard shows who pays",
      "pays: a travel agent" in _dash and "proof: they email PDFs today" in _dash, _dash)
seed(id="biz5", name="Silent Business", stage="building", kind="business", milestones=[])
check("an unanswered question shows as a gap, not as nothing",
      "— unanswered —" in projects.dashboard(), projects.dashboard())


print(f"\n  {len(fails)} failed" if fails else "\n  all good")
sys.exit(1 if fails else 0)
