#!/usr/bin/python3 -I
"""card — ONE Slack message per build run, edited in place, instead of a stream of posts.

    card.py <job-dir> <stage> [<line>] [--next "<what happens next>"] [--ping] [--reply-file <file>]

Brad, 2026-10-17: "There's no real way for me to know the status of what's being built. There's so
many messages from different agents in the dev channel. When one person says something it's a black
box until the next message." So a run — a job, its follow-ups, and the next pieces Knight builds after
it — has one card, posted once in the conversation that asked (notify.json) or Knight's channel, and
every later step EDITS it: what is happening now, what happened (time-stamped), and what happens next.

Edits do not notify anyone in Slack. So `--ping` adds one short reply under the card — only when the
run is finished or needs Brad — and `--reply-file` puts a long text (Knight's report) under it once,
instead of in the channel.

THE RUN: a job started by knight-next carries `run-of` (the first job's id); the card lives in that
first job's directory as card.json. Every step of every job in the run updates the same card.

Never raises into its caller: a card that cannot be posted goes to Knight's channel instead, and what
failed is appended to card-error.txt with exit status 1 so a caller with its own fallback uses it. The
build carries on either way — a status message is an enhancement, and enhancements degrade (6).
KNIGHT_SLACK_CHANNEL set and empty means "post nowhere" (the test suites), as everywhere in Knight.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

STAGES = {
    "started": "🟡 Started",
    "building": "🔨 Building",
    "checking": "🧪 Running the checks",
    "reviewing": "🔎 In review",
    "following-up": "🔧 Fixing what review found",
    "landing": "🛬 Landing",
    "landed": "✅ Landed",
    "live": "✅ Live",
    "next-piece": "🔁 Building the next piece",
    "waiting": "⏸️ Waiting for you",
    "needs-you": "🙋 Needs you",
    "done": "🏁 Done",
    "stopped": "⏹️ Stopped",
    "failed": "🔴 Failed",
}
API = os.environ.get("SLACK_API_BASE", "https://slack.com/api")
MAX_LINES = 14


def _token_and_channel() -> tuple[str, str]:
    env = Path(os.environ.get("KNIGHT_SLACK_ENV") or
               Path(os.environ.get("MOSES_CONFIG_DIR", Path.home() / ".config" / "moses")) / "knight.env")
    vals: dict[str, str] = {}
    try:
        for line in env.read_text().splitlines():
            m = re.match(r"\s*(?:export\s+)?([A-Z_]+)=(.*)$", line)
            if m:
                vals[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    except OSError:
        pass
    chan = os.environ["KNIGHT_SLACK_CHANNEL"] if "KNIGHT_SLACK_CHANNEL" in os.environ else vals.get("SLACK_KNIGHT_CHANNEL", "")
    return vals.get("SLACK_BOT_TOKEN", ""), chan


def _call(method: str, payload: dict, token: str) -> dict:
    req = urllib.request.Request(f"{API}/{method}", data=json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def run_dir(job_dir: Path) -> Path:
    """The run's first job: follow run-of, or this job when it started the run."""
    try:
        origin = (job_dir / "run-of").read_text().strip()
        if origin and (job_dir.parent / origin).is_dir():
            return job_dir.parent / origin
    except OSError:
        pass
    return job_dir


def thread_or_chan_known(rd: Path) -> bool:
    try:
        return bool(json.loads((rd / "notify.json").read_text()).get("channel"))
    except (OSError, ValueError):
        return False


def render(card: dict) -> str:
    stage = STAGES.get(card.get("stage", ""), card.get("stage", ""))
    head = f"🛠️ *{card.get('target', '?')}* — {card.get('title', '')}"
    now = f"*Now:* {stage} · {card.get('updated', '')}"
    lines = [f"• {t}  {text}" for t, text in card.get("lines", [])[-MAX_LINES:]]
    nxt = f"*Next:* {card['next']}" if card.get("next") else ""
    return "\n".join(x for x in [head, now, *lines, nxt] if x)


def update(job_dir: Path, stage: str, line: str = "", nxt: str | None = None, ping: bool = False,
           reply: str = "") -> int:
    rd = run_dir(job_dir)
    state_file = rd / "card.json"
    try:
        card = json.loads(state_file.read_text())
    except (OSError, ValueError):
        task = ""
        try:
            task = (rd / "task.txt").read_text().splitlines()[0][:110]
        except (OSError, IndexError):
            pass
        target = ""
        try:
            target = (rd / "target").read_text().strip()
        except OSError:
            pass
        card = {"target": target, "title": task, "lines": [], "stage": "", "next": ""}
    stamp = time.strftime("%H:%M")
    card["stage"], card["updated"] = stage, stamp
    if line:
        card.setdefault("lines", []).append([stamp, line])
        # THE RUN'S ONE LOG (#6, 2026-10-18): every step of every job in the run, in order, in one
        # plain file beside the card — what `knight status` prints, and what the card is a view of.
        # Written before Slack is tried, so it holds the story even when no card could be posted.
        try:
            with open(rd / "run.log", "a") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {job_dir.name}  {STAGES.get(stage, stage)}  {line}\n")
        except OSError:
            pass
    if nxt is not None:
        card["next"] = nxt
    try:
        state_file.write_text(json.dumps(card, indent=1))
    except OSError:
        pass

    token, chan = _token_and_channel()
    home_chan, thread = chan, ""
    try:
        note = json.loads((rd / "notify.json").read_text())
        if note.get("channel"):
            chan, thread = note["channel"], note.get("thread_ts", "")
        user = note.get("user", "")
    except (OSError, ValueError):
        user = ""
    # KNIGHT_SLACK_CHANNEL set and EMPTY is the suites saying "post nowhere" — that wins over the asking
    # conversation too, or a test run would post into the real thread its fixture names.
    if not token or not chan or ("KNIGHT_SLACK_CHANNEL" in os.environ and not os.environ["KNIGHT_SLACK_CHANNEL"]):
        return 0
    errors: list[str] = []

    def post(where: str, in_thread: str, text: str) -> dict:
        body = {"channel": where, "text": text, "username": "Knight", "icon_emoji": ":crossed_swords:",
                "unfurl_links": False}
        if in_thread:
            body["thread_ts"] = in_thread
        r = _call("chat.postMessage", body, token)
        if not r.get("ok"):
            errors.append(f"chat.postMessage to {where}: {r.get('error', 'not ok')}")
        return r

    try:
        text = render(card)
        # MOVE TO THE CONVERSATION THAT ASKED, once. `knight start` posts the card before Moses records
        # which conversation the request came from (he does that right after his reply), so the first
        # card can land in Knight's channel — or at its top level when the request came from a thread
        # there. The new copy is posted FIRST and the old one deleted only once it exists: a move that
        # fails leaves the card where it was, never nowhere (review, 2026-10-17).
        if card.get("ts") and thread_or_chan_known(rd) and not card.get("moved") \
                and (card.get("channel"), card.get("thread", "")) != (chan, thread):
            card["moved"] = True
            r = post(chan, thread, text)
            if r.get("ok"):
                try:
                    _call("chat.delete", {"channel": card["channel"], "ts": card["ts"]}, token)
                except Exception as e:                              # noqa: BLE001 — a stale copy is harmless
                    errors.append(f"chat.delete: {type(e).__name__}")
                card.update(ts=r["ts"], channel=r.get("channel", chan), thread=thread)
                text = ""                                          # just posted: nothing to edit
        if text and card.get("ts") and card.get("channel"):
            r = _call("chat.update", {"channel": card["channel"], "ts": card["ts"], "text": text}, token)
            if not r.get("ok"):
                card.pop("ts", None)          # deleted or uneditable: post a fresh card below
        if not card.get("ts"):
            r = post(chan, thread, text or render(card))
            if not r.get("ok") and home_chan and (chan, thread) != (home_chan, ""):
                chan, thread = home_chan, ""                        # the conversation refused it: Knight's channel
                r = post(chan, thread, text or render(card))
            if not r.get("ok"):
                raise RuntimeError("the card could not be posted anywhere")
            card.update(ts=r["ts"], channel=r.get("channel", chan), thread=thread)
        state_file.write_text(json.dumps(card, indent=1))
        # Replies go under the card: in its thread, or the card's own thread when it has none.
        under = card.get("thread") or card["ts"]
        if reply:
            post(card["channel"], under, reply[:3800])
        if ping:
            who = f"<@{user}> " if user else ""
            post(card["channel"], under, f"{who}{STAGES.get(stage, stage)}: {line or card.get('next', '')}")
    except Exception as e:                                          # noqa: BLE001 — never stop a build
        errors.append(f"{type(e).__name__}: {e}")
    if errors:
        # APPENDED, not overwritten: the first failure is usually the one that explains the rest. And a
        # nonzero exit, so a caller that has its own way to reach Brad (knight-next, knight-land-live)
        # uses it. Every caller ignores or handles the status; none stops on it.
        try:
            with open(job_dir / "card-error.txt", "a") as f:
                for e in errors:
                    f.write(f"{time.strftime('%H:%M:%S')} {stage}: {e}\n")
        except OSError:
            pass
        return 1
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.splitlines()[2].strip(), file=sys.stderr)
        return 0
    job_dir, stage = Path(argv[0]), argv[1]
    rest = argv[2:]
    nxt, ping, reply = None, False, ""
    line_parts = []
    i = 0
    while i < len(rest):
        if rest[i] == "--next" and i + 1 < len(rest):
            nxt = rest[i + 1]; i += 2
        elif rest[i] == "--ping":
            ping = True; i += 1
        elif rest[i] == "--reply-file" and i + 1 < len(rest):
            try:
                reply = Path(rest[i + 1]).read_text()
            except OSError:
                reply = ""
            i += 2
        else:
            line_parts.append(rest[i]); i += 1
    return update(job_dir, stage, " ".join(line_parts), nxt, ping, reply)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
