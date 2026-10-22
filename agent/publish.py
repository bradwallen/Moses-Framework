"""publish — put a project on the web at example.com/<path>, from Slack or the Claude app.

Brad, 2026-10-17, after asking for the Listings finder at example.com/listings and being
told "I don't know how example.com is served": Moses should know the architecture. He does now, in
two places that cannot drift apart — the action description below (what the model reads) and
agent/moses-publish (what actually happens, and its docstring says how the site is wired).

HOW THE SITE IS SERVED, in one breath: a Cloudflare tunnel sends the whole hostname (MOSES_PUBLIC_BASE)
to the site's own server on this machine, which serves the static site and forwards any prefix listed in
its apps file (MOSES_SITE_APPS) to a project's own server on 127.0.0.1:88xx. All three names are the
operator's settings, in moses.env. Publishing touches nothing at Cloudflare and needs no password.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import moses_env as _env  # noqa: E402 — the operator's settings: which site, at which address

NAME = "publish"
HERE = Path(__file__).resolve().parent
PUBLISH = shlex.split(os.environ.get("MOSES_PUBLISH", str(HERE / "moses-publish")))
MCP_DIR = os.environ.get("MOSES_MCP_DIR", str(HERE.parent / "mcp"))
# A path, or the full address on the operator's own site (MOSES_PUBLIC_BASE): "listings",
# "/listings/" or "https://<site>/listings".
_HOST = re.escape(urlparse(_env.setting("MOSES_PUBLIC_BASE", "https://example.com")).hostname or "example.com")
PATH = re.compile(rf"^(?:https?://)?(?:www\.)?(?:{_HOST})?/?([a-z0-9][a-z0-9-]{{1,40}})/?$", re.I)


def actions() -> list[tuple[str, str]]:
    return [(NAME,
             "put a project from the tracker on the web at example.com/<path>, when Brad asks for it to be "
             "viewable there (\"put it at example.com/listings\", \"publish the listings page\"). The "
             "argument is `<project> | <path>`: the project's tracker number, id or name, then the path "
             "after example.com/. You KNOW how this works and never need to ask: a Cloudflare tunnel sends "
             "all of example.com to the site server on Reserve, which forwards a published path to the "
             "project's own server running as a user service on this machine. Publishing runs the app, routes "
             "the path to it, checks the public address answers, and makes every later landing restart it. "
             "It needs no code and touches nothing at Cloudflare. If Brad names no path, ask for one.")]


def parse(arg: str) -> tuple[str, str] | None:
    if "|" not in (arg or ""):
        return None
    ref, path = (x.strip() for x in arg.split("|", 1))
    m = PATH.match(path)
    return (ref, m.group(1).lower()) if ref and m else None


def _project_id(ref: str) -> str:
    if MCP_DIR not in sys.path:
        sys.path.insert(0, MCP_DIR)
    import projects                                             # noqa: PLC0415
    d = projects.load()
    m = re.fullmatch(r"#?(\d+)", ref.strip())
    if m:
        hit = [p for p in d["projects"] if p.get("rank") == int(m.group(1))]
        if hit:
            return hit[0]["id"]
    return projects.find(d, ref)["id"]


def run(arg: str) -> str:
    """Publish and say what happened, step by step. Never raises."""
    got = parse(arg)
    if not got:
        return "⚠️ I need a project and a path, like `13 | listings`. Nothing was published."
    try:
        pid = _project_id(got[0])
    except Exception as e:                                      # noqa: BLE001
        return f"⚠️ I couldn't tell which project: {e}. Nothing was published."
    try:
        r = subprocess.run([*PUBLISH, pid, got[1]], capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"⚠️ Publishing could not run ({type(e).__name__}). Nothing was published."
    out = (r.stdout + r.stderr).strip()
    return out[-3500:] if out else ("✅ Published." if r.returncode == 0 else "⚠️ Publishing failed and said nothing.")
