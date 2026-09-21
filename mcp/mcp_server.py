#!/usr/bin/env python3
"""Moses as an MCP server — Brad's own Claude app is the model, so Reserve pays nothing.

WHY THIS EXISTS
Moses used to answer Slack questions by calling the Anthropic API from Reserve. That is
pay-per-token and not covered by a Pro subscription, so Brad turned it off (2026-08-05). This
inverts the arrangement: his Claude app (iOS or desktop) does the reasoning on his subscription,
and Moses is the tool layer it calls. Same capability, no API bill, and it works from his phone.

THE SAFETY MODEL IS THE TOOL LIST
There is no bash tool here and there never should be. An MCP server's blast radius is exactly the
set of tools it exposes, so safety comes from what is *absent*: no shell, no arbitrary paths, no
destructive operations. Every tool below is read-only except three that append to the todo/idea
files. That is a smaller and far more auditable surface than a general-purpose agent behind a
permission gate — which is why this direction is better than the one it replaced, not a fallback.

Memory reads are confined to the corpus directory and the filename is sanitized to a bare stem, so
a path cannot escape it however it is spelled.

RUNS AS `brad`, NOT ROOT. It only needs to read the memory corpus (brad owns it) and run the
read-only `moses`/`birdeye` queries. Nothing here needs privilege, so it doesn't get any.
"""

from __future__ import annotations

import sys as _sys, pathlib as _pl  # noqa: E401
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent / "agent" / "lib"))
import moses_env as _env  # noqa: E402 — whose home, where Moses lives, the operator's settings
import os
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from mcp.server.mcpserver import MCPServer

# ONE COPY OF EACH MODULE, WHEREVER IT LIVES.
#
# project_status.py sits in the agent tree because `moses projects` runs it there as a script, and
# the `moses` CLI is root-owned — so pointing that at a different path needs a privileged install,
# while this line needs nothing. Both callers now read the same file.
#
# This is not fussiness. Four modules existed in both trees; three were byte-identical and one,
# mcp_server.py itself, had drifted 393 lines apart. Reading the stale copy is how this server was
# twice described as serving tools it had served for weeks — the source said 16, the process served
# 34, and nothing reconciled them. `moses-drift` now fails on any duplicate, so the rule is checked
# rather than remembered.
# Everything this server writes is stamped as coming from an agent turn, not a terminal.
# See projects.save(): the registry write tools are granted to Moses on human-directed
# turns, and the answer to "an agent that can edit this can make its own report come true"
# is evidence rather than prohibition.
os.environ.setdefault("MOSES_ACTOR", "mcp")

AGENT_DIR = os.environ.get("MOSES_AGENT_DIR", str(_env.ROOT / "agent"))
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

MEMORY = Path(os.environ.get("MOSES_MEMORY_DIR") or _env.MEMORY)
MOSES_BIN = "/usr/local/bin/moses"
BIRDEYE_BIN = "/usr/local/bin/birdeye"
BIRDEYE_REPORT_BIN = "/usr/local/bin/birdeye-report"
# What the root-run timer last posted to #ops. Read, never written, by this service.
BIRDEYE_CACHE = "/var/lib/birdeye/last-report.txt"
KNIGHT_BIN = str(_env.ROOT / "knight/bin/knight")
# Finishing a branch-mode job: merge it and make it live. A separate entry point from `knight`
# because it is a different decision — the builder gate asks "is this good", this asks "is this
# going in" — and because the tool list IS the blast radius, so the thing that can merge Brad's
# code is named and reviewable rather than another verb hidden inside an existing tool.
KNIGHT_LAND = str(_env.ROOT / "knight/bin/knight-land")
ZRYACHIY_BIN = str(_env.ROOT / "knight/bin/zryachiy")
JOB_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9]+$")

server = MCPServer(
    name="moses",
    title="Moses — Brad's project-of-projects",
    instructions=(
        "Moses holds Brad's commandments, the state of every project, and the roster of who is "
        "accountable for what across Reserve and Viatica. Viatica is his travel-itinerary product; "
        "Reserve is the server everything runs on. Assume any project, tool or term he names is "
        "recorded here — the corpus is the source of truth for his work.\n\n"
        "HOW TO LOOK THINGS UP. Start with list_memory: its one-line descriptions usually identify "
        "the right file outright, and it costs one call. search_memory is LITERAL substring matching "
        "over notes written in shorthand, so a user's phrasing frequently misses — '25km' not "
        "'twenty five kilometers', 'iDrive' not 'the backup service'. **Never conclude a topic is "
        "unknown after one failed search.** Search a short distinctive word, or list_memory and read "
        "the file whose description fits. Saying 'I have nothing about Viatica' when 40 lines mention "
        "it is the failure to avoid.\n\n"
        "Once you have the file, read_memory it in full before answering. The notes record WHY "
        "things are the way they are, which is usually what Brad is actually asking.\n\n"
        "If something genuinely is not written down, say so plainly rather than guessing — a "
        "confident wrong answer about project state is worse than 'that's not recorded' (his "
        "commandment #12). But check properly first: that rule is about not inventing facts, not "
        "about giving up early.\n\n"
        "For 'who owns X' or 'is anyone behind', use get_roster and who_is_behind — those are live, "
        "while memory reflects what was true when written."
    ),
)


def _run(argv: list[str], limit: int = 8000) -> str:
    """Run a fixed, whitelisted command. Nothing from a tool argument ever reaches a shell."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return f"(could not run {argv[0]}: {type(e).__name__})"
    out = ((p.stdout or "") + (p.stderr or "")).strip() or "(no output)"
    return out[-limit:] if len(out) > limit else out


def _safe_memory_path(name: str) -> Path | None:
    """Resolve a memory name to a file inside the corpus, or None.

    Reduced to a bare stem first, so `../../etc/shadow`, an absolute path, or a URL-encoded
    traversal all collapse to something harmless before the path is built. Then the resolved path
    is checked to be inside the corpus — belt and braces, because path handling is exactly where a
    "surely that can't happen" assumption turns into a file read.
    """
    stem = os.path.basename(name.strip()).removesuffix(".md")
    if not stem or not re.fullmatch(r"[A-Za-z0-9._-]+", stem):
        return None
    candidate = (MEMORY / f"{stem}.md").resolve()
    try:
        candidate.relative_to(MEMORY.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


# ── Knowledge: the memory corpus ─────────────────────────────────────────────
@server.tool(
    description="List every memory file with its one-line description. Start here to see what Moses "
                "knows before reading anything in full."
)
def list_memory() -> str:
    if not MEMORY.is_dir():
        return f"No memory corpus at {MEMORY}."
    rows = []
    for p in sorted(MEMORY.glob("*.md")):
        desc = ""
        try:
            for line in p.read_text(encoding="utf-8").splitlines()[:12]:
                if line.startswith("description:"):
                    desc = line.split(":", 1)[1].strip().strip('"'); break
        except OSError:
            desc = "(unreadable)"
        rows.append(f"{p.stem} — {desc}" if desc else p.stem)
    return f"{len(rows)} memory files:\n" + "\n".join(f"  {r}" for r in rows)


@server.tool(
    description="Read one memory file in full, by name (e.g. 'project_moses' or 'reserve-drive-layout'). "
                "Use list_memory or search_memory first to find the right name."
)
def read_memory(name: str) -> str:
    p = _safe_memory_path(name)
    if not p:
        return f"No memory called {name!r}. Use list_memory to see what exists."
    try:
        return p.read_text(encoding="utf-8")
    except OSError as e:
        return f"Could not read {name}: {e}"


# Brad speaks numbers as words; the notes write digits ("25km", "8765", "02:30"). Without this,
# "the twenty five kilometer rule" has no token in common with the file that documents it, and the
# search honestly reports nothing while the answer sits in the corpus.
_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
          "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
          "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}


def _number_words_to_digits(q: str) -> list[str]:
    """Extra search terms for any number spelled out in the query ('twenty five' -> '25', '20')."""
    toks = re.findall(r"[a-z]+", q.lower())
    extra: list[str] = []
    i = 0
    while i < len(toks):
        if toks[i] in _TENS:
            n = _TENS[toks[i]]
            if i + 1 < len(toks) and toks[i + 1] in _UNITS and _UNITS[toks[i + 1]] < 10:
                extra.append(str(n + _UNITS[toks[i + 1]]))
                i += 1
            extra.append(str(n))
        elif toks[i] in _UNITS:
            extra.append(str(_UNITS[toks[i]]))
        i += 1
    return list(dict.fromkeys(extra))


_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "is", "it", "its", "was", "why",
    "what", "how", "does", "do", "did", "with", "that", "this", "about", "regarding", "rule",
}


@server.tool(
    description=(
        "Search the memory corpus and return matching lines with their file. Matching is LITERAL "
        "substring, so a user's exact phrasing often misses: the notes write numbers as digits and "
        "use shorthand ('25km', not 'twenty five kilometers'; 'iDrive', not 'the backup service'). "
        "If the whole phrase finds nothing this falls back to matching individual words and tells "
        "you so. When a search comes back empty, call list_memory — its one-line descriptions "
        "usually identify the right file directly — rather than concluding the topic is unknown."
    )
)
def search_memory(query: str, max_results: int = 40) -> str:
    if not MEMORY.is_dir():
        return f"No memory corpus at {MEMORY}."
    q = query.strip()
    if len(q) < 2:
        return "Give me at least two characters to search for."

    files = sorted(MEMORY.glob("*.md"))

    def scan(needle: str) -> list[str]:
        found: list[str] = []
        for p in files:
            try:
                for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                    if needle.lower() in line.lower():
                        found.append(f"{p.stem}:{i}: {line.strip()[:200]}")
                        if len(found) >= max_results:
                            return found
            except OSError:
                continue
        return found

    hits = scan(q)
    if hits:
        return f"{len(hits)} match(es) for {q!r}:\n" + "\n".join(hits)

    # Phrase missed. Fall back to the distinctive words in it, because a model will naturally paste
    # the user's wording and the corpus is written in shorthand. Returning "nothing found" on a
    # single literal miss is what made Moses claim he had never heard of Viatica.
    words = [w for w in re.findall(r"[A-Za-z0-9_.-]{3,}", q) if w.lower() not in _STOPWORDS]
    # Digit forms first — they are the most likely to be what the notes actually say.
    words = _number_words_to_digits(q) + words
    per_word: list[tuple[str, list[str]]] = []
    for w in words:
        got = scan(w)
        if got:
            per_word.append((w, got))

    if not per_word:
        return (f"Nothing matches {q!r}, and none of its words appear either "
                f"({', '.join(words) or 'no searchable words'}). Call list_memory — the descriptions "
                f"there will tell you whether this topic is recorded under different wording.")

    per_word.sort(key=lambda t: len(t[1]), reverse=True)
    out = [f"No line contains the whole phrase {q!r}. Matching individual words instead — "
           f"the notes may use different wording (numbers as digits, shorthand)."]
    budget = max_results
    for w, got in per_word:
        take = got[: max(2, budget // max(1, len(per_word)))]
        budget -= len(take)
        out.append(f"\n{w!r} — {len(got)} match(es):")
        out.extend("  " + h for h in take)
        if budget <= 0:
            break
    return "\n".join(out)


# ── Live state: roster, accountability, ops ──────────────────────────────────
@server.tool(
    description="The roster: every agent and job, what each is accountable for, what each explicitly "
                "does NOT own, and when it last reported. This is live — prefer it over memory for "
                "ownership questions."
)
def get_roster() -> str:
    return _run([MOSES_BIN, "roster"])


@server.tool(
    description="Who is overdue against their declared cadence. Empty means everyone is inside it. "
                "Silence from a reporter is a failure, not an absence of one — this is how it surfaces."
)
def who_is_behind() -> str:
    return _run([MOSES_BIN, "status"])


# ── The personas, on demand ──────────────────────────────────────────────────
# Namespaced by owner, matching the roster. Each is the READ half of a persona whose scheduled
# Slack report keeps running untouched on its own timer — asking a question here posts nothing.
@server.tool(
    description="BIRDEYE (ops): Reserve's disk headroom on every mounted drive, plus any long job "
                "handed off to him. Read-only — this does not post to Slack."
)
def birdeye_status() -> str:
    disks = _run(["df", "-h", "--output=target,pcent,avail"], limit=1500)
    jobs = _run([BIRDEYE_BIN, "list"], limit=3000)
    return f"DISKS\n{disks}\n\nHANDED-OFF JOBS\n{jobs}"


@server.tool(
    description="BIRDEYE (ops): his full ops report — backup liveness and staleness, iDrive cloud "
                "quota, disk alarms including drives declared in fstab but NOT mounted, and failed "
                "jobs. This is the same assessment as the 06:00 Slack post, rendered rather than "
                "posted. Use it for 'did the backup run', 'is anything wrong with Reserve'. "
                "mode: morning (issues only) | full (everything) | backup (backup alone)."
)
def birdeye_report(mode: str = "full") -> str:
    if mode not in ("morning", "full", "backup"):
        return "mode must be morning, full or backup."

    # THIS TOOL DOES NOT RUN birdeye-report. It reads what the root-run timer last produced.
    #
    # Two things forced that, and both are worth keeping in mind before "improving" it:
    #
    # 1. Running it from here would POST. An earlier version passed --print and inspected the output
    #    for a usage error; it never got one, because the script ignored the unknown flag, built the
    #    report and called `birdeye say`. The only reason #ops wasn't spammed is that this service
    #    runs as brad and cannot read the root-owned Slack token — an accident, not a safeguard.
    # 2. Running it as brad gives WRONG ANSWERS. It cannot read the iDrive profile directory, so it
    #    reports "no job summary found" — a false alarm about the single thing Birdeye exists to
    #    watch. A monitor that cries wolf gets ignored, which is worse than having no monitor.
    #
    # Granting brad sudo on the binary would fix both and was the first plan. A cache is strictly
    # less privilege for the same answer: this is the exact text Slack received, so the tool cannot
    # disagree with the channel. The cost is freshness, which is stated rather than hidden — and
    # birdeye_status covers the live picture.
    cache = Path(BIRDEYE_CACHE)
    if not cache.is_file():
        return (f"No cached ops report at {cache}. Either birdeye-report has not run since the "
                f"caching change, or install-persona-tools.sh has not been run yet. "
                f"birdeye_status gives the live disk and job picture in the meantime.")
    try:
        text = cache.read_text(encoding="utf-8", errors="replace")
        age_min = int((time.time() - cache.stat().st_mtime) / 60)
    except OSError as e:
        return f"Could not read the cached ops report: {e}"

    stamp = (f"{age_min} min ago" if age_min < 90
             else f"{age_min // 60}h {age_min % 60}m ago")
    warn = ""
    if age_min > 60 * 26:
        # The morning run is daily. Past ~26h it has missed one, and a stale report presented as
        # current is exactly the silent-monitoring failure this system keeps running into.
        warn = ("\n\n⚠️  This is more than a day old, so Birdeye's scheduled run has MISSED at least "
                "once. Treat the content as history, and check why the timer did not fire.")
    return (f"Birdeye's last ops report (as posted to #ops, {stamp}):\n"
            f"Requested mode {mode!r}; the cache holds whichever mode ran last.\n\n{text}{warn}")


@server.tool(
    description="BIG PIPE (finance): Viatica's live P&L from Customs. period: ytd (default, the "
                "standing year-to-date profit and loss with cost lines) | daily | weekly | monthly "
                "| quarterly. Income is live Stripe; expenses are the recorded recurring costs. "
                "Read-only — this does NOT post his scheduled report to #finance."
)
def bigpipe_pnl(period: str = "ytd") -> str:
    try:
        import personas
        return personas.cfo(period)
    except Exception as e:
        # Financial figures: report the failure, never a zero that reads like a fact.
        return f"Big Pipe could not answer: {e}"


@server.tool(
    description="TAGILLA (support): Viatica's support queue from Customs — what came in over the "
                "window, how much the AI answered, and the backlog still waiting on Brad, oldest "
                "first. hours defaults to 24, max 168. Read-only — posts nothing to #support."
)
def tagilla_queue(hours: int = 24) -> str:
    try:
        import personas
        return personas.support(hours)
    except Exception as e:
        return f"Tagilla could not answer: {e}"


@server.tool(
    description="KNIGHT (software development): hand him a build task on one of the repositories in "
                "his target registry. `target` picks which: 'viatica' (default) is the product; "
                "'moses' is the agent, the MCP server and Knight himself; 'moses-framework' is the "
                "shareable half at github.com/bradwallen/Moses. Any other name is refused and the "
                "refusal lists the real ones — use knight_targets if you are unsure. "
                "WHERE THE WORK LANDS DEPENDS ON THE TARGET: on viatica a green gate pushes to "
                "master and Customs ships it, so THAT ONE DEPLOYS; on moses and moses-framework a "
                "green gate pushes a branch for a human to merge and nothing goes live, because "
                "those repositories are the machinery that runs Knight. The gate is objective and "
                "run by the runner, never claimed by Knight, and red means nothing is pushed. The "
                "AGENT never holds push rights — it works in an isolated clone with no remote and "
                "no access outside its own directory. Returns a job id immediately; the work runs "
                "up to 60 minutes in the background, one job at a time, 8 per day. Brief him like a "
                "senior developer: what to build and how to verify it. Knight posts his own report "
                "to #viatica-dev when he finishes. "
                "`acceptance` IS REQUIRED AND IT DOES NOT GO TO KNIGHT — it goes to Zryachiy, who "
                "reviews the finished diff and BLOCKS THE PUSH if the work does not meet it. Write "
                "what must be TRUE when this is done, as observable outcomes someone could check "
                "without reading the code, one per line. Not a restatement of the task: the task is "
                "how to build it, the acceptance is how anyone knows it was built. You are writing "
                "MARK ANYTHING ONLY AN ADMIN CAN SEE with a leading [admin] — you cannot reach "
                "those after deploy (an admin page answers you with a redirect to /login), so they "
                "become Brad's to confirm and the request stays open until he does. "
                "it BEFORE the code exists, which is the only reason it can judge anything — "
                "criteria derived from a finished diff just describe whatever happened. If Brad was "
                "vague, say what you are assuming in your reply so he can correct it before the "
                "build finishes."
)
def knight_start(task: str, acceptance: str, target: str = "viatica") -> str:
    task = task.strip()
    acceptance = (acceptance or "").strip()
    if len(task) < 15:
        return ("Give Knight a real brief — what to build and how to check it. A one-word task "
                "produces a branch with nothing on it.")
    # REFUSED HERE, NOT SKIPPED DOWNSTREAM. Without criteria the reviewer can check whether the code
    # is correct and never whether it is what Brad asked for — which is the gap this whole change
    # exists to close, and the one no test in the pipeline can see.
    if len(acceptance) < 15:
        return ("Knight needs acceptance criteria before he starts — what must be TRUE when this is "
                "done, in terms someone could check without reading the diff. They go to the "
                "reviewer, not to Knight, and a criterion he cannot meet blocks the push.\n\n"
                "  weak:   'remove the Labs entry'\n"
                "  usable: 'the Labs/bcmdisplay host is absent from the hardware registry; the "
                "registry still parses; no other host entry changed'")
    out = _run([KNIGHT_BIN, "start", "--target", target.strip() or "viatica",
                "--acceptance", acceptance, task], limit=3000)
    if "disabled" in out.lower():
        return f"{out}\n\nKnight is switched off. Brad can re-enable him with `knight enable`."
    return out


@server.tool(
    description="WHAT IS STILL UNVERIFIED — the acceptance criteria of finished Knight jobs that "
                "nobody has confirmed against the LIVE product yet. A deploy that landed is not a "
                "request that is done: Zryachiy judged the criteria against the DIFF, and this is "
                "the list of what still needs checking against the running site. Each row says who "
                "can check it — 'moses' means you can reach it yourself (fetch the page or the API "
                "and look), 'brad' means it is admin-gated and only he can see it. Call this with no "
                "job id for everything still open. Use it when asked what is outstanding, before "
                "claiming a change is finished, and in the standup."
)
def verification_open(job_id: str = "") -> str:
    import verification as V
    return V.report(job_id.strip() or None)


@server.tool(
    description="RECORD A VERIFICATION as done. `n` is the criterion number from verification_open. "
                "`by` is who actually confirmed it — 'moses' if YOU checked the live product this "
                "turn, 'brad' if he told you he looked. `note` is what you or he actually saw, in "
                "one line: it is the only evidence anyone will have later, so 'the marker line is "
                "present in the /admin header' rather than 'confirmed'. "
                "NEVER record a criterion you have not actually checked, and never record Brad's on "
                "his behalf unless he said in this conversation that he verified it — a ledger that "
                "records unchecked rows is worse than no ledger, because it closes the request. A "
                "job is only done when every row is verified."
)
def verification_record(job_id: str, n: int, by: str, note: str) -> str:
    import verification as V
    return V.record(job_id.strip(), int(n), by.strip().lower(), note.strip())


@server.tool(
    description="VIATICA INCIDENTS: what the product itself noticed going wrong for real people — "
                "browser crashes, failed requests, server actions that threw — as opposed to what "
                "somebody wrote in about (that is tagilla_queue). Read live from the app every time, "
                "never from memory. Returns each incident's ID, which is what resolve_incident needs. "
                "Use for 'what's broken', 'any incidents', 'is anything failing for customers'. Pass "
                "state='all' to include ones already closed."
)
def viatica_incidents(state: str = "open", limit: int = 20) -> str:
    import viatica_incidents as vi
    return vi.queue(state=state, limit=limit)


@server.tool(
    description="VIATICA INCIDENTS: mark one investigated (or reopen it with reopen=true). Takes the "
                "id from viatica_incidents. THIS WRITES — only do it when Brad has asked, or when the "
                "underlying bug is genuinely fixed and shipped; put what was found in `note`, because "
                "the next person to see this row will only have that sentence. Your name goes on it, "
                "so the queue never implies a person looked. An incident reopens by itself if anyone "
                "hits the bug again."
)
def resolve_incident(incident_id: str, note: str = "", reopen: bool = False) -> str:
    import viatica_incidents as vi
    return vi.resolve(incident_id, note=note, by="moses", reopen=reopen)


@server.tool(
    description="KNIGHT: which repositories Knight is allowed to work on, what the gate is for each, "
                "and where green work lands — straight to the mainline, or onto a branch for a human "
                "to merge. Read this before guessing a target name."
)
def knight_targets() -> str:
    return _run([KNIGHT_BIN, "targets"], limit=3000)


@server.tool(
    description="KNIGHT: how a build job is going, or how it ended — status, elapsed time, the "
                "branch, commit count, diff size, and Knight's own written report. Use the job id "
                "from knight_start. Knight also posts this report to #viatica-dev when he finishes; "
                "this is the way to read it without leaving the conversation."
)
def knight_status(job_id: str) -> str:
    # The states a finished job can be in, and what each ACTUALLY means, because "pushed" used to be
    # the end of the story and it is not: live = verified answering; rolled-back = it broke and was
    # reverted, so nothing is fixed; deploy-broken = broken AND still live, which is an emergency;
    # deploy-unverified = pushed and nobody could confirm what happened, which is not a pass.
    jid = job_id.strip()
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9]+", jid):
        return f"{job_id!r} is not a Knight job id (they look like 20260808-092342-3136360)."
    return _run([KNIGHT_BIN, "status", jid], limit=6000)


@server.tool(
    description="KNIGHT: stop or restart him. action='disable' engages the kill switch so no new "
                "job can start (a job already running is unaffected — use knight_cancel for that); "
                "action='enable' lifts it; action='status' just reports. Use disable the moment "
                "Knight looks wrong: it is the remote off-switch, and it takes effect immediately."
)
def knight_switch(action: str = "status", reason: str = "") -> str:
    act = action.strip().lower()
    if act not in ("disable", "enable", "status"):
        return "action must be disable, enable or status."
    if act == "status":
        return _run([KNIGHT_BIN, "doctor"], limit=2000)
    # The switch existed only as a local CLI command, so on 2026-08-08 Knight could not be stopped
    # from the phone that had just started him. An off-switch you cannot reach is not an off-switch.
    out = _run([KNIGHT_BIN, act])
    return f"{out}\n\n{_run([KNIGHT_BIN, 'doctor'], limit=1200)}"


@server.tool(
    description="KNIGHT: FINISH a job that is waiting on a branch — merge it and make it live. "
                "Only for targets that land on a branch (moses, moses-framework); viatica deploys "
                "itself. Call with no argument to LIST what is waiting and whether each one may "
                "land. It refuses on its own if the review was not clean, if the review left a "
                "note nobody has answered, if the checkout has uncommitted work, or if the branch "
                "is already in — so ask it and read what it says rather than deciding yourself. "
                "ONLY when Brad has said to: this merges his code and restarts the services he "
                "talks to. The restart happens after this returns, and the result posts to Slack."
)
def knight_land(job_or_branch: str = "", anyway: bool = False) -> str:
    """Brad, 2026-09-17: *"Just need Moses to be able to finish up the job on command."*

    Knight's moses work stops at a branch on purpose, and that is not changing — the point was never
    that Brad types the merge, it was that a person decides. Saying "merge it" IS the decision; this
    carries it out, which is what "a human merges" was always supposed to mean.

    `anyway=True` is only for a branch the review passed WITH a note, after Brad has answered the
    note. It is not a way past a failed review: a CONFIRMED or MISSED finding, or a review nothing
    could read, refuses either way.
    """
    if not job_or_branch.strip():
        return _run([KNIGHT_LAND, "--list"], limit=3000)
    argv = [KNIGHT_LAND, job_or_branch.strip()]
    if anyway:
        argv.append("--anyway")
    return _run(argv, limit=4000)


@server.tool(
    description="KNIGHT: stop a job that is currently running, by id. Kills the worker; anything "
                "already committed stays on its branch. Use knight_switch(disable) to stop NEW "
                "jobs from starting."
)
def knight_cancel(job_id: str) -> str:
    jid = job_id.strip()
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9]+", jid):
        return f"{job_id!r} is not a Knight job id."
    return _run([KNIGHT_BIN, "cancel", jid])


@server.tool(description="KNIGHT: recent build jobs with their status and task, newest first. Also "
                        "shows whether Knight is enabled and how much of today's budget is used.")
def knight_jobs() -> str:
    return f"{_run([KNIGHT_BIN, 'doctor'], limit=2000)}\n\nRECENT JOBS\n{_run([KNIGHT_BIN, 'list'], limit=3000)}"


@server.tool(
    description="ZRYACHIY: have him use a Viatica feature the way customers would, in the disposable "
                "sandbox on Reserve. He drives a real browser as each kind of person: a paid traveler, "
                "a free-plan traveler, an early-access tester, an admin and a visitor. He is judged "
                "against acceptance criteria you write. Use it for a new or early-access feature "
                "before testers see it, or when Brad asks to test something. `criteria` IS REQUIRED: "
                "what must be TRUE when the feature works, one per line, checkable on screen. Not a "
                "restatement of the feature. It runs up to `minutes` (default 30) plus a few minutes "
                "of setup, one at a time, on Brad's Pro subscription (the same allowance as Knight "
                "and Brad's own sessions), so start one when asked, not on your own initiative. It "
                "returns an id at once. Zryachiy posts his findings to #viatica-dev and back to this "
                "conversation when he is done. Nothing he does reaches a real customer: the sandbox "
                "holds made-up people and no production secrets, and his browser can reach nothing "
                "else."
)
def zryachiy_explore(feature: str, criteria: str, minutes: int = 30) -> str:
    feature = (feature or "").strip()
    criteria = (criteria or "").strip()
    if len(feature) < 3:
        return "Name the feature for Zryachiy to explore."
    # Refused here, not skipped downstream: without criteria he can say what looks broken, and never
    # whether the feature is what Brad asked for.
    if len(criteria) < 15:
        return ("Zryachiy needs acceptance criteria before he starts: what must be TRUE when the "
                "feature works, one per line, checkable on screen.\n\n"
                "  weak:   'group trips work'\n"
                "  usable: 'the roster never appears on the public link, whatever the privacy setting'")
    return _run([ZRYACHIY_BIN, "explore", "--feature", feature, "--criteria", criteria,
                 "--minutes", str(int(minutes))], limit=3000)


@server.tool(description="ZRYACHIY: how an exploration is going or how it ended, with his report once "
                        "it is done. With no id, lists the recent ones and today's count.")
def zryachiy_status(job_id: str = "") -> str:
    jid = (job_id or "").strip()
    if jid and not JOB_ID.match(jid):
        return f"{job_id!r} is not an exploration id (they look like 20260911-190000-12345)."
    return _run([ZRYACHIY_BIN, "status"] + ([jid] if jid else []), limit=6000)


@server.tool(description="BIRDEYE (ops): one handed-off job in detail, including live progress. "
                        "Get ids from birdeye_status.")
def get_job(job_id: str) -> str:
    if not JOB_ID.match(job_id.strip()):
        return f"{job_id!r} is not a job id (they look like 20260805-131500-12345)."
    return _run([BIRDEYE_BIN, "show", job_id.strip()])


@server.tool(
    description="Check whether Moses's own architecture document still describes the running system. "
                "Returns any claim in docs/ARCHITECTURE.md — services, binds, paths, hostnames, "
                "roster entries — that reality no longer supports. Use when asked whether the docs "
                "are current, or after anything on Reserve changes."
)
def check_architecture() -> str:
    # Commandment #5 with teeth: a stale diagram is confidently wrong, which is worse than absent.
    try:
        import arch_check
        return arch_check.report()
    except Exception as e:
        return f"Could not run the architecture check: {type(e).__name__}: {e}"


# ── Central status ───────────────────────────────────────────────────────────
@server.tool(
    description="The state of every project in one place: declared phase, focus, blockers and next "
                "step from the registry, enriched with live git state, recent transcript activity "
                "and open todos — and flagging where the declaration DISAGREES with what actually "
                "happened. Use for 'what's the state of everything', 'what am I working on', "
                "'what's blocked', 'what have I not touched lately'."
)
def project_status() -> str:
    try:
        import project_status as ps
        return ps.report()
    except Exception as e:
        return f"Could not build project status: {type(e).__name__}: {e}"


# ── Projects: the list Moses keeps for Brad ──────────────────────────────────
#
# Brad does not open a terminal to file an idea — he is in the Claude app, VS Code or Slack, and he
# tells Moses. So these are the real front door, not a convenience wrapper around one. Every call is
# a thin pass into projects.py, which is also what the CLI uses; one implementation, because the last
# time this logic existed twice the copies drifted and nobody noticed for weeks.

@server.tool(
    description="Show Brad's projects as a dashboard: every project in his order of focus, with its "
                "stage (idea/scope/milestones/building/shipped), progress against milestones, target "
                "date, scope, next step and blockers. Use for 'show me my projects', 'what's on the "
                "list', 'what am I meant to be working on', 'project dashboard'."
)
def project_dashboard() -> str:
    try:
        import projects
        return projects.dashboard()
    except Exception as e:
        return f"Could not build the dashboard: {type(e).__name__}: {e}"


@server.tool(
    description="Look up one item on the Projects page by its code, like V14, M3 or IN2. Every "
                "milestone, idea and proposal has one: the project's letters plus a number that is "
                "never reused. Says which project, whether it is a milestone (and where it stands), "
                "an idea, or a proposal awaiting confirmation, and its note. Use whenever Brad names "
                "an item by its code."
)
def project_item(code: str) -> str:
    try:
        import projects
        return projects.item(code)
    except Exception as e:
        return f"Could not look it up: {e}"


@server.tool(
    description="Add a new project or idea to Brad's list. Name is the only thing required — an idea "
                "with no scope is a valid entry, and the point is catching a thought in one sentence. "
                "Optionally give it a scope, a rank (1 = what he works on next; inserting pushes the "
                "others down) and a status. Use when he says 'add a project', 'new idea', "
                "'I want to build X', 'track this'."
)
def project_add(name: str, scope: str = "", rank: int | None = None, status: str = "parked") -> str:
    try:
        import projects
        return projects.add(name, rank=rank, scope=scope, status=status)
    except Exception as e:
        return f"Could not add it: {e}"


@server.tool(
    description="Change one field on a project. Fields: status (active/parked/dormant/scrapped), "
                "stage (idea/scope/milestones/building/shipped), rank, target (YYYY-MM-DD), scope, "
                "focus, next, phase, name. Writing a scope onto an idea moves it to the scope stage "
                "on its own. The project can be named by id or by part of its name."
)
def project_update(project: str, field: str, value: str) -> str:
    try:
        import projects
        return projects.update(project, field, value)
    except Exception as e:
        return f"Could not update it: {e}"


@server.tool(
    description="Promote a project's idea to a milestone, once it is actually being committed to. "
                "`item` is its code (e.g. V14), which names the project too, so `project` can be "
                "left out; a position number still works with a project. It keeps its code and its "
                "note, so the reason it was worth doing survives the move. Ideas are candidates and "
                "are never counted in a project's progress; a milestone is."
)
def project_promote_idea(item: str, project: str = "") -> str:
    try:
        import projects
        return projects.promote_idea(project, item)
    except Exception as e:
        return f"Could not promote it: {e}"


@server.tool(description="Drop a project's idea, for one that has been decided against. `item` is its "
                         "code (e.g. V14; `project` can then be left out) or a position number with a "
                         "project. Its number is retired, never handed to another item.")
def project_remove_idea(item: str, project: str = "") -> str:
    try:
        import projects
        return projects.remove_idea(project, item)
    except Exception as e:
        return f"Could not drop it: {e}"


@server.tool(
    description="Add a milestone to a project — a step from its scope that can be finished and ticked "
                "off. Adding the first one moves the project to the milestones stage."
)
def project_add_milestone(project: str, title: str) -> str:
    try:
        import projects
        return projects.add_milestone(project, title)
    except Exception as e:
        return f"Could not add the milestone: {e}"


@server.tool(
    description="Mark a project's milestone complete. `item` is its code as shown on the dashboard "
                "(e.g. V14; `project` can then be left out) or its 1-based position with a project. "
                "Pass undo=true to un-tick one."
)
def project_complete_milestone(item: str, project: str = "", undo: bool = False) -> str:
    try:
        import projects
        return projects.complete_milestone(project, item, undo=undo)
    except Exception as e:
        return f"Could not update the milestone: {e}"


@server.tool(
    description="Remove a project from Brad's list entirely. Prefer setting its status to 'scrapped' "
                "instead — that keeps the record of having tried it. Only delete when he asks to."
)
def project_remove(project: str) -> str:
    try:
        import projects
        return projects.remove(project)
    except Exception as e:
        return f"Could not remove it: {e}"


# ── Transcripts: every session, searchable ───────────────────────────────────
@server.tool(
    description="Search every past Claude session transcript. Full-text with stemming, so 'backup' "
                "finds 'backups'; supports \"quoted phrases\", AND/OR/NOT and prefix*. Use this for "
                "'when did we decide X', 'what did we try before', 'have we hit this error already'. "
                "Credentials are redacted at index time, so secrets are never returned."
)
def search_transcripts(query: str, limit: int = 12) -> str:
    try:
        import transcript_index
        return transcript_index.search(query, limit=limit)
    except Exception as e:
        return f"Transcript search failed: {type(e).__name__}: {e}"


@server.tool(
    description="Recall an IDEA from past sessions by meaning rather than wording — use this when "
                "Brad half-remembers something and can't supply the exact phrase: 'that thing we "
                "discussed about making backups safer', 'the reason we dropped that approach', "
                "'what were my thoughts on pricing'. Matches concepts, so it finds a transcript "
                "that shares no words with the query. Prefer search_transcripts instead when you "
                "have an exact string — an error message, a port, a session id, a filename."
)
def recall(query: str, limit: int = 8, mode: str = "hybrid") -> str:
    """mode: 'hybrid' (default, exact+semantic rank-fused) | 'semantic' (meaning only)."""
    try:
        import semantic_index
        if mode == "semantic":
            return semantic_index.semantic(query, limit=limit)
        return semantic_index.search(query, limit=limit)
    except Exception as e:
        # Falling back to exact search is better than returning nothing, but it must SAY so —
        # a silent downgrade would let Brad conclude an idea isn't in the corpus when the
        # search that could have found it never ran (commandment #13).
        try:
            import transcript_index
            return (f"⚠️  Semantic recall failed ({type(e).__name__}: {e}) — "
                    f"showing EXACT keyword matches only:\n\n"
                    + transcript_index.search(query, limit=limit))
        except Exception:
            return f"Recall failed: {type(e).__name__}: {e}"


# ── Discovery: the half that runs unprompted ─────────────────────────────────
@server.tool(
    description="Connections Moses found on his own — pairs of past discussions that converge in "
                "meaning despite coming from different sessions, projects or months apart. Nobody "
                "asked for these. Use when Brad asks what you've noticed, what connects, or "
                "whether two things overlap. Each pair is reported once unless repeat=True."
)
def find_connections(limit: int = 10, repeat: bool = False) -> str:
    try:
        import discovery
        return discovery.connections(limit=limit, repeat=repeat)
    except Exception as e:
        return f"Connection scan failed: {type(e).__name__}: {e}"


@server.tool(
    description="What does this text connect to in everything Brad has worked on? Give it an idea, "
                "a problem statement or a paragraph; it returns prior work that shares substance. "
                "Use before starting anything new, to surface what he has already solved."
)
def link_idea(text: str, limit: int = 6) -> str:
    try:
        import discovery
        return discovery.link(text, limit=limit)
    except Exception as e:
        return f"Link failed: {type(e).__name__}: {e}"


@server.tool(
    description="Cluster everything Brad has discussed into recurring themes, with representative "
                "excerpts and NO labels — read the excerpts and name the clusters yourself. Use "
                "for 'what do I actually spend my time on' and to spot a theme spanning projects."
)
def themes(count: int = 10) -> str:
    try:
        import discovery
        return discovery.themes(k=count)
    except Exception as e:
        return f"Theme clustering failed: {type(e).__name__}: {e}"


@server.tool(description="List indexed Claude sessions newest-first, with title, date, project and turn count.")
def list_sessions() -> str:
    try:
        import transcript_index
        return transcript_index.sessions()
    except Exception as e:
        return f"Could not list sessions: {type(e).__name__}: {e}"


@server.tool(
    description="Read a stretch of one session's transcript by its id (the 8-character prefix from "
                "search_transcripts or list_sessions is enough). start is the turn number to begin at."
)
def read_session(session_id: str, start: int = 1, count: int = 20) -> str:
    try:
        import transcript_index
        return transcript_index.read_session(session_id, start=start, count=min(count, 40))
    except Exception as e:
        return f"Could not read session: {type(e).__name__}: {e}"


# ── Capture: the only tools that write ─────────────────────────────────
# ONE STORE, AND IT IS THE REGISTRY. This used to append to memory/ideas.md, a flat file separate
# from the Projects page. It died the way flat files do — 2 entries, untouched for a month, while
# the memory index still called it the front door. Brad, 2026-09-09: "kill ideas.md and roll ideas
# into the Projects page."
#
# An idea with no project still lands somewhere: the registry inbox, rendered as its own cards on
# the board. Refusing until someone names a project would lose the thought at the exact moment
# capture is supposed to cost nothing.
def _capture(body: str, project: str = "") -> str:
    body = (body or "").strip()
    if len(body) < 3:
        return "Give me something to write down."
    import projects
    if (project or "").strip():
        return projects.add_idea(project, body)
    return projects.add_inbox_idea(body)


@server.tool(description="Record an idea Brad wants to keep but not act on yet. Automatically "
                        "reports what the idea connects to in past work — this is the point of "
                        "capturing it, so do not suppress that part of the answer. Pass `project` "
                        "(an id or name, e.g. 'viatica') when the idea belongs to one, and it is "
                        "also added to that project's candidate list, which the Projects page "
                        "shows separately from its milestones. Ideas are never counted as progress.")
def add_idea(idea: str, project: str = "") -> str:
    saved = _capture(idea, project)
    # FILED TO A PROJECT TOO, when one is named. The idea log is cross-project on purpose — that is
    # what makes the linking below able to spot an overlap between the whitepaper and Viatica — but
    # a shipped product also needs its own shortlist of what might come next, and until now there
    # was nowhere to put one. Same front door, two views; a second capture tool competing with this
    # one is how two lists end up disagreeing about what was said.
    # Capture ALONE is a filing cabinet. Brad's stated goal is seeing where ideas overlap or
    # combine, and the moment an idea is written down is exactly when the prior art is worth
    # knowing — before he starts from scratch on something he already worked out. Linking is
    # best-effort: a failure here must never lose the idea he just gave us.
    try:
        import discovery
        return f"{saved}\n\n{discovery.link(idea, limit=4)}"
    except Exception as e:
        return f"{saved}\n\n(Could not check for connections: {type(e).__name__}: {e})"


@server.tool(
    description="Read every candidate idea in the registry — the unfiled inbox first, then each "
                "project's shortlist. For what is actually being WORKED ON, use project_dashboard or "
                "project_status instead: tasks live on projects, not in a list."
)
def list_ideas() -> str:
    import projects
    d = projects.load()
    out = []
    unfiled = d.get("inbox") or []
    if unfiled:
        out.append("UNFILED — captured, no project yet:")
        for n, i in enumerate(unfiled, 1):
            out.append(f"  {n}. {i['title']}" + (f"  ({i['note']})" if i.get("note") else ""))
    for pr in d.get("projects", []):
        ideas = pr.get("ideas") or []
        if not ideas:
            continue
        out.append("")
        out.append(f"{pr['name']} — {len(ideas)} candidate(s):")
        for n, i in enumerate(ideas, 1):
            out.append(f"  {n}. {i['title']}" + (f"  ({i['note']})" if i.get("note") else ""))
    return "\n".join(out) if out else "Nothing captured yet."


@server.tool(
    description="Perform one of the SHORT list of named repairs Moses is allowed to make himself. "
                "Call with op='ops' and no target first to see what is available. Use rerun-probe "
                "to test whether an alarm was transient before treating it as real. Any op that "
                "CHANGES things, propose it and wait for Brad. Anything not "
                "on the list is refused by name — hand code changes to Knight via knight_start, "
                "or report it to Brad."
)
def remediate(op: str = "ops", target: str = "") -> str:
    """The only tool here that CHANGES anything, and it changes only what moses-remediate allows.

    A wrapper rather than a shell, deliberately: Bash on Reserve reaches the memory corpus, Moses's
    own source and the Viatica checkout, and the boundary would be the model's judgement. Here the
    boundary is a root-owned script that cannot express anything outside its own op list, and every
    attempt — allowed or refused — is recorded where it can be read without asking Moses.
    """
    argv = ["sudo", "-n", "/usr/local/bin/moses-remediate", op]
    if target:
        argv.append(target)
    return _run(argv, limit=3000)


# ── Profiles: how much of Moses a given listener exposes ─────────────────────
# The tool list IS the blast radius, so the public instance starts narrow and is widened
# deliberately — the same asymmetry as the VS Code allowlist: adding a tool is a decision someone
# makes on purpose, while a tool that was never exposed cannot leak.
#
#   full   — everything. For the Tailscale-bound listener, where the network is the boundary.
#   public — no corpus reads. For the listener behind the Cloudflare tunnel, until Access is
#            confirmed to be enforcing on it. The memory files describe Reserve's whole layout —
#            Tailscale addresses, service names, where credentials live — so they are the last
#            thing to publish, not the first.
#
# To widen: set MOSES_MCP_PROFILE=full on the public unit once you have watched Access actually
# challenge an unauthenticated request. That is one env line, and it should follow evidence.
PUBLIC_WITHHELD = ["read_memory", "search_memory", "list_memory"]


def apply_profile(profile: str) -> list[str]:
    """Remove tools this profile should not expose. Returns what was withheld."""
    if profile != "public":
        return []
    for name in PUBLIC_WITHHELD:
        try:
            server.remove_tool(name)
        except Exception:                     # already absent is fine; never fail to start over this
            pass
    return list(PUBLIC_WITHHELD)


def main() -> None:
    host = os.environ.get("MOSES_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MOSES_MCP_PORT", "8765"))
    profile = os.environ.get("MOSES_MCP_PROFILE", "full").strip().lower()
    withheld = apply_profile(profile)
    print(f"moses-mcp: profile={profile} on {host}:{port}"
          + (f" (withholding {', '.join(withheld)})" if withheld else ""), flush=True)
    # Bound to loopback by default. The ONLY way in is the Cloudflare tunnel, so there is no
    # LAN-reachable port on the box holding the BCM footage and the family photos — the same
    # reasoning that put code-server on the Tailscale IP rather than 0.0.0.0.
    #
    # stateless_http: each request stands alone. Brad's Pi server needed two hand-patches to the
    # SDK because claude.ai deletes the session right after tool discovery and opens several
    # concurrent GET streams; with no session state there is nothing to terminate and nothing to
    # conflict. Expected to make both patches unnecessary — but that is reasoning, not evidence,
    # so it stays a claim to verify against the real connector rather than a fact.
    # DNS-rebinding protection is ON by default in the SDK and validates the Host header. Behind a
    # Cloudflare tunnel the Host is the public name, not localhost, so every proxied request comes
    # back HTTP 421 Misdirected Request until the name is allowed here. Found by curling the real
    # tunnel URL rather than the local port — the local check passes happily and tells you nothing
    # about this.
    #
    # Keep the protection ON and name the hosts. Disabling it would also work and would be worse:
    # it turns off a real defense to fix a configuration gap.
    from mcp.server.transport_security import TransportSecuritySettings

    allowed = [h.strip() for h in os.environ.get("MOSES_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    allowed += [f"{host}:{port}", host, "127.0.0.1", "localhost", f"localhost:{port}"]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(set(allowed)),
        # Browsers send Origin; MCP clients generally don't. Mirror the host list so a browser-based
        # client works too, rather than failing in a way that looks like an auth problem.
        allowed_origins=sorted({f"https://{h}" for h in allowed if not h.startswith("127.")}),
    )
    print(f"moses-mcp: allowed hosts = {', '.join(security.allowed_hosts)}", flush=True)

    import anyio
    anyio.run(lambda: server.run_streamable_http_async(
        host=host, port=port, streamable_http_path="/mcp", stateless_http=True,
        transport_security=security))


if __name__ == "__main__":
    main()
