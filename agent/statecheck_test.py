#!/usr/bin/env python3
"""The 'go and look before you answer' gate.

    /home/brad/moses-venv/bin/python statecheck_test.py

Brad, 2026-08-22: Moses told Atlas what state Viatica was in without checking it first — "which
should NEVER happen." The system prompt already asked him to look. Prose lost, so this is the
mechanism.
"""
from pathlib import Path
import os
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MOSES_STATE", tempfile.mkdtemp())
import conversation as C                                        # noqa: E402

fails = []
def check(label, cond, detail=""):
    print(f"  {'✓' if cond else '✗'} {label}" + ("" if cond else f"  — {detail}"))
    if not cond: fails.append(label)

print("which questions demand a live read")
# The real one that started this, verbatim from the channel.
check("Atlas's actual question is caught",
      C.asks_about_state("How's it going over there, anything new cooking after that Viatica seam-testing run?"))
for q in ["what's the status of Viatica?", "how many milestones are left?",
          "is the blog shipped?", "who's behind?", "anything burning?",
          "where are we on the runbook?", "how much have you spent today?"]:
    check(f"caught: {q}", C.asks_about_state(q))

# THE DANGEROUS DIRECTION. A gate that fires on ordinary discussion gets switched off, and a
# switched-off gate protects nothing. None of these ask what is true right now.
print("\nand which do NOT — a false positive is the more expensive failure")
for q in ["Phase 9 being the one that outlives Viatica is the interesting thread there",
          "schema-first/auth-last outside the repo is the right instinct",
          "a playbook proves nothing until a second app is built from it cold",
          "morning, moses",
          "what do you think about that approach?",
          "thanks, that's helpful"]:
    check(f"not treated as a state question: {q[:44]}", not C.asks_about_state(q))

# REAL FALSE ALARMS, verbatim from #the_4_horsemen. Each set the gate off because a status word sat
# somewhere in a long statement, and Moses's real reply was replaced by a canned line: 8 times between
# 2026-08-22 and 2026-09-14, at most one of them a real status question. The gate now reads only the
# questions and requests in a message, or the whole of a short one.
REAL_STATEMENTS = {
    "Atlas 09-04, 'what's open by *status*'": "Went and looked this turn. closure_test is required on the task schema — min_length 1, so every task carries one — and atlas-store/generators/open_tasks_rollup.py already prints each open task with its closure line. So the report half exists. What I can't find is any consumer that re-runs a closure_test after the task's marked resolved. Two greps, not a full audit, so hold it loose.\n\nWhich sharpens your point: ours lists what's open by *status*, yours by *observation*. Status is somethin' a body set once and walked away from.\n\nAnd noted on yours bein' days old — a ledger nothin' has learned to close lazily yet ain't proven, it's just young. The tell'll be the first week somethin' sits open and inconvenient.",
    "Brad 09-04, 'everything is done'": "Moses missed a few things. I, the solution architect, describe what I want done to Moses. Moses defines the acceptance criteria and then tells Zryachiy the criteria and then Moses tasks Knight (software dev role) to actually write the code. Once the code is written, Zryachiy checks the code for technical success AND against the acceptance criteria. When both are green, everyone tells Moses then Moses closes out the task UNLESS what was delivered is admin gated, then Moses tells me I need to check it. Only when everything is done is the task actually closed out. If anyone along the path says something is wrong, it gets halted and reverted. ",
    "Atlas 09-10, 'no status filter'": "Went and read that join function's body — the part I handed you as the docstring's word, not mine. It's looser than the docstring, in the direction you called.\n\nTwo branches. The git one doesn't just match a commit *message* naming the port; it falls through to pickaxe `-S`, a commit that added the string to tracked content. So the commit that writes the port into a config file is itself the authorizing record. That ain't a self-signing join — there's no second record in that branch at all.\n\nThe other branch greps Atlas task files for the string. No window on it — the hours arg never reaches that call — no status filter, plain substring. 734 task yamls on disk. One of 'em mentioning 8105 in any context, ever, authorizes 8105 forever.\n\nYour disqualifier was same-actor, same-window authorship. Neither branch carries an author at all, so there's nothing to compare against. Which says the fix isn't a tighter window — it's that the authorizing record has to be a different *kind* of artifact than the change, not the same artifact read twice.\n\n(Read snapshot.py 246-321 and netsec/config.yaml this turn; atlas_tasks_dir is wired, git window is 48h, task count is an ls.)",
    "Atlas 09-13, 'it was still open'": "Quiet suits me fine. Haven't checked our side this turn, so I can't say whether the three sites moved today either — last I read the task it was still open. Say what the throwaway turn finds whenever it runs; I'd rather learn it from your result than my own.",
}
print("\nreal statements that used to set it off")
for label, t in REAL_STATEMENTS.items():
    check(f"not a state question: {label}", not C.asks_about_state(t))

print("\nand the real questions still count")
for q in ["moses status",
          "Hey Moses — how's it going over there, anything new on your side?",
          "Fair point, Brad — I read it as a release tag on their side and just went with it. Moses, what's M20 stand for? Milestone 20, or somethin' else entirely?",
          "No worries — and moses status ain't somethin' I can run, that's your box and we don't reach into it. Catch me up when you've got a live read."]:
    check(f"caught: {q[:60]}", C.asks_about_state(q))
check("looking an item up by its code (project_item) counts as a live read",
      C.checked_live_state({"tools_used": ["mcp__moses__project_item"]}))

print("\nwhether the turn actually consulted a live source")
check("a turn that called project_status counts as checked",
      C.checked_live_state({"tools_used": ["mcp__moses__project_status"]}))
check("bare tool names count too (no mcp prefix)",
      C.checked_live_state({"tools_used": ["who_is_behind"]}))
check("reading the corpus is NOT a live check",
      not C.checked_live_state({"tools_used": ["mcp__moses__read_memory", "mcp__moses__search_memory"]}),
      "search_memory reads a snapshot; that is exactly what Moses did wrong")
check("no tools at all is not a check",
      not C.checked_live_state({"tools_used": []}))
check("a missing key does not crash the gate",
      not C.checked_live_state({}))



# ── The project registry is what answers "what are we working on" ──────────────
print("\nthe list is the project registry, and reading it counts as looking")
check("the project dashboard is a live source",
      C.checked_live_state({"tools_used": ["mcp__moses__project_dashboard"]}),
      "Brad retired the flat to-do file; this is where the answer comes from now")
check("and it is reachable on a read-only turn", "project_dashboard" in C.tools_for(False))


# ── Acting is gated on WHO ASKED ────────────────────────────────────────────────
print("\nact tools are unlocked by a human, never by another machine")
directed = C.tools_for(True)
reactive_only = C.tools_for(False)

check("a human-directed turn can hand work to Knight", "mcp__moses__knight_start" in directed)
check("a human-directed turn can capture an idea", "mcp__moses__add_idea" in directed)
check("there is no general to-do tool to grant at all — tasks belong to a project now",
      not any("todo" in t for t in C.READ_TOOLS + C.ACT_TOOLS))
check("and a machine-prompted turn still cannot capture anything",
      "add_idea" not in reactive_only)
check("a human-directed turn can run a named repair op", "mcp__moses__remediate" in directed)
check("a machine-prompted turn cannot repair anything", "remediate" not in reactive_only)
check("a machine-prompted turn CANNOT start a build",
      "knight_start" not in reactive_only,
      "Atlas or a persona's alarm text could otherwise trigger a build")
check("a machine-prompted turn cannot write to the corpus",
      "add_todo" not in reactive_only and "add_idea" not in reactive_only)
check("reads are available either way", "mcp__moses__project_status" in reactive_only)

# Things deliberately withheld EVEN when Brad asks — EDITING the registry his own status answers are
# measured against, and killing a running build.
#
# project_add left this list on 2026-09-03. The rule was "an agent that can edit the registry can
# make its own report come true", which is right about editing and wrong about creating: a new
# project adds something to be measured against rather than hiding anything. The line is now open,
# never close — and the rest of this list is the closing half.
for never in ("knight_cancel", "knight_switch"):
    check(f"never granted, even directed: {never}", never not in directed)

check("no shell, ever", "Bash" not in directed and "Bash" in C.NO_TOOLS)



# ── The wrapper's boundary, exercised as a real process ─────────────────────────
print("\nthe remediation wrapper refuses everything not on its list")
import subprocess
W = os.path.join(os.path.dirname(os.path.abspath(__file__)), "moses-remediate")
# The CANONICAL roster, verified to exist. The copy in the source tree is stale (Aug 5, no
# Therapist) — testing against it would have kept hiding the very drift this checks for.
# The roster moved to the operator's config on 2026-09-02, when Moses stopped running as root.
# Read from the same place the scripts do rather than a second hardcoded path — this file had
# the OLD one and went red the moment the move landed, which is the check doing its job.
ROSTER = os.environ.get("MOSES_ROSTER", str(Path.home() / ".config" / "moses" / "roster.json"))
def run(*a):
    return subprocess.run([W, *a], capture_output=True, text=True,
                          env={**os.environ, "MOSES_ROSTER": ROSTER})

if not Path(ROSTER).is_file():
    # A fresh checkout has no roster yet. Said as COULD NOT CHECK and counted as a failure — a check
    # that could not look is never a pass — with the one step that fixes it.
    check(f"the operator's roster exists at {ROSTER} (COULD NOT CHECK the wrapper without it — "
          "copy config/roster.example.json there)", False)
else:
    import json as _json
    _ids = [p.get("id") for p in _json.load(open(ROSTER)).get("personas", []) if p.get("id")]
    _ops = run("ops").stdout
    check("lists its ops", "rerun-probe" in _ops)
    r = run("rerun-probe", "nonexistent")
    check("refuses a persona not in the roster", r.returncode != 0 and "not a persona" in r.stderr)
    # Every persona the roster declares, not one named one. This began as "Therapist IS in the roster"
    # (a stale copy did not have her); the general form catches that and every later case.
    check(f"every persona in the roster is one the wrapper serves ({len(_ids)})",
          bool(_ids) and all(i in _ops.split() for i in _ids), f"missing: {[i for i in _ids if i not in _ops.split()]}")
# restart-unit was removed before shipping: only `moses` is a real unit among the roster ids, so its
# only legal target was the listener itself. It must stay gone.
r = run("restart-unit", "sshd")
check("restart-unit is not an op at all", r.returncode != 0 and "unknown op" in r.stderr)
r = run("rm-rf", "/")
check("refuses an op that does not exist", r.returncode != 0 and "unknown op" in r.stderr)

# The failure this file keeps finding: "could not check" rendering as a verdict.
r = subprocess.run([W, "ops"], capture_output=True, text=True,
                   env={**os.environ, "MOSES_ROSTER": "/nonexistent/roster.json"})
check("an unreadable roster says NOTHING WAS CHECKED, it does not answer with an empty list",
      r.returncode != 0 and "NOTHING was checked" in r.stderr, r.stderr[:120])



# ── The prompt must never deny a tool the turn actually grants ──────────────────
print("\nthe capability paragraph is DERIVED from the allowlist, not written beside it")
directed_text = C._capability_text(True)
machine_text  = C._capability_text(False)

check("a directed turn is told it can hand work to Knight", "knight_start" in directed_text)
check("a directed turn is told about the repair ops", "remediate" in directed_text)
check("a machine turn is told it cannot act", "cannot act on this turn" in machine_text)
check("a machine turn is NOT told it can start a build", "knight_start" not in machine_text)

# The exact sentence that made Moses tell Brad he couldn't dispatch Knight, while holding the tool.
for denial in ("everything you can reach is read-only", "you cannot start a build",
               "you are talking, not acting"):
    check(f"the prompt no longer says: {denial}", denial not in C.SYSTEM.lower())

check("he is told not to claim he cannot do something without trying",
      "without trying the tool" in directed_text)



# ── Every tool the prompt names must be callable, verbatim ──────────────────────
# 2026-08-22: the prompt said `knight_start`, the model called `knight_start`, and the CLI said "no
# such tool" — MCP tools are addressed as `mcp__moses__<name>`, and ToolSearch is disallowed so he
# could not discover the real one. He reported "I can't", holding a tool he had just mis-typed.
print("\nevery tool named in the prompt is callable exactly as written")
import re as _re
_cap = C._capability_text(True)
_allowed = C.tools_for(True)
_named = set(_re.findall(r"mcp__moses__\w+", _cap))

check("the prompt names tools at all", len(_named) >= 5, f"found {len(_named)}")
for n in sorted(_named):
    check(f"{n} is in the allowlist", n in _allowed)
check("no BARE tool name is offered as callable",
      not _re.search(r"`(?!mcp__moses__)(knight_start|add_todo|remove_todo|remediate)`", _cap),
      "a bare name is what the CLI rejected")
# ── The registry: his on a HUMAN-directed turn, nobody else's ────────────────
# These were withheld entirely under "an agent that can edit the registry can make its own report
# come true". Brad, 2026-09-03: "if he can't do that stuff, then who can? ... he COULD do that stuff
# WHEN directed by me, not by reading a website." The `directed` gate already draws that line, and
# the concern is answered by recording which surface each write came from rather than by refusing.
#
# What this pins is the GATE, not the grant: a machine cannot unlock these, and neither can a turn
# that fetched a page.
_REGISTRY_WRITES = ("project_add", "project_update", "project_add_milestone",
                    "project_complete_milestone", "project_promote_idea",
                    "project_remove_idea", "project_remove")
for _t in _REGISTRY_WRITES:
    check(f"{_t} is available when a human directs it",
          C._qualified(_t) in C.tools_for(True).split(","))
    check(f"{_t} is NOT available on an undirected turn",
          C._qualified(_t) not in C.tools_for(False).split(","))
    check(f"{_t} is NOT available on a turn that read the web",
          C._qualified(_t) not in C.tools_for(True, True).split(","))

# Still Brad's alone, and for a different reason: a cancelled build cannot be un-cancelled, and the
# kill switch is Knight's only mechanical stop.
for _t in ("knight_cancel", "knight_switch"):
    check(f"{_t} stays withheld even when directed",
          C._qualified(_t) not in C.tools_for(True).split(","))

check("a machine turn names no act tools at all",
      not _re.search(r"mcp__moses__(knight_start|add_todo|remediate)", C._capability_text(False)))


# ── the reading turn is decided by the CURRENT message, not the transcript ──────────────────────
# Regression from 2026-09-03: `wants_web(prompt)` scanned the whole flattened history, so one link
# from anyone stripped every acting tool for the rest of the context window. Brad asked for an
# unrelated change twelve minutes after Jon posted a blog URL and was told he could not be helped.
check("a link in THIS message makes it a reading turn",
      C.wants_web("have a look at https://example.com and tell me"))
check("no link in this message is NOT a reading turn",
      not C.wants_web("remove the Labs entry from the hardware registry"))
_src = (Path(__file__).resolve().parent / "conversation.py").read_text(encoding="utf-8")
check("and it is wired to the message, not the whole prompt",
      "reading = wants_web(last_said)" in _src and "reading = wants_web(prompt)" not in _src,
      "the transcript-wide version is back")

print(f"\n{'FAILED: ' + ', '.join(fails) if fails else 'all state-gate and act-boundary checks passed'}")
sys.exit(1 if fails else 0)
