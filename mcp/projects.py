"""projects — the registry Moses keeps, and everything that reads or changes it.

WHO DRIVES THIS (Brad, 2026-08-18): "Moses is the keeper of Projects. I tell him from some
interface, he adds it to the list." Brad works in the Claude iOS app, the desktop app, VS Code and
Slack — he does not open a terminal to file an idea. So the real front door is the MCP tools, and
every one of them is a thin call into this module.

The CLI is the same functions with a different hat on. ONE implementation, because the last time
this code existed twice the two copies drifted and the cross-check in one of them had been silently
inert for weeks.

A PROJECT HAS A LIFE: idea → scope → milestones → building → shipped. An idea with nothing else
filled in is a complete, valid entry — the point is catching a thought in one sentence, not making
anyone write a plan before they are allowed to write it down.
"""

from __future__ import annotations

import sys as _sys, pathlib as _pl  # noqa: E401
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent / "agent" / "lib"))
import moses_env as _env  # noqa: E402 — whose home, where Moses lives, the operator's settings
import datetime as _dt
import os
import json
import re
from datetime import datetime
from pathlib import Path

# OVERRIDABLE SO A TEST CANNOT WRITE INTO THE REAL ONE. `MOSES_REGISTRY` is the same name
# moses-drift already reads. Found the hard way on 2026-09-09: the moment capture started landing
# here instead of a flat file, agent/answer_test.py — which exercises the capture path — began
# appending its fixture straight into Brad's live registry. That is the third store in one day to
# be polluted by its own test (Knight's job records, the #viatica-dev channel, this).
REGISTRY = Path(os.environ.get("MOSES_REGISTRY") or _env.MEMORY / "projects.json")

STAGES = ["idea", "scope", "milestones", "building", "shipped"]

# WHERE A MILESTONE IS, not just whether it is finished. `{title, done}` had exactly two states and
# the estate has more than two: work can be under way, and it can be live for named testers without
# being released. Both were invisible, and the cost was concrete — Brad's own group-trips request
# was never written down anywhere durable because "being built" had nowhere to live (it sat in a
# plan file that the next plan overwrites), and travel-day mode was ticked as SHIPPED when it was
# in early access, which is a beta test rather than a release. See [[project_btp_releases]].
#
# `done: True` still means done and outranks any state, so every entry written before this reads
# exactly as it did.
MILESTONE_STATES = ["next", "building", "ea"]
MILESTONE_STATE_LABEL = {"next": "Next", "building": "Building", "ea": "Early access"}


def milestone_lane(m: dict) -> str:
    """The one place that decides which column a milestone belongs in."""
    if m.get("done"):
        return "done"
    st = m.get("state")
    return st if st in MILESTONE_STATES else "next"
STATUSES = ["active", "parked", "dormant", "scrapped"]
SETTABLE = ["status", "stage", "rank", "target", "scope", "focus", "next", "phase", "name", "repo",
            "kind", "who_pays", "evidence", "kill_test"]

# ── The demand gate: no business starts building on a hunch ──────────────────
#
# WHY (2026-09-24): "I don't always just want a yes-man to build exactly what I want."
# Drawn from a real product in this estate, which grew feature after feature — every one properly
# engineered, guarded, tested and documented — while the question of whether anyone wanted it went
# unasked for months. Every instrument here points at whether the code is CORRECT. Not one pointed
# at demand, and nobody noticed, because a missing instrument is invisible in a way a failing test
# never is.
#
# So the three questions get asked once, at the moment building starts, and the answers are stored
# where the dashboard shows them. It is three lines, not a business plan.
#
# NOT EVERY PROJECT IS A BUSINESS, and a gate that fires on correct work gets switched off
# (commandment 8). Housekeeping — this machine's own site, the framework, backups — answers
# `internal` and walks straight through. The one thing nobody may skip is SAYING WHICH IT IS:
# "is this actually a business?" is the question that never gets asked out loud.
#
# IT REFUSES SILENCE, NOT DOUBT. "Nobody yet, this is a bet" is a valid answer and opens the gate,
# because a gate that demands a particular answer only teaches people to write that answer.
KINDS = ["business", "internal"]
DEMAND_FIELDS = [
    ("who_pays", "who SPECIFICALLY pays — a named person or a describable role, not 'travelers'"),
    ("evidence", "what has been OBSERVED that says they want it — behavior, not a hunch"),
    ("kill_test", "the cheapest test that could prove this wrong, and the result that would kill it"),
]


DEMAND_LABEL = {"who_pays": "pays", "evidence": "proof", "kill_test": "kills it"}


def assert_may_build(p: dict) -> None:
    """Refuse to enter `building` until the demand behind it is on the record.

    Raises with the exact command to answer, because a gate that only says no is a gate people
    route around — and one nobody can satisfy is one somebody deletes.
    """
    kind = (p.get("kind") or "").strip().lower()
    if kind not in KINDS:
        raise ProjectError(
            f"{p['name']} has not said whether it is a business or housekeeping, and building "
            f"starts here.\n"
            f"  moses-project set {p['id']} kind business   — someone is meant to pay for this\n"
            f"  moses-project set {p['id']} kind internal   — housekeeping, no case to make")
    if kind != "business":
        return
    missing = [(f, why) for f, why in DEMAND_FIELDS if not (p.get(f) or "").strip()]
    if missing:
        lines = [f"{p['name']} is a business, and {len(missing)} of the three questions that decide "
                 f"whether it is worth the months are unanswered:"]
        lines += [f"  {f} — {why}" for f, why in missing]
        lines.append(f"  moses-project set {p['id']} <field> \"<answer>\"")
        lines.append("An honest \"nobody yet, this is a bet\" is an answer and opens the gate. "
                     "Silence is not.")
        raise ProjectError("\n".join(lines))

# Ranks at or above this are "not in the running order" — finished or abandoned work that should
# still be listed, but never numbered against live projects.
ARCHIVE_RANK = 90

# ── Item codes: V14, not "Donations and a follow-along supporters page" ──────
#
# Brad, 2026-09-14: "a short alphanumeric numbering system for each item so you and I can reference
# that." Every milestone, idea and proposal carries a `ref`: its project's `key` plus a number. V14 is
# Viatica's fourteenth item.
#
# ONE SEQUENCE PER PROJECT, shared by milestones, ideas and proposals, and a number is NEVER REUSED.
# That is the reason for the scheme. The tools used to address items by list position, which shifts
# whenever something above is dropped or promoted, so "idea 3" meant a different idea after every
# change. An idea promoted to a milestone keeps its code, so V14 still finds it.
#
# ASSIGNED IN load() AND save(), not by each function that adds an item. Same reasoning as the
# last_change stamp: a rule every caller has to remember is one some caller forgets.
REF_RE = re.compile(r"^\s*([A-Za-z]{1,3})-?(\d{1,5})\s*$")
INBOX_KEY = "IN"                  # the inbox's own sequence; reserved so no project can take it
REF_FIELDS = ("key", "next_ref")  # numbering bookkeeping, not a change anybody made (see save)


class ProjectError(Exception):
    """Something the caller asked for cannot be done, with a reason worth showing them."""


def _derive_key(name: str, taken: set) -> str:
    """A short, unique, letters-only code from the name: V for Viatica, MF for Moses framework."""
    words = re.findall(r"[a-z]+", (name or "").lower()) or ["x"]
    initials = "".join(w[0] for w in words)
    for cand in (initials[:1], initials[:2], words[0][:2], initials[:3], words[0][:3]):
        if cand.upper() not in taken:
            return cand.upper()
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for a in letters:
        for b in letters:
            if initials[0].upper() + a + b not in taken:
                return initials[0].upper() + a + b
    raise ProjectError("ran out of project codes")


def _ref_number(ref, key: str):
    m = REF_RE.match(ref or "")
    return int(m.group(2)) if m and m.group(1).upper() == key else None


def _number(items: list, key: str, start: int) -> int:
    """Give every item without a code the next number. Returns the next number to hand out.

    Starts past both the stored counter and the highest code present, so a number that was ever
    handed out is never handed out again, even after the item holding it was dropped.
    """
    nxt = max([start or 1] + [n + 1 for n in (_ref_number(i.get("ref"), key) for i in items) if n])
    for i in items:
        if not i.get("ref"):
            i["ref"] = f"{key}{nxt}"
            nxt += 1
    return nxt


def ensure_refs(d: dict) -> dict:
    """Give every project a key and every item a code. Idempotent; changes nothing already coded."""
    taken = {p["key"] for p in d.get("projects", []) if p.get("key")} | {INBOX_KEY}
    for p in sorted(d.get("projects", []), key=lambda p: (p.get("rank", 99), p.get("name", ""))):
        if not p.get("key"):
            p["key"] = _derive_key(p.get("name") or p.get("id", ""), taken)
            taken.add(p["key"])
        # Milestones before ideas, so the first run numbers committed work before candidates.
        # After that, a number is simply the order of arrival.
        p["next_ref"] = _number((p.get("milestones") or []) + (p.get("ideas") or []),
                                p["key"], p.get("next_ref", 1))
    if d.get("inbox"):
        d["inbox_next_ref"] = _number(d["inbox"], INBOX_KEY, d.get("inbox_next_ref", 1))
    return d


def _without_refs(proj: dict) -> dict:
    """A project as it would compare with no codes on it. See save(): codes are not news."""
    out = {k: v for k, v in proj.items() if k != "last_change" and k not in REF_FIELDS}
    for lst in ("milestones", "ideas"):
        if lst in out:
            out[lst] = [{k: v for k, v in i.items() if k != "ref"} for i in out[lst] or []]
    return out


def load() -> dict:
    if not REGISTRY.is_file():
        raise ProjectError(f"no registry at {REGISTRY}")
    try:
        d = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ProjectError(f"the registry is not valid JSON ({e}) — fix that before writing to it")
    # Codes are assigned in memory as well as on write, so every reader sees the codes save() will
    # write. The numbering is deterministic, so a page rendered before the first write shows exactly
    # what that write records.
    return ensure_refs(d)


def save(d: dict) -> None:
    """Write through a temp file that is re-parsed before it replaces the real one.

    This registry feeds the status report, the MCP tools and the morning standup. A half-written or
    unparseable file breaks all three at once, and does it quietly — the report just says
    "registry unreadable" and everyone reads past it.
    """
    # WHO CHANGED IT, recorded on every write. The registry is what Moses's own status answers are
    # measured against, which is why its write tools were withheld from him entirely. Brad, 2026-09-03:
    # he should be able to use them when HE directs it. The right answer to "an agent that can edit
    # this can make its own report come true" is not a prohibition, it is evidence — so a status
    # answer can be checked against who actually asked for the change.
    #
    # Deliberately coarse: "mcp" means an agent turn, "cli" means a human at a terminal. The MCP
    # server has no Slack identity to record, so this says WHICH SURFACE, and the channel says who.
    # Claiming more precision than the process actually has would be worse than this.
    ensure_refs(d)
    stamp = {"at": _dt.datetime.now().replace(microsecond=0).isoformat(),
             "by": os.environ.get("MOSES_ACTOR", "cli")}
    d["last_change"] = stamp

    # AND PER PROJECT, worked out by DIFFING rather than by being told. Every mutator would
    # otherwise have to remember to pass its own id, and "remember to" is how the registry got out
    # of step with reality in the first place. Comparing against what is on disk cannot be
    # forgotten by a caller that does not know this exists.
    #
    # This is what `moses-drift`'s work-is-recorded check measures a repository's commits against:
    # a project whose code moved while its entry did not is the estate's most common quiet failure.
    try:
        before = {p["id"]: p for p in load()["projects"]}
    except Exception:                                              # noqa: BLE001
        before = {}                                                # first write, or unreadable
    for proj in d["projects"]:
        was = before.get(proj["id"])
        if was is None:
            proj["last_change"] = stamp                            # newly added
            continue
        # CODES ARE NOT NEWS. Numbering an item is bookkeeping; counting it as a change would mark
        # every project with items as freshly updated the day codes arrived, and moses-drift's
        # "code moved, the registry did not" check would stop seeing projects whose entry is stale.
        if _without_refs(proj) != _without_refs(was):
            proj["last_change"] = stamp
        elif was.get("last_change"):
            proj["last_change"] = was["last_change"]               # unchanged: keep its history
    d["projects"].sort(key=lambda p: (p.get("rank", 99), p.get("name", "")))
    tmp = REGISTRY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        json.loads(tmp.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        tmp.unlink(missing_ok=True)
        raise ProjectError(f"refusing to write — the result would not load ({e})")
    tmp.replace(REGISTRY)


def find(d: dict, pid: str) -> dict:
    pid = (pid or "").strip().lower()
    for p in d["projects"]:
        if p["id"] == pid:
            return p
    # Second pass on the NAME, because someone speaking to Moses says "the whitepaper", not an id.
    for p in d["projects"]:
        if pid and pid in p.get("name", "").lower():
            return p
    known = ", ".join(x["id"] for x in d["projects"])
    raise ProjectError(f"no project matching '{pid}'. Known: {known}")


def _project_for_ref(d: dict, ref) -> dict | None:
    m = REF_RE.match(str(ref or ""))
    if not m:
        return None
    return next((p for p in d["projects"] if p.get("key") == m.group(1).upper()), None)


def _resolve(d: dict, pid: str, which) -> dict:
    """The project an item call is about: the one named, or, when none is, the one its code names."""
    if (pid or "").strip():
        return find(d, pid)
    p = _project_for_ref(d, which)
    if p is None:
        raise ProjectError(f"which project? '{which}' is not a code that names one")
    return p


def _pick(p: dict, lst: str, which, kind: str) -> int:
    """Where an item sits in p[lst] (1-based): found by its code (V14) or, as before, its position."""
    items = p.get(lst) or []
    s = str(which).strip()
    m = REF_RE.match(s)
    if m:
        want = f"{m.group(1).upper()}{int(m.group(2))}"
        if m.group(1).upper() != p.get("key"):
            raise ProjectError(f"{want} is not one of {p['name']}'s items — its code is {p.get('key', '?')}")
        for n, i in enumerate(items, 1):
            if (i.get("ref") or "").upper() == want:
                return n
        other = "ideas" if lst == "milestones" else "milestones"
        if any((i.get("ref") or "").upper() == want for i in p.get(other) or []):
            what = "an idea" if other == "ideas" else "a milestone"
            raise ProjectError(f"{want} is {what} on {p['name']}, not {kind}")
        # A dropped item's number is never handed out again, so this is the honest answer.
        raise ProjectError(f"{p['name']} has no {want} — it may have been dropped")
    try:
        n = int(s)
    except ValueError:
        raise ProjectError(f"'{which}' is neither a code like {p.get('key', 'V')}14 nor a number")
    if not 1 <= n <= len(items):
        raise ProjectError(f"{p['name']} has {len(items)} {lst}; asked for {n}")
    return n


def _pick_inbox(items: list, which) -> int:
    s = str(which).strip()
    m = REF_RE.match(s)
    if m:
        want = f"{m.group(1).upper()}{int(m.group(2))}"
        for n, i in enumerate(items, 1):
            if (i.get("ref") or "").upper() == want:
                return n
        raise ProjectError(f"nothing in the inbox is {want}")
    try:
        n = int(s)
    except ValueError:
        raise ProjectError(f"'{which}' is neither an inbox code like {INBOX_KEY}3 nor a number")
    if not 1 <= n <= len(items):
        raise ProjectError(f"the inbox has {len(items)} item(s); asked for {n}")
    return n


def item(code: str) -> str:
    """What V14 is: its project, which list it is on, where it stands, and its note."""
    d = load()
    m = REF_RE.match(str(code or ""))
    if not m:
        raise ProjectError(f"'{code}' is not a code like V14")
    want = f"{m.group(1).upper()}{int(m.group(2))}"
    note = lambda i: f"\n  {i['note']}" if i.get("note") else ""
    if m.group(1).upper() == INBOX_KEY:
        for i in d.get("inbox") or []:
            if i.get("ref") == want:
                return f"{want} · inbox, no project yet — {i.get('title', '?')}{note(i)}"
        raise ProjectError(f"nothing in the inbox is {want}")
    p = _project_for_ref(d, want)
    if p is None:
        codes = ", ".join(f"{x['key']} {x['name']}" for x in d["projects"] if x.get("key"))
        raise ProjectError(f"no project has the code {m.group(1).upper()}. Codes: {codes}")
    for ms in p.get("milestones") or []:
        if ms.get("ref") == want:
            lane = milestone_lane(ms)
            where = "done" if lane == "done" else MILESTONE_STATE_LABEL[lane].lower()
            return f"{want} · {p['name']} · milestone, {where} — {ms.get('title', '?')}{note(ms)}"
    for i in p.get("ideas") or []:
        if i.get("ref") == want:
            what = "proposal, awaiting your confirm" if i.get("state") == PROPOSED else "idea"
            return f"{want} · {p['name']} · {what} — {i.get('title', '?')}{note(i)}"
    raise ProjectError(f"{p['name']} has no {want} — it may have been dropped")


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    # Short on purpose: the id is typed and read by people, and a 40-character id wrecks every
    # aligned listing it appears in.
    return "-".join(s.split("-")[:3])[:24] or "project"


def _days_to(target: str) -> int | None:
    try:
        return (datetime.strptime(target, "%Y-%m-%d").date() - datetime.now().date()).days
    except (ValueError, TypeError):
        return None


# ── Reading ──────────────────────────────────────────────────────────────────

def _bar(done: int, total: int, width: int = 10) -> str:
    if not total:
        return "─" * width
    filled = round(width * done / total)
    return "█" * filled + "░" * (width - filled)


def dashboard() -> str:
    """Every project, in focus order, as something you can take in at a glance.

    Rendered as text rather than a web page because Brad reads this in the Claude app, in VS Code
    and in Slack — all of which show text and none of which show a locally-hosted page.
    """
    d = load()
    ps = sorted(d["projects"], key=lambda p: (p.get("rank", 99), p.get("name", "")))
    live = [p for p in ps if p.get("rank", 99) < ARCHIVE_RANK]
    archive = [p for p in ps if p.get("rank", 99) >= ARCHIVE_RANK]

    out = [f"PROJECTS — {datetime.now().strftime('%a %d %b %Y, %H:%M')}", ""]

    active = [p for p in live if p.get("status") == "active"]
    if active:
        names = ", ".join(p["name"] for p in active)
        out.append(f"Working on: {names}")
        parked = len([p for p in live if p.get("status") == "parked"])
        if parked:
            out.append(f"{parked} parked behind it.")
    else:
        out.append("Nothing is marked active.")
    out.append("")

    for p in live:
        ms = p.get("milestones") or []
        done = sum(1 for m in ms if m.get("done"))
        rank = p.get("rank", 99)
        flag = "▶" if p.get("status") == "active" else " "
        out.append(f"{flag} #{rank}  {p.get('name','?')} ({p.get('key','?')})   [{p.get('stage','?')}]  ·  {p.get('status','?')}")

        if ms:
            out.append(f"       {_bar(done, len(ms))}  {done}/{len(ms)}")
        left = _days_to(p.get("target", ""))
        if left is not None:
            when = (f"{left} days away" if left > 0
                    else "today" if left == 0 else f"{abs(left)} days OVERDUE")
            out.append(f"       target {p['target']} — {when}")

        if p.get("scope"):
            out.append(f"       {p['scope']}")
        else:
            out.append("       (no scope yet — still just an idea)")
        # A business says who pays, right under its scope. Stored and never shown is paperwork:
        # the answers are only worth collecting if they are in front of whoever is deciding what to
        # work on. An answer that was never given shows as a gap rather than as nothing at all.
        if (p.get("kind") or "").strip().lower() == "business":
            for f, _why in DEMAND_FIELDS:
                out.append(f"       {DEMAND_LABEL[f]}: {(p.get(f) or '').strip() or '— unanswered —'}")
        if p.get("next"):
            out.append(f"       next: {p['next']}")
        for b in p.get("blockers") or []:
            out.append(f"       BLOCKED: {b}")
        # The unfinished milestones are the actionable part; the finished ones are just history.
        for m in [m for m in ms if not m.get("done")][:3]:
            out.append(f"         [ ] {m.get('ref','')} {m.get('title','?')}")
        # Ideas last and marked differently, because a candidate that looks like a commitment is
        # how a wishlist quietly becomes a plan nobody agreed to.
        all_ideas = p.get("ideas") or []
        # A PROPOSAL IS NOT AN IDEA YET. Shown first, marked as waiting on Brad, and never counted in
        # the idea total — the whole failure this replaces was work sitting in a queue nobody read.
        waiting = [i for i in all_ideas if i.get("state") == PROPOSED]
        if waiting:
            out.append(f"       ⏳ AWAITING YOUR CONFIRM ({len(waiting)}):")
            for i in waiting:
                out.append(f"         ? {i.get('ref','')} {i.get('title','?')}   [{i.get('id','')}]")
        ideas = [i for i in all_ideas if i.get("state") != PROPOSED]
        if ideas:
            out.append(f"       ideas ({len(ideas)}, not committed):")
            for i in ideas[:3]:
                out.append(f"         ~ {i.get('ref','')} {i.get('title','?')}")
            if len(ideas) > 3:
                out.append(f"         ~ …and {len(ideas) - 3} more")
        out.append("")

    waiting_all = pending_proposals()
    if waiting_all:
        out.append(f"⏳ {len(waiting_all)} proposal(s) awaiting your confirm — "
                   f"reply `confirm all`, or `confirm <id>`:")
        for i in waiting_all:
            out.append(f"    {i.get('ref','')} [{i.get('id','?')}] {i['projectName']}: {i['title'][:88]}")
        out.append("")

    if archive:
        out.append("Not in the running order:")
        for p in archive:
            out.append(f"    {p.get('name','?')} — {p.get('status','?')}")
    return "\n".join(out).rstrip() + "\n"


def brief() -> str:
    """One line per project. For when the question is 'what have I got', not 'how is it going'."""
    d = load()
    ps = sorted(d["projects"], key=lambda p: (p.get("rank", 99), p.get("name", "")))
    rows = []
    for p in ps:
        ms = p.get("milestones") or []
        done = sum(1 for m in ms if m.get("done"))
        prog = f"{done}/{len(ms)}" if ms else "—"
        # Appended to the progress column rather than mixed into it: +3 is three things being
        # considered, and must never be mistaken for three things outstanding.
        all_ideas = p.get("ideas") or []
        n_ideas = sum(1 for i in all_ideas if i.get("state") != PROPOSED)
        if n_ideas:
            prog = f"{prog}+{n_ideas}"
        # Waiting-on-Brad gets its own marker. Folding it into the idea count would hide the one
        # number that means somebody has to DO something.
        n_wait = sum(1 for i in all_ideas if i.get("state") == PROPOSED)
        if n_wait:
            prog = f"{prog}?{n_wait}"
        rank = f"#{p['rank']}" if p.get("rank", 99) < ARCHIVE_RANK else "  "
        pid = p["id"] if len(p["id"]) <= 18 else p["id"][:17] + "…"
        rows.append(f"{rank:>4}  {p.get('key',''):<3} {pid:<18} {p.get('status',''):<9} {p.get('stage',''):<11} "
                    f"{prog:>5}  {p.get('target') or '—':<11} {p.get('name','')}")
    return "\n".join(rows)


def show(pid: str) -> str:
    return json.dumps(find(load(), pid), indent=2, ensure_ascii=False)


# ── Writing ──────────────────────────────────────────────────────────────────

def add(name: str, rank: int | None = None, scope: str = "", status: str = "parked") -> str:
    name = (name or "").strip()
    if not name:
        raise ProjectError("a project needs a name")
    d = load()
    pid = _slug(name)
    if any(x["id"] == pid for x in d["projects"]):
        raise ProjectError(f"'{pid}' already exists — use project_update to change it")
    if rank is None:
        ranked = [x.get("rank", 0) for x in d["projects"] if x.get("rank", 99) < ARCHIVE_RANK]
        rank = (max(ranked) + 1) if ranked else 1
    else:
        # Rank is an ORDER. Inserting at 3 pushes the old 3 down, or the registry ends up with two
        # projects both claiming to be third and no way to tell which Brad meant.
        for x in d["projects"]:
            if ARCHIVE_RANK > x.get("rank", 99) >= rank:
                x["rank"] += 1
    entry = {
        "id": pid, "name": name, "rank": rank,
        "status": status, "stage": "scope" if scope else "idea", "target": "",
        "phase": "idea", "focus": "", "next": "", "scope": scope,
        "milestones": [], "blockers": [], "repo": "", "deploy": "",
        "keywords": re.findall(r"[a-z0-9]{4,}", name.lower())[:6],
        "memory_prefix": f"project_{pid.replace('-', '_')}",
    }
    d["projects"].append(entry)
    save(d)
    # Read from the entry, not from the list: save() re-sorts, so d["projects"][-1] is whichever
    # project sorts last — which reported the wrong stage back to whoever just added one.
    return f"Added '{name}' as #{rank} ({entry['stage']}, {status}). id: {pid}"


def update(pid: str, field: str, value: str) -> str:
    if field not in SETTABLE:
        raise ProjectError(f"cannot set '{field}'. One of: {', '.join(SETTABLE)}")
    d = load()
    p = find(d, pid)
    if field == "stage" and value not in STAGES:
        raise ProjectError(f"stage must be one of: {', '.join(STAGES)}")
    if field == "status" and value not in STATUSES:
        raise ProjectError(f"status must be one of: {', '.join(STATUSES)}")
    if field == "kind" and value and value.strip().lower() not in KINDS:
        raise ProjectError(f"kind must be one of: {', '.join(KINDS)}")
    # Only a genuine TRANSITION is gated. Re-stating a stage a project already holds asks nobody
    # anything new, and a guard that fires on a no-op is the kind people learn to talk past.
    if field == "stage" and value in ("building", "shipped") and p.get("stage") != value:
        assert_may_build(p)
    if field == "target" and value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ProjectError("target must be YYYY-MM-DD, or empty to clear it")
    if field == "rank":
        try:
            want = int(value)
        except ValueError:
            raise ProjectError("rank must be a number")
        for x in d["projects"]:
            if x["id"] != p["id"] and ARCHIVE_RANK > x.get("rank", 99) >= want:
                x["rank"] += 1
        p["rank"] = want
    else:
        p[field] = value
        # Writing a scope is what turns an idea into something defined; nobody should have to
        # remember to move the stage by hand afterward.
        if field == "scope" and value and p.get("stage") == "idea":
            p["stage"] = "scope"
    save(d)
    return f"{p['name']}: {field} = {value or '(cleared)'}"


def add_milestone(pid: str, title: str) -> str:
    title = (title or "").strip()
    if not title:
        raise ProjectError("a milestone needs a title")
    d = load()
    p = find(d, pid)
    p.setdefault("milestones", []).append({"title": title, "done": False})
    if p.get("stage") in ("idea", "scope"):
        p["stage"] = "milestones"
    save(d)
    return f"{p['name']}: milestone {p['milestones'][-1]['ref']} — {title}  (stage: {p['stage']})"


# ── Ideas: candidates, not commitments ───────────────────────────────────────
#
# A shipped project still has a future, and until now there was nowhere to put it. Viatica reached
# 12/12 milestones and stage `shipped`, so everything anyone thought of next lived in a plan file
# that the next plan overwrote.
#
# KEPT OUT OF THE MILESTONE COUNT ON PURPOSE, and that separation is the whole reason for this.
# "12/12" means "we finished what we set out to do"; the moment it also counts things nobody has
# promised, the one number the dashboard shows stops meaning anything. An idea is a candidate. It
# becomes a milestone by being promoted, which is a decision somebody makes.
#
# This does NOT replace `add_idea` in the MCP server, which writes to ideas.md and links each new
# idea to prior work across every project — that cross-project discovery is the point of it and is
# worth keeping. This is the per-project shortlist; the MCP tool feeds both.

# THE THREE STATES A PIECE OF WORK CAN BE IN, on one list rather than in three places.
#
#   proposed  — Moses suggested it; it is waiting for Brad to confirm or dismiss
#   idea      — accepted as a candidate; worth doing, not committed to a release
#   (milestone lives in its own list: committed work, and the only thing in the ratio)
#
# Proposals used to live in their own `proposals.json` under MOSES_STATE — which resolves
# differently for the root service (/var/lib/moses) and for anything run as brad
# (~/.local/state/moses). Two stores existed and one went unread for 17 days, holding a Stripe
# go-live checklist filed five days before Viatica took real money. A proposal is an unconfirmed
# idea, so it is now a STATE rather than a place, and the registry is the single source of truth.
PROPOSED = "proposed"
ACCEPTED = "idea"


def add_idea(pid: str, title: str, note: str = "", state: str = ACCEPTED, **meta) -> str:
    title = (title or "").strip()
    if not title:
        raise ProjectError("an idea needs a title")
    if state not in (PROPOSED, ACCEPTED):
        raise ProjectError(f"unknown state {state!r}")
    d = load()
    p = find(d, pid)
    entry = {"title": title, "addedAt": datetime.now().strftime("%Y-%m-%d"), "state": state}
    if (note or "").strip():
        entry["note"] = note.strip()
    # Provenance for a proposal: which channel it was made in, which message carried it, who asked.
    # Kept because a reaction on that message is one of the ways Brad confirms, and because knowing
    # where a piece of work came from is worth more than a tidy record.
    for k in ("id", "channel", "msg_ts", "by", "at", "related"):
        if meta.get(k):
            entry[k] = meta[k]
    p.setdefault("ideas", []).append(entry)
    save(d)
    # Deliberately does NOT move the stage. An idea for a shipped product does not make it unshipped,
    # and an idea for an idea-stage project has not scoped it.
    return f"{p['name']}: idea {entry['ref']} — {title}"


def propose(pid: str, title: str, note: str = "", **meta) -> str:
    """Suggest work against a project. It is NOT filed until Brad confirms it."""
    return add_idea(pid, title, note, state=PROPOSED, **meta)


def pending_proposals() -> list[dict]:
    """Every unconfirmed proposal, across every project, newest last.

    ONE QUERY, ONE STORE. The whole point: there is no second place a proposal can hide.
    """
    out = []
    for p in load().get("projects", []):
        for n, i in enumerate(p.get("ideas") or [], 1):
            if i.get("state") == PROPOSED:
                out.append(dict(i, project=p["id"], projectName=p["name"], number=n))
    return out


def find_proposal(key: str) -> dict | None:
    """Look one up by its short id or by the Slack message that carried it."""
    key = (key or "").strip()
    if not key:
        return None
    for i in pending_proposals():
        if i.get("id") == key or i.get("msg_ts") == key or (i.get("ref") or "").upper() == key.upper():
            return i
    return None


def accept_proposal(pid: str, n) -> str:
    """Confirm: proposed -> idea. The one act that turns a suggestion into filed work.

    `n` is the item's code (V14) or, as before, its position among the project's ideas."""
    d = load()
    p = _resolve(d, pid, n)
    k = _pick(p, "ideas", n, "an idea")         # before touching the list: it may not exist
    it = p["ideas"][k - 1]
    if it.get("state") != PROPOSED:
        raise ProjectError(f"{it.get('ref') or n} on {p['name']} is not awaiting confirmation")
    it["state"] = ACCEPTED
    it["acceptedAt"] = datetime.now().strftime("%Y-%m-%d")
    save(d)
    return f"{p['name']}: {it['ref']} {it['title']}"


# ── The inbox: an idea that does not belong to a project yet ─────────────────
#
# ideas.md was the cross-project capture file. It died quietly — 2 entries, untouched from
# 2026-08-11 to 2026-09-09, while the memory index still advertised it as the front door. Two idea
# stores, one abandoned, is the failure [[project_moses_one_store]] already recorded once when a
# Stripe checklist went missing for 17 days.
#
# So capture now lands HERE, inside the registry the Projects page renders, and an idea with no
# project is a first-class thing rather than a reason to refuse. Refusing would be worse: "which
# project?" at the moment somebody has a thought is how the thought gets lost, and the whole point
# of capture is that it costs nothing.
def add_inbox_idea(title: str, note: str = "") -> str:
    title = (title or "").strip()
    if len(title) < 3:
        raise ProjectError("give me something to write down")
    d = load()
    inbox = d.setdefault("inbox", [])
    if any(title.lower() == (i.get("title") or "").lower() for i in inbox):
        return "Already in the inbox — not adding it twice."
    inbox.append({"title": title, "note": (note or "").strip(),
                  "addedAt": _dt.date.today().isoformat()})
    save(d)
    return f"Filed to the inbox — unassigned. {len(inbox)} waiting for a project."


def inbox(d: dict | None = None) -> list[dict]:
    return (d or load()).get("inbox") or []


def claim_inbox_idea(n, pid: str) -> str:
    """Move an inbox idea onto a project, which is where it can actually become work.

    `n` is its inbox code (IN3) or its position. It takes the project's next number; the inbox code
    retires with the inbox entry."""
    d = load()
    items = d.setdefault("inbox", [])
    k = _pick_inbox(items, n)
    p = find(d, pid)
    got = items.pop(k - 1)
    entry = {"title": got["title"], "note": got.get("note", ""), "addedAt": _dt.date.today().isoformat()}
    p.setdefault("ideas", []).append(entry)
    save(d)
    return (f"{p['name']}: idea {entry['ref']} — {got['title'][:70]}  "
            f"(was {got.get('ref') or 'unnumbered'}; {len(items)} left in the inbox)")


def remove_idea(pid: str, n) -> str:
    d = load()
    p = _resolve(d, pid, n)
    k = _pick(p, "ideas", n, "an idea")
    gone = p["ideas"].pop(k - 1)
    save(d)
    return f"{p['name']}: dropped idea {gone.get('ref', '')} — {gone.get('title','?')}"


def promote_idea(pid: str, n) -> str:
    """An idea becomes a committed milestone, carrying its note so the reasoning survives the move,
    and its code, so V14 still finds it."""
    d = load()
    p = _resolve(d, pid, n)
    k = _pick(p, "ideas", n, "an idea")
    ideas = p["ideas"]
    idea = ideas.pop(k - 1)
    ms = p.setdefault("milestones", [])
    entry = {"title": idea["title"], "done": False, "ref": idea.get("ref")}
    if idea.get("note"):
        entry["note"] = idea["note"]
    ms.append(entry)
    if p.get("stage") in ("idea", "scope"):
        p["stage"] = "milestones"
    save(d)
    return (f"{p['name']}: {entry['ref']} idea → milestone — {idea['title']} "
            f"({len(ideas)} idea(s) left)")


def set_milestone_state(pid: str, n, state: str) -> str:
    """Move a milestone between next / building / early access.

    Finishing it is `complete_milestone`; this is for everything before that.
    """
    state = (state or "").strip().lower()
    if state not in MILESTONE_STATES:
        raise ProjectError(f"state must be one of {', '.join(MILESTONE_STATES)} — got '{state}'")
    d = load()
    p = _resolve(d, pid, n)
    k = _pick(p, "milestones", n, "a milestone")
    m = p["milestones"][k - 1]
    if m.get("done"):
        raise ProjectError(f"{p['name']} {m.get('ref') or n} is already done — undo it first if it is not")
    # Marking a milestone "building" IS starting to build, so it is the same door and carries the
    # same gate. Checked BEFORE the milestone is touched: a refusal must leave nothing half-changed.
    if state == "building" and p.get("stage") in ("idea", "scope", "milestones"):
        assert_may_build(p)
        p["stage"] = "building"
    m["state"] = state
    save(d)
    return f"{p['name']}: {m['ref']} {m['title'][:60]} — {MILESTONE_STATE_LABEL[state]}"


def complete_milestone(pid: str, n, undo: bool = False) -> str:
    d = load()
    p = _resolve(d, pid, n)
    k = _pick(p, "milestones", n, "a milestone")
    ms = p["milestones"]
    m = ms[k - 1]
    m["done"] = not undo
    # Finishing something clears where it WAS. A done milestone still carrying state="building"
    # would sit in two columns at once on the board.
    if not undo:
        m.pop("state", None)
    left = sum(1 for x in ms if not x.get("done"))
    save(d)
    tail = "" if left else "  — every milestone is complete."
    return f"{p['name']}: {m['ref']} {m['title']} — {'not done' if undo else 'done'} ({left} left){tail}"


def remove(pid: str) -> str:
    d = load()
    p = find(d, pid)
    d["projects"] = [x for x in d["projects"] if x["id"] != p["id"]]
    save(d)
    return f"Removed '{p['name']}'."
