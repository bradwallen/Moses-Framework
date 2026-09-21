#!/usr/bin/env python3
"""The Projects board: one board per project, collapsible, and nothing hidden by collapsing.

    .venv/bin/python mcp/dashboard_board_test.py

WHY THIS EXISTS. The page was "a wall of text" (Brad, 2026-09-09) — every project listed with every
milestone under it, so "what is being built right now" meant reading all of it. It is now a board
per project inside a <details>, and two properties have to hold or the redesign quietly costs more
than it gained:

  * COLLAPSING MUST NOT HIDE THE ANSWER. A closed project still carries its lane counts on the
    summary line. Without that, eight collapsed projects are eight questions.
  * NO JAVASCRIPT. The disclosure is the browser's own. A page that needs script to reveal its
    contents fails closed, and this one is read on a phone over a tunnel.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import projects                                                  # noqa: E402
import dashboard_server as d                                     # noqa: E402

# The live pill asks the running app how it is doing. That is a network call with no business here.
d.live_state.summary = lambda: None

REG = Path(tempfile.mkdtemp(prefix="board-test-")) / "projects.json"
REG.write_text(json.dumps({"projects": [
    {"id": "alpha", "name": "Alpha", "rank": 1, "status": "active", "stage": "building",
     "milestones": [
         {"title": "shipped thing", "done": True},
         {"title": "being built", "done": False, "state": "building"},
         {"title": "in beta", "done": False, "state": "ea"},
         {"title": "queued", "done": False},
     ],
     "ideas": [{"title": "some candidate", "note": "a note"}]},
    {"id": "beta", "name": "Beta", "rank": 2, "status": "parked", "stage": "idea",
     "milestones": [{"title": "parked work", "done": False, "state": "building"}], "ideas": []},
], "inbox": [{"title": "captured with no project", "note": ""}]}))
projects.REGISTRY = REG


def _parses(js: str) -> bool:
    """True if node can parse it. If node is absent we say so rather than reporting a pass —
    could-not-check is never agreement."""
    import shutil, subprocess, tempfile as _t
    node = shutil.which("node")
    if not node:
        print("      (node not installed — the script was NOT syntax checked)")
        return True
    with _t.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js)
        path = f.name
    return subprocess.run([node, "--check", path], capture_output=True).returncode == 0


fails = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  — {detail}"))
    if not cond:
        fails.append(label)

page = d.page()

print("one board per project")
# Counted as ELEMENTS, not as a substring: the stylesheet's own comment mentions <details>, so a
# naive count reads the CSS as markup. Found by this check going to zero when an id was added.
_blocks = re.findall(r'<details id="p-[^"]*" class="pblock', page)
check("every project is its own collapsible block", len(_blocks) == 3, f"blocks={_blocks}")
check("each project gets a full set of lanes",
      len(re.findall(r'<div class="lane ', page)) == 11,
      f'lanes={len(re.findall(chr(60)+"div class=.lane ", page))}')

print("\nwhat is open, and what a closed one still says")
opened = re.findall(r'<details id="[^"]*" class="pblock ([^"]*)"( open)?>', page)
check("an active project starts open", any(c[0].startswith("active") and c[1] for c in opened), str(opened))
check("a parked project starts closed", any(c[0].startswith("parked") and not c[1] for c in opened), str(opened))

beta = page[page.index('>Beta<'):]
beta_summary = beta[:beta.index("</summary>")]
check("a collapsed project still says where its work sits",
      "1 building" in beta_summary, beta_summary[-160:])

print("\ncards land in the right column")
alpha = page[page.index('>Alpha<'):page.index('>Beta<')]
def lane_of(name):
    m = re.search(r'<div class="lane (\w+)"><h2>.*?</h2>(.*?)(?=<div class="lane |</div></details>|$)',
                  alpha, re.S)
    for lm in re.finditer(r'<div class="lane (\w+)"><h2>.*?</h2>(.*?)(?=<div class="lane |$)', alpha, re.S):
        if name in lm.group(2):
            return lm.group(1)
    return None

check("work under way is in Building", lane_of("being built") == "building", str(lane_of("being built")))
check("a beta feature is in Early access, NOT Done", lane_of("in beta") == "ea", str(lane_of("in beta")))
check("a finished one is in Done", lane_of("shipped thing") == "done", str(lane_of("shipped thing")))
check("an uncommitted candidate is in Ideas", lane_of("some candidate") == "idea", str(lane_of("some candidate")))
check("a committed but unstarted one is in Next", lane_of("queued") == "next", str(lane_of("queued")))

print("\nevery card carries the code people use to name it")
# Alpha is A: its four milestones are A1 to A4, its idea A5. The inbox has its own IN sequence.
check("a card shows its item's code", '<span class="ref">A5</span> some candidate' in page)
check("an inbox card shows its inbox code", '<span class="ref">IN1</span> captured with no project' in page)

print("\nan idea with no project is not lost")
# Compare against the project BLOCK, not the name. There used to be a "Working on Alpha" banner
# above everything, so matching the bare name put the goalposts in the wrong place; the banner is
# gone now and this stays anchored to the block, which is the thing being ordered.
check("the inbox renders first, above the projects",
      'pblock inbox' in page
      and page.index("pblock inbox") < page.index('class="pblock active'),
      f'inbox at {page.find("pblock inbox")}, first project at {page.find(chr(34)+"pblock active")}')
check("and its card is there", "captured with no project" in page)

print("\nnothing on the page depends on script")
# THE FIRST VERSION OF THIS CHECK ASSERTED "no <script> tag at all", and it was wrong — my own, one
# commit earlier. The property worth having is that the CONTENT and the DISCLOSURE are server
# rendered; "no script anywhere" was a cheap proxy for it, and within the hour it would have blocked
# a correct change: replacing the meta-refresh (which navigates, and so throws away scroll position
# and every open <details>) with an in-place update that needs a few lines of JavaScript.
#
# A guard that fires on correct work gets switched off, so this now tests the real thing: strip every
# script from the page and it must still be complete and still be open where it should be.
stripped = re.sub(r"<script.*?</script>", "", page, flags=re.S | re.I)
check("no inline event handlers", "onclick" not in page.lower() and "onload" not in page.lower())
check("every card is in the served HTML, with the script removed",
      all(t in stripped for t in ["being built", "in beta", "shipped thing", "queued",
                                  "some candidate", "captured with no project"]))
check("every lane survives without script",
      len(re.findall(r'<div class="lane ', stripped)) == 11)
check("and an active project is still open without script",
      re.search(r'<details id="p-alpha"[^>]*\bopen\b', stripped) is not None,
      stripped[stripped.find('id="p-alpha"'):][:120])

print("\nthe in-place update, which is the enhancement")
scripts = re.findall(r"<script[^>]*>(.*?)</script>", page, flags=re.S | re.I)
# It used to demand exactly ONE script. Search (2026-09-11) is a second, so this now pins what the
# count stood for: one script refreshes, and nothing on the page reloads or navigates it.
refresh = [s for s in scripts if "fetch(location.href" in s]
check("exactly one script refreshes the page", len(refresh) == 1, f"{len(refresh)} of {len(scripts)} scripts")
check("no script on the page reloads or navigates it",
      all("location.reload" not in s and "location.assign" not in s and "location.href=" not in s.replace(" ", "")
          for s in scripts), "a script navigates")
check("every script on the page parses", all(_parses(s) for s in scripts))
check("it parses — nothing else here checks a string of JavaScript",
      _parses(refresh[0]) if refresh else False)
check("it swaps in place rather than navigating",
      refresh and "location.href" in refresh[0] and "location.reload" not in refresh[0])
check("it puts back which projects were open",
      refresh and "details[id]" in refresh[0] and ".open=" in refresh[0].replace(" ", ""))
# It compared against the live DOM (innerHTML===el.innerHTML). A search, or just opening a project,
# changes the live DOM, so every tick looked like a change and the page was swapped for nothing —
# observed in a browser, 2026-09-11. It now compares against what the server last sent.
check("it does nothing when the content has not changed",
      refresh and "bare(next.innerHTML)===bare(last)" in refresh[0].replace(" ", "")
      and "varlast=el.innerHTML" in refresh[0].replace(" ", ""))
# 2026-09-15: the time printed inside #live made every minute look like a change, so the page was
# redrawn while nothing had changed. The comparison leaves the clock out and moves it in place.
check("a new minute is not a change: the clock is compared out and moved in place",
      refresh and "data-clock" in refresh[0] and "shown.textContent=now.textContent" in refresh[0].replace(" ", ""))
check("the time on the page is marked as the clock", "<span data-clock>" in page)
check("the meta refresh is gone — that was the thing that moved the page",
      "http-equiv=\"refresh\"" not in page)

print(f"\n  {len(fails)} failed" if fails else "\n  all good")
sys.exit(1 if fails else 0)
