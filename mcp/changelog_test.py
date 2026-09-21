#!/usr/bin/env python3
"""The changelog gate.

    mcp/venv/bin/python mcp/changelog_test.py

Built against throwaway repositories with a known history, so every claim below is checked against
an answer written down before the code ran: a long stretch at 0.1.0, a tagged 1.0.0, an UNTAGGED patch
(the lapse that made tags unusable as the definition of a release), a change after the last bump, and
a repository with no version file at all.
"""
import json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

fails = []
def check(label, cond, detail=""):
    print(f"  {'✓' if cond else '✗'} {label}" + ("" if cond else f"  — {detail}"))
    if not cond: fails.append(label)


def repo_with(history, root):
    """history: [(date, subject, body, version-or-None, tag-or-None)]; version None = no package.json change."""
    r = Path(tempfile.mkdtemp(dir=root))
    run = lambda *a, env=None: subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True, env=env)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com"); run("config", "user.name", "T")
    for i, (day, subject, body, version, tag) in enumerate(history):
        if version is not None:
            (r / "package.json").write_text(json.dumps({"name": "x", "version": version}, indent=2) + "\n")
            run("add", "package.json")
        (r / "notes.txt").write_text(f"{i}\n")
        run("add", "notes.txt")
        env = {**os.environ, "GIT_AUTHOR_DATE": f"{day}T12:00:00", "GIT_COMMITTER_DATE": f"{day}T12:00:00"}
        run("commit", "-q", "-m", subject + (f"\n\n{body}" if body else ""), env=env)
        if tag:
            run("tag", "-a", tag, "-m", tag)
    return str(r)


with tempfile.TemporaryDirectory() as root:
    import changelog as cl

    viatica_like = repo_with([
        ("2026-06-01", "feat: initial scaffold", "", "0.1.0", None),
        ("2026-06-10", "feat: the itinerary builder", "", None, None),
        ("2026-07-01", "fix: dates <b>shift</b> west of Greenwich", "", None, None),
        ("2026-08-20", "Viatica 1.0", "The day it charged real money.", "1.0.0", "v1.0.0"),
        ("2026-08-21", "Stop telling Google Viatica is free", "", None, None),
        ("2026-09-10", "Never show a flight time nobody stated", "Why it matters.\n\nCo-Authored-By: Claude <x@y>", "1.0.1", None),
        ("2026-09-10", "Write down how to change the app's address", "", None, None),
    ], root)
    log = cl.build(viatica_like)

    print("releases come from the version the code declared")
    check("it is a versioned changelog", log["kind"] == "versions", log.get("kind"))
    rels = log.get("releases", [])
    check("every version, newest first", [x["version"] for x in rels] == ["1.0.1", "1.0.0", "0.1.0"],
          [x["version"] for x in rels])
    by = {x["version"]: [c["subject"] for c in x["changes"]] for x in rels}
    check("a change after the last bump is in the latest release",
          by.get("1.0.1") == ["Write down how to change the app's address", "Never show a flight time nobody stated"], by.get("1.0.1"))
    check("each release holds exactly what was committed while the code said that version",
          by.get("1.0.0") == ["Stop telling Google Viatica is free", "Viatica 1.0"], by.get("1.0.0"))
    check("it reaches back past 1.0", by.get("0.1.0") == [
        "fix: dates <b>shift</b> west of Greenwich", "feat: the itinerary builder", "feat: initial scaffold"], by.get("0.1.0"))
    check("an untagged release is still a release — tags lapsed once", any(x["version"] == "1.0.1" for x in rels))
    tagged = {x["version"]: x["tagged"] for x in rels}
    check("a tag is shown where there is one, and only there", tagged == {"1.0.1": False, "1.0.0": True, "0.1.0": False}, tagged)
    check("a release is dated by the commit that declared it", {x["version"]: x["date"] for x in rels}.get("1.0.0") == "2026-08-20")
    body = next(c["body"] for c in rels[0]["changes"] if c["subject"].startswith("Never"))
    check("the description is kept and the attribution trailer is not", body == "Why it matters.", repr(body))

    print("\na repository with no version file is grouped by day")
    moses_like = repo_with([
        ("2026-09-08", "The registry learns a building state", "", None, None),
        ("2026-09-09", "Each project gets its own board", "", None, None),
        ("2026-09-09", "Stop the refresh throwing away what you read", "", None, None),
    ], root)
    days = cl.build(moses_like)
    check("it says it is by day", days["kind"] == "days", days.get("kind"))
    check("days newest first, changes kept together",
          [(g["date"], len(g["changes"])) for g in days.get("groups", [])] == [("2026-09-09", 2), ("2026-09-08", 1)],
          days.get("groups"))

    print("\nno repository says so instead of looking empty")
    check("no repo recorded", cl.build(None)["kind"] == "none")
    check("a path that is not a checkout", cl.build(root)["kind"] == "none" and "not a git checkout" in cl.build(root)["why"])

    print("\nthe page")
    reg = Path(root) / "projects.json"
    reg.write_text(json.dumps({"projects": [
        {"id": "viatica", "name": "Viatica", "rank": 1, "status": "active", "stage": "building", "repo": viatica_like, "milestones": []},
        {"id": "moses", "name": "Moses", "rank": 2, "status": "active", "stage": "building", "repo": moses_like, "milestones": []},
        {"id": "idea-only", "name": "Idea only", "rank": 3, "status": "parked", "stage": "idea", "repo": "",
         "milestones": [{"title": "Scope it", "done": True}]},
    ], "inbox": []}))
    os.environ["MOSES_REGISTRY"] = str(reg)
    import dashboard_server as d
    d.projects.REGISTRY = reg
    d.live_state.summary = lambda: None

    page = d.render("/changelog/viatica") or ""
    check("the changelog renders", "Viatica" in page and "1.0.1" in page)
    for v in ["1.0.1", "1.0.0", "0.1.0"]:
        check(f"it has a place to jump to {v}", f'href="#v{v}"' in page and f'id="v{v}"' in page)
    check("a commit subject is text, never markup", "<b>shift</b>" not in page and "&lt;b&gt;shift&lt;/b&gt;" in page)
    check("the long pre-1.0 stretch is grouped by week", "Week of" in page)
    check("the Projects pill is the current one", 'href="/projects" class="on"' in page)
    nav = lambda s: s.split("<nav>")[1].split("</nav>")[0] if "<nav>" in s else None
    check("same nav as every other page", nav(page) == nav(d.render("/projects") or ""))
    check("by-day projects render", "2026-09-09" in (d.render("/changelog/moses") or ""))
    none_page = d.render("/changelog/idea-only") or ""
    check("a project without a repository explains itself", "No repository is recorded" in none_page)
    check("and still lists what it finished", "Scope it" in none_page)
    check("an unknown project is a 404, not an empty page", d.render("/changelog/nope") is None)

    projects_page = d.render("/projects") or ""
    for pid in ["viatica", "moses", "idea-only"]:
        check(f"the Projects page links {pid}'s changelog", f'href="/changelog/{pid}"' in projects_page)

print(f"\n{'FAILED: ' + ', '.join(fails) if fails else 'changelog gate passes'}")
sys.exit(1 if fails else 0)
