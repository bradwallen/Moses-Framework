"""A project's changelog, read from its own git history — nothing here is hand-maintained.

Brad, 2026-09-11: "For each Project, create a changelog link so I can click on it and show the full
change log. You should be able to go back and find each release change. If you can go back past the
1.0 release that'd be ideal so we have it all."

WHERE A RELEASE COMES FROM. Not from tags: Viatica's patch tags lapsed for ten releases (v1.10.1 to
v1.10.10, tagged after the fact on 2026-09-11), so a changelog built on tags would have silently lost
them. The version the code DECLARES is the record that cannot lapse: every commit that changed
package.json's "version" starts a release, and each change is listed under the version the code
carried when it was committed. That reaches back past 1.0 on its own — Viatica sat at 0.1.0 from its
first commit on 2026-06-17 until 1.0.0 on 2026-08-20. Tags are not used at all (Brad, 2026-09-11):
`tagged` is still computed, but the page does not show it, so a release is never made to look incomplete.

A repository with no version file (Moses) has no releases to name, so its changes are grouped by day.
A project with no repository says so rather than showing an empty page.
"""
import json
import re
import subprocess
from pathlib import Path

FS, RS = "\x1f", "\x1e"
TRAILER = re.compile(r"^(Co-Authored-By|Signed-off-by):", re.I)
_CACHE: dict = {}          # repo -> (HEAD, result); a new commit is a new HEAD, so it cannot go stale


def _git(repo: str, *args: str) -> str:
    # safe.directory: the page runs as brad today, but git refuses a repository owned by another user,
    # and a changelog that silently empties the day the service user changes would read as "no history".
    r = subprocess.run(["git", "-c", f"safe.directory={repo}", "-C", repo, *args],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "").strip() or f"git {' '.join(args)} failed")
    return r.stdout


def _log(repo: str, *rev: str) -> list[dict]:
    """Changes in a range, newest first. Merges are left out: the commits they bring in are listed."""
    out = _git(repo, "log", "--no-merges", f"--format=%H{FS}%h{FS}%as{FS}%s{FS}%b{RS}", *rev)
    items = []
    for rec in out.split(RS):
        rec = rec.strip("\n")
        if not rec:
            continue
        full, short, day, subject, body = (rec.split(FS) + [""] * 5)[:5]
        body = "\n".join(l for l in body.strip().splitlines() if not TRAILER.match(l.strip())).strip()
        items.append({"hash": full, "short": short, "date": day, "subject": subject, "body": body})
    return items


def version_marks(repo: str) -> list[tuple[str, str, str]]:
    """(version, commit, date) for every version the code has declared, oldest first."""
    if not (Path(repo) / "package.json").is_file():
        return []
    marks, seen = [], set()
    lines = _git(repo, "log", "--reverse", "--format=%H %as", "-G", '"version"', "--", "package.json")
    for line in lines.splitlines():
        if not line.strip():
            continue
        commit, day = line.split()
        try:
            v = json.loads(_git(repo, "show", f"{commit}:package.json")).get("version")
        except (RuntimeError, ValueError):
            continue
        if isinstance(v, str) and v and v not in seen:
            seen.add(v)
            marks.append((v, commit, day))
    return marks


def _has_parent(repo: str, commit: str) -> bool:
    return len(_git(repo, "rev-list", "--parents", "-n1", commit).split()) > 1


def _by_day(changes: list[dict]) -> list[dict]:
    groups: list[dict] = []
    for c in changes:
        if not groups or groups[-1]["date"] != c["date"]:
            groups.append({"date": c["date"], "changes": []})
        groups[-1]["changes"].append(c)
    return groups


def build(repo: str | None) -> dict:
    """The changelog for a repository: kind "versions", "days", or "none" (with the reason)."""
    if not repo:
        return {"kind": "none", "why": "No repository is recorded for this project, so there is no history to read."}
    path = Path(repo).expanduser()
    if not (path / ".git").exists():
        return {"kind": "none", "why": f"The recorded repository ({repo}) is not a git checkout on Reserve."}
    r = str(path)
    head = _git(r, "rev-parse", "HEAD").strip()
    cached = _CACHE.get(r)
    if cached and cached[0] == head:
        return cached[1]

    tags = set(_git(r, "tag", "--list").split())
    marks = version_marks(r)
    if not marks:
        res = {"kind": "days", "groups": _by_day(_log(r, "HEAD"))}
    else:
        releases = []
        for i, (version, commit, day) in enumerate(marks):
            end = f"{marks[i + 1][1]}^" if i + 1 < len(marks) else "HEAD"
            rev = [f"{commit}^..{end}"] if _has_parent(r, commit) else [end]
            releases.append({"version": version, "date": day,
                             "tagged": f"v{version}" in tags or version in tags,
                             "changes": _log(r, *rev)})
        if _has_parent(r, marks[0][1]):
            # package.json arrived after the first commit: what came before had no version at all.
            early = _log(r, f"{marks[0][1]}^")
            if early:
                releases.insert(0, {"version": None, "date": early[-1]["date"], "tagged": False, "changes": early})
        res = {"kind": "versions", "releases": list(reversed(releases))}
    _CACHE[r] = (head, res)
    return res
