"""build_spec — a build starts only after Brad confirms what it will and will not include (#2).

Brad, 2026-10-17: "When you said 'civil and water' earlier I didn't think you meant civil engineer."
A request in words was built as something else, and nobody found out until it was live. So in Slack,
Moses cannot start Knight directly any more. He PROPOSES (knight_propose): the task, plus concrete
examples of what the build WILL include and what it WILL NOT — "GIS Analyst Intern: yes; Civil
Engineering Intern that mentions ArcGIS: no". He posts that; Brad answers in the same conversation.
"yes" (or a ✅ on the spec) starts the build — started by the LISTENER's code, never by the model —
and the examples become acceptance criteria the reviewer checks.

The store is one JSON file in the shared state directory (moses_env.STATE, the same for the listener
and the MCP servers). A spec is tied to its conversation after the turn that proposed it (claim), the
same way a Knight job learns where to report (knight_notify). One pending spec per conversation; a
new proposal replaces it, which is how "no, also include X" works.

WHAT A "YES" MAY CONFIRM (fresh-eyes review, 2026-10-17). Only the spec in this EXACT conversation —
the same thread, never "any thread of the channel". Only while it is the last thing asked: anything
else Brad says there ends it (expire_on_reply), and the listener checks no other agent spoke since.
Only a check mark on the spec's own message, never a 👍, and never after the TTL. And only a spec the
Slack turn's own MCP server recorded (`port`): a knight_propose from the iOS app, which reaches the
public server, is never claimed by whichever Slack turn happens to end next.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import moses_env as _env  # noqa: E402

STORE = Path(_env.STATE) / "build-specs.json"
KNIGHT = shlex.split(os.environ.get("KNIGHT_BIN", str(_env.ROOT / "knight" / "bin" / "knight")))
JOBS = Path(os.environ.get("KNIGHT_JOBS", str(_env.ROOT / "knight" / "jobs")))
TTL = 24 * 3600
START_ID = re.compile(r"^\s*(?:<@\w+>\s*)?(?:moses[\s,:]*)?(?:start|build)\s+`?([0-9a-f]{6})`?\s*[.!]*\s*$", re.I)
SPEC_REACTIONS = {"white_check_mark", "heavy_check_mark", "ballot_box_with_check"}
CARD = shlex.split(os.environ.get("KNIGHT_CARD", str(_env.ROOT / "knight" / "lib" / "card.py")))
YES = re.compile(r"^\s*(?:<@\w+>\s*)?(?:moses[\s,:]*)?(yes|yep|yeah|go|go ahead|do it|build it|confirm(?:ed)?|"
                 r"looks good|lgtm|ship it|ok|okay|approved?)\s*[.!]*\s*$", re.I)


class _Locked:
    """One writer at a time: a listener turn, the MCP servers and a confirmation all write this file."""
    def __enter__(self):
        STORE.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(STORE.with_name(STORE.name + ".lock"), "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        self.f.close()


def _load() -> dict:
    try:
        return json.loads(STORE.read_text())
    except (OSError, ValueError):
        return {"specs": []}


def _save(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_name(f"{STORE.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d, indent=1))
    os.replace(tmp, STORE)


def _lines(value) -> list[str]:
    items = value if isinstance(value, list) else re.split(r"\n|;", str(value or ""))
    return [re.sub(r"^[\s•*-]+", "", str(x)).strip() for x in items if str(x).strip()]


def propose(task: str, acceptance: str, will_include, will_exclude, target: str = "") -> str:
    """Record a spec and return the text Moses posts. Refuses a spec without examples on both sides."""
    task, acceptance = (task or "").strip(), (acceptance or "").strip()
    inc, exc = _lines(will_include), _lines(will_exclude)
    if len(task) < 15 or len(acceptance) < 15:
        return "A spec needs a real task and acceptance criteria. Nothing was proposed."
    if not inc or not exc:
        return ("A spec needs concrete EXAMPLES on both sides — at least one thing it will include and one it "
                "will not — so Brad can see where the line is before anything is built. Nothing was proposed.")
    with _Locked():
        d = _load()
        sid = secrets.token_hex(3)
        d["specs"] = [s for s in d["specs"] if time.time() - s.get("created", 0) < TTL]
        d["specs"].append({"id": sid, "task": task, "acceptance": acceptance, "include": inc, "exclude": exc,
                           "target": (target or _env.setting("KNIGHT_DEFAULT_TARGET")).strip(), "created": time.time(), "status": "pending",
                           "port": os.environ.get("MOSES_MCP_PORT", "")})
        _save(d)
    # Moses does not have to paste this: the listener posts the spec itself, so what Brad confirms is
    # what was recorded, word for word, whatever the reply around it says.
    return (render(d["specs"][-1]) + "\n\n(This spec is posted to Brad for you, exactly as above. Do not "
            "paste it; say in a line or two what you drafted and that it is waiting on his yes.)")


def render(spec: dict) -> str:
    inc = "\n".join(f"   ✅ {x}" for x in spec["include"])
    exc = "\n".join(f"   ❌ {x}" for x in spec["exclude"])
    return (f"📝 *Here's what I'll have Knight build* (`{spec['id']}`, on {spec['target']}):\n{spec['task']}\n\n"
            f"*Will include*, for example:\n{inc}\n*Will not include*, for example:\n{exc}\n\n"
            "_Reply *yes* (or ✅ this) to start it, or tell me what to change._")


def claim(before: set[str], *, channel: str, thread_ts: str = "", user: str = "", message_ts: str = "",
          port: str = "", ambiguous: bool = False) -> list[str]:
    """Tie the specs proposed during this turn to its conversation. One pending spec per conversation:
    an older pending one there is superseded. Only specs the Slack turn's own MCP server recorded
    (`port`). When another turn ran at the same time (`ambiguous`), a new spec could be either turn's,
    so it is dropped rather than guessed at — and the caller says so."""
    with _Locked():
        d = _load()
        new = [s for s in d["specs"] if s["id"] not in before and s.get("status") == "pending"
               and not s.get("channel") and _same_server(s, port)]
        if not new:
            return []
        if ambiguous:
            for s in new:
                s["status"] = "ambiguous"
            _save(d)
            return []
        for s in d["specs"]:
            if s.get("status") == "pending" and s.get("channel") == channel and s.get("thread_ts", "") == thread_ts:
                s["status"] = "superseded"
        for s in new[:-1]:
            s["status"] = "superseded"      # two proposals in one turn: the last one is the plan
        new[-1].update(channel=channel, thread_ts=thread_ts, user=user, message_ts=message_ts)
        _save(d)
        return [new[-1]["id"]]


def _same_server(spec: dict, port: str) -> bool:
    return str(spec.get("port", "")) == str(port)


def get(sid: str) -> dict | None:
    return next((s for s in _load()["specs"] if s["id"] == sid), None)


def expire_on_reply(channel: str, thread_ts: str = "") -> str:
    """Brad said something other than yes where a spec was waiting: that spec is no longer the
    question on the table. A changed plan comes back as a new proposal; nothing stays armed for a
    stray "ok" hours later."""
    with _Locked():
        d = _load()
        hit = ""
        for s in d["specs"]:
            if s.get("status") == "pending" and s.get("channel") == channel and s.get("thread_ts", "") == thread_ts:
                s["status"], hit = "expired", s["id"]
        if hit:
            _save(d)
        return hit


def attach_message(spec_ids: list[str], message_ts: str) -> None:
    """Remember which posted message carries the spec, so a ✅ on it confirms the right one."""
    if not spec_ids or not message_ts:
        return
    with _Locked():
        d = _load()
        for s in d["specs"]:
            if s["id"] in spec_ids:
                s["message_ts"] = message_ts
        _save(d)


def ids() -> set[str]:
    return {s["id"] for s in _load()["specs"]}


def pending(channel: str, thread_ts: str = "") -> dict | None:
    """The live spec in this EXACT conversation: the same thread, or the channel's top level."""
    now = time.time()
    for s in reversed(_load()["specs"]):
        if s.get("status") == "pending" and s.get("channel") == channel and now - s.get("created", 0) < TTL \
                and s.get("thread_ts", "") == (thread_ts or ""):
            return s
    return None


def by_message(ts: str) -> dict | None:
    now = time.time()
    return next((s for s in _load()["specs"] if s.get("status") == "pending" and ts and s.get("message_ts") == ts
                 and now - s.get("created", 0) < TTL), None)


def by_start_id(text: str, channel: str, thread_ts: str = "") -> dict | None:
    """ "start 1a2b3c" — the explicit answer when a spec and something else both wait on a yes."""
    m = START_ID.match(text or "")
    s = pending(channel, thread_ts) if m else None
    return s if s and s["id"] == m.group(1).lower() else None


def is_yes(text: str) -> bool:
    return bool(YES.match(text or ""))


def _take(sid: str) -> bool:
    """Move a pending spec to "starting", under the lock. Only one caller ever gets True for a spec."""
    with _Locked():
        d = _load()
        mine = next((s for s in d["specs"] if s["id"] == sid and s.get("status") == "pending"), None)
        if mine:
            mine["status"] = "starting"
            _save(d)
        return bool(mine)


def start(spec: dict, user: str = "") -> str:
    """Start the confirmed build. The examples become criteria the reviewer judges from the change."""
    acc = (spec["acceptance"] + "\n\nCONFIRMED EXAMPLES (Brad agreed to these before the build):\n"
           + "\n".join(f"- It INCLUDES: {x}" for x in spec["include"]) + "\n"
           + "\n".join(f"- It does NOT include: {x}" for x in spec["exclude"]))
    # Claim it FIRST, under the lock: two yeses (or a yes and a ✅) start one build, not two.
    if not _take(spec["id"]):
        was = get(spec["id"]) or {}
        return (f"That build plan is already {was.get('status', 'gone')}"
                f"{' as `' + was['job'] + '`' if was.get('job') else ''}; nothing new was started.")

    def _set(status: str, **kw) -> None:
        with _Locked():
            d = _load()
            for s in d["specs"]:
                if s["id"] == spec["id"]:
                    s["status"] = status
                    s.update(kw)
            _save(d)
    try:
        r = subprocess.run([*KNIGHT, "start", "--target", spec["target"], "--acceptance", acc, spec["task"]],
                           capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        # It may have started anyway, so this spec is NOT re-armed: a second yes could start it twice.
        _set("unknown")
        return ("⚠️ Knight took too long to answer, so I can't tell whether the build started. Look for its "
                "card in Knight's channel before asking again.")
    except OSError as e:
        _set("pending")
        return f"⚠️ The build could not start ({type(e).__name__}). The spec is still waiting; say yes again to retry."
    out = (r.stdout + r.stderr).strip()
    job = next((line.split()[1] for line in out.splitlines() if line.startswith("started ")), "")
    if not job:
        _set("pending")
        return f"⚠️ Knight did not start it: {out.splitlines()[-1] if out else 'no answer'}. The spec is still waiting."
    _set("started", job=job)
    # The job reports to this conversation, on its status card (knight/lib/card.py reads notify.json).
    try:
        (JOBS / job / "notify.json").write_text(json.dumps({"channel": spec.get("channel", ""),
                                                            "thread_ts": spec.get("thread_ts", ""), "user": user}))
    except OSError:
        pass
    # Bring the card here now, not at the first check: it was posted in Knight's channel before this
    # conversation was known, and "its status card is below" should be true while he builds.
    try:
        subprocess.run([*CARD, str(JOBS / job), "building", "Started from the spec you confirmed."],
                       capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return f"🔨 Started `{job}` on {spec['target']} with the examples you confirmed. Its status card is below; it updates as he goes."
