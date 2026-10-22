"""new_build — Brad's green light for a brand-new build, and the one-time code that unlocks it (#14 A3).

Brad, 2026-10-16: "once I provide the green light for a project to be built from the Projects tracker
Moses should then prompt me for an MFA code and once I provide the code that should unlock Moses/Knight
to create a new directory for the project to live, put it in version control and then build out
whatever the scope of the project is."

Two halves, and the model is in neither:

  start()   Moses decides a message means "build #13" and asks for it with `DO: new-build 13`. CODE
            then checks the project is in the tracker, has a scope and has no Knight target yet, asks
            the root helper to open a challenge, and posts the prompt. The project the prompt names
            comes from the helper's own answer, so what Brad reads is what his code will authorize.
  answer()  A bare 6-digit message from the owner is caught by the listener BEFORE anything else —
            before the operator's commands, the proposal matcher and the model — and handed here. The
            code goes to `moses-mfa verify` on stdin, and the result is said in the thread.

Every check that matters lives in agent/moses-mfa, which runs as root. This module only routes:
the note it keeps (which challenge is open, where to answer) is operator-writable, and nothing in it
can authorize anything. Swapping it would point an answer at the wrong challenge id, and the helper
refuses an id it did not open.

Codes are also kept out of what the model READS: listener.recent() calls redact() on every message it
fetches, so a code Brad typed is gone from the context of every later turn, not only this one.

What happens after a verified code is agent/moses-new-target (A4: the directory, git, the target,
Knight's first job), run off the event thread by build(). It checks the authorization again itself.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

NAME = "new-build"
# ASCII digits only. `\d` also matches Arabic-Indic digits, and those crashed the helper's compare.
CODE = re.compile(r"^\s*([0-9]{3})[ -]?([0-9]{3})\s*[.!]?\s*$")
# What a person puts before a code without thinking: "@Moses 123456", "Moses, 123 456".
PREFIX = re.compile(r"^\s*(?:(?:<@\w+>|moses\b)[\s,:.-]*)+", re.I)
SIX = re.compile(r"(?<![0-9])[0-9]{3}[ -]?[0-9]{3}(?![0-9])")
WITHHELD = "[a one-time code — withheld]"

HERE = Path(__file__).resolve().parent
MFA_CMD = shlex.split(os.environ.get("MOSES_MFA_CMD", "sudo -n /usr/local/bin/moses-mfa"))
NEW_TARGET = shlex.split(os.environ.get("MOSES_NEW_TARGET", str(HERE / "moses-new-target")))
TARGETS = Path(os.environ.get("KNIGHT_TARGETS", str(HERE.parent / "knight" / "targets")))
MCP_DIR = os.environ.get("MOSES_MCP_DIR", str(HERE.parent / "mcp"))
# THE SAME STATE DIRECTORY FROM EVERY CALLER. The listener gets MOSES_STATE from moses.env; the MCP
# servers do not load moses.env at all, so a plain environment read would put their note under
# /var/lib/moses (root's, unwritable) while the listener's sat in ~/.local/state/moses — the split state
# directory that once made Moses report every persona as never having run. moses_env.STATE reads the
# environment, then the operator's settings file, then the default, so every process lands on one path.
sys.path.insert(0, str(HERE / "lib"))
import moses_env as _env  # noqa: E402
STATE = Path(_env.STATE)
NOTE = STATE / "new-build.json"


def actions() -> list[tuple[str, str]]:
    return [(NAME,
             "start a brand-new build of a project that is already in the projects tracker: a new "
             "directory, version control, its Knight target, and Knight building its scope. Use it "
             "whenever Brad gives a project the green light to be built (\"build #13\", \"go ahead "
             "and build the listings finder\", \"kick off G\"), however he puts it. The argument is "
             "the project: its tracker number, id, code or name, then `| <path>` when Brad says where it "
             "should live on the web (\"build #13 at example.com/listings\" → `13 | listings`); "
             "it is published there once its first job lands. Code then asks Brad for his "
             "one-time code; NEVER ask him for a code yourself, never repeat one, and never tell him "
             "the build has started — code says what happened.")]


def is_code(text: str) -> str | None:
    """The six digits if the message is a one-time code ('123456', '123 456', '@Moses 123456'), else None."""
    m = CODE.match(PREFIX.sub("", text or ""))
    return (m.group(1) + m.group(2)) if m else None


def has_digits(text: str) -> bool:
    """Six digits anywhere in the message: "123456 thanks", "here you go 123456"."""
    return bool(SIX.search(text or ""))


def redact(messages: list[dict]) -> list[dict]:
    """Messages with every code-shaped run from a person withheld, so no later turn's model sees one.
    A bare code becomes WITHHELD; six digits inside a sentence are replaced in place. It costs the
    model the odd six-digit number a person typed, which is cheaper than one live code."""
    out = []
    for m in messages:
        text = m.get("text") or ""
        if not m.get("bot_id") and text:
            if is_code(text):
                m = {**m, "text": WITHHELD}
            elif has_digits(text):
                m = {**m, "text": SIX.sub("[six digits withheld]", text)}
        out.append(m)
    return out


def open_here(channel: str) -> bool:
    """Is a code request open, posted in this channel? The note is routing, not authority."""
    n = _note()
    return bool(n.get("challenge")) and n.get("channel") == channel


# ── The helper ───────────────────────────────────────────────────────────────
def _mfa(*args: str, stdin: str = "") -> tuple[int, str, str]:
    try:
        r = subprocess.run([*MFA_CMD, *args], input=stdin, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 2, "", f"could not run {MFA_CMD[-1]} ({type(e).__name__})"
    err = r.stderr.strip()
    # sudo's own refusal is not the helper's answer: it means the helper never ran (A7 not installed,
    # or the sudoers line is missing). That is "could not check", never "refused".
    if r.returncode != 0 and (err.startswith("sudo:") or "a password is required" in err):
        return 2, "", f"the code check is not installed for this account ({err.splitlines()[0][:120]})"
    return r.returncode, r.stdout.strip(), err.replace("moses-mfa: ", "", 1)


def _note() -> dict:
    try:
        return json.loads(NOTE.read_text())
    except (OSError, ValueError):
        return {}


def _save_note(d: dict) -> bool:
    try:
        NOTE.parent.mkdir(parents=True, exist_ok=True)
        tmp = NOTE.with_name(f"{NOTE.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(d))
        os.replace(tmp, NOTE)
        return True
    except OSError as e:
        print(f"moses: new-build note not saved ({e.strerror})", flush=True)
        return False


def _registry():
    if MCP_DIR not in sys.path:
        sys.path.insert(0, MCP_DIR)
    import projects                                             # noqa: PLC0415
    return projects


def _project(ref: str) -> dict:
    """The tracker project by its number ('13', '#13'), or anything projects.find() accepts."""
    reg = _registry()
    d = reg.load()
    ref = (ref or "").strip().strip(".!")
    m = re.fullmatch(r"#?(\d+)", ref)
    if m:
        hit = [p for p in d["projects"] if p.get("rank") == int(m.group(1))]
        if hit:
            return hit[0]
    return reg.find(d, ref)


# ── The green light ──────────────────────────────────────────────────────────
def start(ref: str, channel: str, thread_ts: str = "") -> str:
    # "13 | listings": where it should live on the web, kept until the first landing publishes it.
    import publish                                              # noqa: PLC0415
    got = publish.parse(ref)
    path = ""
    if got:
        ref, path = got
    try:
        p = _project(ref)
    except Exception as e:                                      # noqa: BLE001 — ProjectError, or no registry
        return f"⚠️ I couldn't tell which project to build: {e}. Nothing was started."
    pid, name = p["id"], (p.get("name") or p["id"]).strip()
    if not (p.get("scope") or "").strip():
        return (f"⚠️ *{name}* has no scope in the tracker, and the scope is what Knight would build. "
                "Give it one first. Nothing was started.")
    try:
        # Building starts here, so the tracker's own rule applies before a code is asked for: a
        # business does not start without its demand on the record (commandment 10).
        _registry().assert_may_build(p)
    except Exception as e:                                      # noqa: BLE001 — ProjectError
        return f"⚠️ {e}\nNothing was started."
    if (TARGETS / f"{pid}.env").exists():
        return (f"*{name}* already has a Knight target, so it needs no code: ask me to have Knight "
                "build it. Nothing was started.")
    rc, out, err = _mfa("challenge", NAME, pid)
    if rc != 0:
        lead = "⚠️ Could not ask for the code" if rc == 2 else "⚠️ The code check refused"
        return f"{lead}: {err or 'no reason given'}. Nothing was started."
    try:
        c = json.loads(out)
        cid, purpose = c["challenge"], c["purpose"]
    except (ValueError, KeyError):
        return "⚠️ The code check answered in a shape I don't understand. Nothing was started."
    if purpose != f"{NAME}:{pid}":
        # The prompt must name what the code will authorize. If the helper says otherwise, trust neither.
        return f"⚠️ The code check opened `{purpose}`, not `{NAME}:{pid}`. Not asking for a code against it."
    if not _save_note({"challenge": cid, "purpose": purpose, "project": pid, "name": name,
                       "channel": channel, "thread_ts": thread_ts, "opened": time.time(), "path": path}):
        # Without the note, his reply would be told "no request open" while one is. Say it now instead.
        return ("⚠️ The code request opened, but I couldn't record where to expect your answer, so I'm not "
                "asking for it. It closes by itself in five minutes; nothing was authorized.")
    return (f"🔐 *New build: {name}* (`{purpose}`)\n"
            f"A new directory under version control, a Knight target, and Knight building this scope:\n"
            f"> {(p.get('scope') or '').strip()[:400]}\n"
            f"Reply with the 6-digit code from your authenticator to authorize it. "
            f"The request closes at {c.get('expires', 'five minutes from now')}.")


# ── The code ─────────────────────────────────────────────────────────────────
def answer(code: str, who: str, channel: str = "", thread_ts: str = ""):
    """(what to say now, the build to run off the event thread, or None).

    The code counts only where it was asked for: the channel the prompt was posted in, and its thread
    when it was posted in one. The helper keeps one request open at a time, so this cannot authorize a
    project other than the one the prompt named; this keeps a code typed elsewhere from answering it."""
    n = _note()
    cid = n.get("challenge")
    if not cid:
        return "There's no code request open, so nothing was checked. Ask me to build a project first.", None
    if n.get("channel") != channel or (n.get("thread_ts") and n.get("thread_ts") != thread_ts):
        return (f"The code request for *{n.get('name') or n.get('project')}* is in another conversation. "
                "Reply there; nothing was checked here.", None)
    rc, out, err = _mfa("verify", cid, "--who", who, stdin=code)
    if rc == 2:
        return f"⚠️ Could not check the code: {err}. Nothing was authorized.", None
    if rc != 0:
        # The challenge is still answerable only when the helper says tries are left.
        if "left on this challenge" not in err:
            _save_note({})
        return f"❌ Not authorized: {err}", None
    _save_note({})
    try:
        a = json.loads(out)
    except ValueError:
        return "⚠️ The code check answered in a shape I don't understand. Treat it as not authorized.", None
    pid, name = n.get("project", ""), n.get("name", "")
    if a.get("authorized") != f"{NAME}:{pid}":
        return f"⚠️ The code authorized `{a.get('authorized')}`, not `{NAME}:{pid}`. Not building anything.", None
    if n.get("path"):
        try:
            _registry().update(pid, "publish_path", n["path"])
        except Exception as e:                                  # noqa: BLE001
            print(f"moses: publish_path not recorded ({type(e).__name__})", flush=True)
    where = f" It goes live at example.com/{n['path']} once its first job lands." if n.get("path") else ""
    return (f"✅ Authorized: new build for *{name or pid}*. Creating its directory and starting Knight; "
            f"I'll post each step here.{where}", lambda: build(pid))


def build(pid: str) -> str:
    """Run moses-new-target and say what it did. It checks the authorization again itself, so nothing
    here can start a build that the helper did not authorize."""
    try:
        r = subprocess.run([*NEW_TARGET, pid], capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"⚠️ The build step could not run ({type(e).__name__}). Check `moses-mfa status`."
    out = (r.stdout + r.stderr).strip()
    return out[-3500:] if out else ("✅ Done." if r.returncode == 0 else "⚠️ The build step failed and said nothing.")
