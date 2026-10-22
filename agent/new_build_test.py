#!/usr/bin/env python3
"""The green light and the one-time code, end to end through the real listener and the real helper.

    /home/brad/moses-venv/bin/python agent/new_build_test.py

Brad says "build #13" → Moses declares `DO: new-build 13` → code opens a challenge in the REAL
agent/moses-mfa (run as the operator against a scratch seed, the overrides it honors only when it is
not root) → Brad replies with a code → the listener hands it to the helper before anything else reads
the message. Slack and the model are stubbed; nothing else is.

The property that matters most is the one a reply cannot show: THE MODEL NEVER SEES THE CODE, on that
turn or a later one. So the conversation stub records every history it is handed, and the checks read
that record. Then each guard is removed in turn and its check must go red (commandment 8).
"""
import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import tempfile
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = Path(tempfile.mkdtemp(prefix="moses-newbuild-"))
T0 = 1_800_000_000.0
SECRET = base64.b32encode(b"new-build-test-seed!").decode().rstrip("=")
(TMP / "seed").write_text(SECRET + "\n")
(TMP / "targets").mkdir()
(TMP / "targets" / "viatica.env").write_text("KT_SOURCE=/nowhere\n")
(TMP / "fake-new-target").write_text("#!/bin/sh\necho \"BUILT $1\"\n")
(TMP / "fake-new-target").chmod(0o755)
(TMP / "fake-publish").write_text("#!/bin/sh\necho \"PUBLISHED $1 at $2\"\n")
(TMP / "fake-publish").chmod(0o755)
REG = TMP / "projects.json"
os.environ.update({
    "SLACK_APP_TOKEN": "xapp-test", "SLACK_BOT_TOKEN": "xoxb-test",
    "MOSES_STATE": str(TMP / "state"), "MOSES_CHAT_CHANNELS": "C_CHAT",
    # The site these tests publish to, named here: from the operator's moses.env it passed only where
    # that file happened to say the same domain (the framework copy failed it, 2026-10-22).
    "MOSES_PUBLIC_BASE": "https://example.com",
    "MOSES_COALESCE_WINDOW": "0",   # no burst wait: every message here is its own turn
    "KNIGHT_JOBS": str(TMP / "knight-jobs"), "ZRYACHIY_JOBS": str(TMP / "zryachiy-jobs"),
    "MOSES_REGISTRY": str(REG), "KNIGHT_TARGETS": str(TMP / "targets"),
    # The real helper, unprivileged, against the scratch seed and state.
    "MOSES_MFA_CMD": str(HERE / "moses-mfa"),
    # The build itself is moses-new-target's own suite; here it only has to be STARTED, with the id.
    "MOSES_NEW_TARGET": str(TMP / "fake-new-target"),
    "MOSES_PUBLISH": str(TMP / "fake-publish"),
    "MOSES_MFA_SEED": str(TMP / "seed"), "MOSES_MFA_STATE": str(TMP / "mfa"), "MOSES_MFA_NOW": str(T0),
})


def registry():
    REG.write_text(json.dumps({"projects": [
        {"id": "viatica", "name": "Viatica", "rank": 1, "status": "active", "stage": "building", "kind": "internal",
         "milestones": [], "ideas": [], "scope": "The travel app."},
        {"id": "example-app", "name": "Listings finder", "rank": 13, "status": "active",
         "stage": "scope", "kind": "internal", "milestones": [], "ideas": [],
         "scope": "Aggregate GIS analyst internships for summer 2027, weighted toward cartography."},
        {"id": "second-project", "name": "Second project", "rank": 20, "status": "active", "stage": "scope",
         "kind": "internal", "milestones": [], "ideas": [], "scope": "Something else entirely."},
        {"id": "no-scope", "name": "No scope yet", "rank": 15, "status": "active", "stage": "idea",
         "milestones": [], "ideas": [], "scope": ""},
    ]}))


registry()

import listener      # noqa: E402
import new_build     # noqa: E402
import pacing        # noqa: E402
import silence       # noqa: E402

CHAN, BOT, BRAD, JON = "C_CHAT", "U_MOSES", "U_BRAD", "U_JON"
listener.BOT_USER_ID = BOT
listener.BOT_ID = "B_MOSES"
listener.OWNER_ID = BRAD
listener.scout.check_target = lambda *a, **k: None
listener._spawn = lambda fn, *a: fn(*a)

posted: list = []
channel_history: list = []
model_saw: list = []          # every history the "model" was handed, and every message text in it


class FakeWeb:
    def chat_postMessage(self, **kw):
        posted.append(kw)
        return {"ok": True, "ts": f"{time.time():.6f}"}

    def conversations_history(self, channel, limit=14):
        return {"messages": list(channel_history)}

    def conversations_replies(self, channel, ts, limit=14):
        return {"messages": list(reversed(channel_history))}

    def reactions_add(self, **kw):
        return {"ok": True}


listener.web = FakeWeb()


class FakeSM:
    def send_socket_mode_response(self, *a, **k):
        pass


def stub_model(text: str):
    def _reply(hist, bot_user_id, channel, **kwargs):
        model_saw.append([m.get("text") or "" for m in hist])
        return "text", text, 0.01
    listener.conversation.reply = _reply


def fire(user: str, text: str, ts: str, channel: str = CHAN, thread_ts: str = "") -> list[str]:
    """Deliver one message, as Slack would; it then sits in the channel, as it would."""
    posted.clear()
    ev = {"type": "message", "channel": channel, "ts": ts, "user": user, "text": text}
    if thread_ts:
        ev["thread_ts"] = thread_ts
    req = types.SimpleNamespace(type="events_api", envelope_id="e", payload={"event": ev})
    listener.handle(req, FakeSM())
    channel_history.insert(0, {"user": user, "text": text, "ts": ts})
    for p in posted:
        # The real shape of a message Moses posts under his own name (listener_test.moses_said): no
        # `user`, and a bot_message subtype. Anything less and he never looks mid-conversation, so a
        # bare code would skip the model for the wrong reason and the intercept would go untested.
        channel_history.insert(0, {"bot_id": "B_MOSES", "app_id": "A_MOSES", "username": "Moses",
                                   "subtype": "bot_message", "text": p["text"]})
    return [p["text"] for p in posted]


def reset():
    silence.FLAG.unlink(missing_ok=True)
    pacing.PACING_FILE.unlink(missing_ok=True)
    # The listener drops a (channel, ts) it has seen. Each mutant run reuses these timestamps, so
    # without this every message after the first run is a "duplicate" and nothing reaches anything.
    listener.SEEN.clear()
    new_build._save_note({})
    for f in (TMP / "mfa").glob("state.json"):
        f.unlink()
    channel_history.clear()
    model_saw.clear()


def code_at(t: float) -> str:
    key = base64.b32decode(SECRET + "=" * (-len(SECRET) % 8))
    mac = hmac.new(key, struct.pack(">Q", int(t // 30)), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    return str((struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000).zfill(6)


def suite() -> tuple[list[str], list[str]]:
    passed: list[str] = []
    failed: list[str] = []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        if VERBOSE:
            print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' — ' + str(detail)[:240]}")

    # ── The green light opens a challenge, and the prompt names the project ──
    reset()
    stub_model("On it, asking for your code.\nDO: new-build 13")
    out = fire(BRAD, "Moses, build #13", "100.1")
    prompt = next((t for t in out if "🔐" in t), "")
    check("'build #13' opens a code request naming that project",
          "Listings finder" in prompt and "`new-build:example-app`" in prompt, out)
    check("the DO marker never reaches the channel", not any("DO:" in t for t in out), out)

    # ── The code: caught before the model, checked by the helper ─────────
    stub_model("I SAW A MESSAGE")
    good = code_at(T0)
    before = len(model_saw)
    out = fire(BRAD, good, "100.2")
    check("the right code authorizes that project", any("✅ Authorized" in t and "Listings finder" in t for t in out), out)
    check("and starts the build for exactly that project", any(t.strip() == "BUILT example-app" for t in out), out)
    check("the model is not called on the code's turn", len(model_saw) == before, model_saw[before:])
    check("nothing posted repeats the code", not any(good in t for t in out), out)

    # THE PREMISE, pinned: right after his prompt he is mid-conversation, so a bare message from Brad
    # would go to the model. If this ever stops being true, the check above proves nothing.
    check("premise: right after the prompt, a bare reply would reach the model",
          listener.addressing.in_conversation(listener.recent(CHAN)[1:], BOT, "B_MOSES"), channel_history[:3])

    # A later, ordinary turn reads the channel — with the code still in it.
    fire(BRAD, "Moses, what's next?", "100.3")
    seen = [t for h in model_saw for t in h]
    check("a later turn's model never sees the code (redacted from history)",
          bool(seen) and not any(good in t for t in seen) and new_build.WITHHELD in seen, seen)

    # ── Spacing and a stray code ─────────────────────────────────────────
    reset()
    stub_model("I SAW A MESSAGE")
    out = fire(BRAD, "123 456", "101.1")
    check("a code with no request open is caught, and says nothing was checked",
          model_saw == [] and any("nothing was checked" in t for t in out), out)

    # ── Wrong code: refused, tries counted, the challenge stays answerable ─
    reset()
    stub_model("Asking.\nDO: new-build example-app")
    fire(BRAD, "Moses, go ahead and build the listings finder", "102.1")
    wrong = "000000" if good != "000000" else "111111"
    out = fire(BRAD, wrong, "102.2")
    check("a wrong code is refused with the tries left", any("Not authorized" in t and "2 tries left" in t for t in out), out)
    out = fire(BRAD, good, "102.3")
    check("the right code after a wrong one still works", any("✅ Authorized" in t for t in out), out)

    # ── Somebody else's code is not Brad's ───────────────────────────────
    reset()
    stub_model("Asking.\nDO: new-build 13")
    fire(BRAD, "Moses, build #13", "103.1")
    stub_model("Hi Jon.")
    out = fire(JON, good, "103.2")
    check("a code from anyone but the owner authorizes nothing",
          not any("Authorized" in t for t in out) and new_build._note().get("challenge"), out)

    # ── The green light refuses what it should ──────────────────────────
    reset()
    stub_model("Asking.\nDO: new-build viatica")
    out = fire(BRAD, "Moses, build Viatica", "104.1")
    check("a project that already has a target needs no code, and none is asked",
          any("already has a Knight target" in t for t in out) and not any("🔐" in t for t in out), out)
    stub_model("Asking.\nDO: new-build 15")
    out = fire(BRAD, "Moses, build #15", "104.2")
    check("a project with no scope is refused before any code is asked",
          any("has no scope" in t for t in out) and not any("🔐" in t for t in out), out)
    s_reg = json.loads(REG.read_text())
    s_reg["projects"].append({"id": "a-business", "name": "A business", "rank": 16, "status": "active",
                              "stage": "scope", "kind": "business", "milestones": [], "ideas": [], "scope": "Sell it."})
    REG.write_text(json.dumps(s_reg))
    stub_model("Asking.\nDO: new-build 16")
    out = fire(BRAD, "Moses, build #16", "104.4")
    check("a business with its demand unanswered is refused before any code is asked",
          any("business" in t for t in out) and not any("🔐" in t for t in out), out)
    registry()
    stub_model("Asking.\nDO: new-build 99")
    out = fire(BRAD, "Moses, build #99", "104.3")
    check("a project not in the tracker is refused", any("couldn't tell which project" in t for t in out), out)

    # ── A bot cannot open one ────────────────────────────────────────────
    reset()
    stub_model("Asking.\nDO: new-build 13")
    posted.clear()
    ev = {"type": "message", "channel": CHAN, "ts": "105.1", "bot_id": "B_ATLAS", "user": "U_ATLAS",
          "text": "Moses, build #13"}
    listener.handle(types.SimpleNamespace(type="events_api", envelope_id="e", payload={"event": ev}), FakeSM())
    check("another agent cannot open a code request", not any("🔐" in p["text"] for p in posted)
          and not new_build._note().get("challenge"), [p["text"] for p in posted])

    # ── The helper never ran: COULD NOT CHECK, never "refused" ───────────
    reset()
    saved = new_build.MFA_CMD
    new_build.MFA_CMD = ["sh", "-c", "echo 'sudo: a password is required' >&2; exit 1"]
    stub_model("Asking.\nDO: new-build 13")
    out = fire(BRAD, "Moses, build #13", "106.1")
    new_build.MFA_CMD = saved
    check("a missing sudoers line says the check is not installed, and asks for no code",
          any("Could not ask for the code" in t and "not installed" in t for t in out)
          and not any("🔐" in t for t in out), out)

    # ── A helper that names a different project is not believed ─────────
    reset()
    new_build.MFA_CMD = ["sh", "-c", 'echo \'{"challenge": "abcd1234", "purpose": "new-build:something-else", "expires": "x"}\'']
    stub_model("Asking.\nDO: new-build 13")
    out = fire(BRAD, "Moses, build #13", "107.1")
    new_build.MFA_CMD = saved
    check("a challenge for a different project is not put in front of Brad",
          not any("🔐" in t for t in out) and any("Not asking for a code" in t for t in out), out)

    # ── REVIEW FINDING 1: a second request cannot take over the first ────
    reset()
    stub_model("Asking.\nDO: new-build 13")
    fire(BRAD, "Moses, build #13", "110.1")
    stub_model("Sure.\nDO: new-build 20")
    out = fire(JON, "Moses, build #20", "110.2")
    check("a non-owner's green light opens nothing", not any("🔐" in t for t in out)
          and new_build._note().get("project") == "example-app", out)
    stub_model("Asking.\nDO: new-build 20")
    out = fire(BRAD, "Moses, actually also build #20", "110.3")
    check("while #13's request is open, #20 cannot replace it", any("still open" in t for t in out)
          and new_build._note().get("project") == "example-app", out)
    out = fire(BRAD, good, "110.4")
    check("so Brad's code authorizes the project his prompt named",
          any(t.strip() == "BUILT example-app" for t in out) and not any("second-project" in t for t in out), out)

    # ── The code counts only where it was asked for ──────────────────────
    reset()
    stub_model("Asking.\nDO: new-build 13")
    fire(BRAD, "Moses, build #13", "111.1")
    out = fire(BRAD, good, "111.2", channel="C_ELSEWHERE")
    check("a code typed in another channel answers nothing", any("another conversation" in t for t in out)
          and new_build._note().get("challenge"), out)

    # ── REVIEW FINDING 2: what people put around a code ─────────────────
    for i, text in enumerate([f"<@{BOT}> {good}", f"Moses, {good}", f"{good[:3]} {good[3:]}."]):
        reset()
        stub_model("Asking.\nDO: new-build 13")
        fire(BRAD, "Moses, build #13", f"112.{i}1")
        stub_model("I SAW A MESSAGE")
        before = len(model_saw)
        out = fire(BRAD, text, f"112.{i}2")
        check(f"'{text.replace(good, 'CODE')}' is read as the code, and no model sees it",
              any("✅ Authorized" in t for t in out) and len(model_saw) == before, out)
    reset()
    stub_model("Asking.\nDO: new-build 13")
    fire(BRAD, "Moses, build #13", "113.1")
    stub_model("I SAW A MESSAGE")
    before = len(model_saw)
    out = fire(BRAD, f"{good} thanks", "113.2")
    check("six digits inside a sentence, while a request is open, are withheld from the model",
          len(model_saw) == before and any("send just the six digits" in t for t in out)
          and new_build._note().get("challenge"), out)
    history = new_build.redact([{"user": BRAD, "text": f"it was {good} I think"}])
    check("and redacted inside a sentence in later history", good not in history[0]["text"], history)

    # ── Publishing: Brad can, nobody else can, and a new build can name its place ──
    reset()
    stub_model("Putting it up.\nDO: publish 13 | example.com/listings")
    out = fire(BRAD, "Moses, put the listings page at example.com/listings", "115.1")
    check("Brad's 'put it at example.com/x' publishes that project at that path",
          any("PUBLISHED example-app at listings" in t for t in out), out)
    stub_model("Sure.\nDO: publish 13 | listings")
    out = fire(JON, "Moses, publish the listings page", "115.2")
    check("nobody else can publish", not any("PUBLISHED" in t for t in out)
          and any("Only the owner can publish" in t for t in out), out)
    reset()
    os.environ["MOSES_MFA_NOW"] = str(T0 + 300)        # a fresh time step: earlier checks spent T0's code
    stub_model("Asking.\nDO: new-build 13 | listings")
    out = fire(BRAD, "Moses, build #13 at example.com/listings", "116.1")
    check("a new build can name where it goes live", any("🔐" in t for t in out)
          and new_build._note().get("path") == "listings", out)
    out = fire(BRAD, code_at(T0 + 300), "116.2", )
    recorded = next((p for p in json.loads(REG.read_text())["projects"] if p["id"] == "example-app"), {})
    check("and the code records that place on the tracker for the first landing",
          recorded.get("publish_path") == "listings" and any("example.com/listings" in t for t in out),
          (recorded.get("publish_path"), out))
    os.environ["MOSES_MFA_NOW"] = str(T0)
    registry()

    # ── REVIEW FINDING 7: a note that cannot be saved is said, not hidden ─
    reset()
    saved_note = new_build.NOTE
    new_build.NOTE = Path("/proc/no-such-dir/new-build.json")
    stub_model("Asking.\nDO: new-build 13")
    out = fire(BRAD, "Moses, build #13", "114.1")
    new_build.NOTE = saved_note
    check("if the request cannot be recorded, no code is asked for", not any("🔐" in t for t in out)
          and any("couldn't record" in t for t in out), out)

    return passed, failed


VERBOSE = True
ORIGINAL_NOTE = new_build._note

# Each guard, removed. Its own check must go red, or the suite is not watching it.
MUTANTS = [
    # Two barriers keep a code from the model: the bare-code intercept, and withholding six digits
    # while a request is open. Remove both, or the mutant stays green for the right reason.
    ("no intercept", lambda: (setattr(new_build, "is_code", lambda t: None), setattr(new_build, "has_digits", lambda t: False)),
     "the model is not called on the code's turn"),
    ("no thread binding", lambda: setattr(new_build, "_note", _note_without_channel),
     "a code typed in another channel answers nothing"),
    ("no redaction", lambda: setattr(new_build, "redact", lambda ms: ms),
     "a later turn's model never sees the code (redacted from history)"),
    ("owner not checked", lambda: setattr(listener, "OWNER_ID", JON),
     "a code from anyone but the owner authorizes nothing"),
    ("target not checked", lambda: setattr(new_build, "TARGETS", TMP / "no-such-dir"),
     "a project that already has a target needs no code, and none is asked"),
    ("sudo failure read as a refusal", lambda: setattr(new_build, "_mfa", _mfa_no_sudo_check),
     "a missing sudoers line says the check is not installed, and asks for no code"),
]


def _note_without_channel():
    n = ORIGINAL_NOTE()
    return {**n, "channel": "C_ELSEWHERE"} if n else n


def _mfa_no_sudo_check(*args, stdin=""):
    import subprocess
    r = subprocess.run([*new_build.MFA_CMD, *args], input=stdin, capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


if __name__ == "__main__":
    print("THE GREEN LIGHT AND THE CODE")
    passed, failed = suite()
    print(f"\n  {len(passed)}/{len(passed) + len(failed)} pass")

    print("\nWATCH EACH GUARD FAIL")
    VERBOSE = False
    blind = []
    originals = {"is_code": new_build.is_code, "redact": new_build.redact, "TARGETS": new_build.TARGETS,
                 "_mfa": new_build._mfa, "OWNER_ID": listener.OWNER_ID, "has_digits": new_build.has_digits,
                 "_note": new_build._note}
    for name, apply, expect in MUTANTS:
        apply()
        try:
            _, mfailed = suite()
        finally:
            for k, v in originals.items():
                setattr(listener if k == "OWNER_ID" else new_build, k, v)
        red = expect in mfailed
        print(f"  {'ok  ' if red else 'FAIL'} {name} → '{expect}' goes red")
        if not red:
            blind.append(name)

    # THE BOT FLOOR LIVES IN THE LISTENER'S SOURCE, not in a name this file can swap, so it is broken
    # on a COPY of the tree and the copy's own suite must go red. NB_SOURCE_MUTANT stops the recursion.
    if not os.environ.get("NB_SOURCE_MUTANT"):
        import shutil
        import subprocess
        expect = "another agent cannot open a code request"
        with tempfile.TemporaryDirectory() as md:
            for d in ("agent", "mcp", "knight"):
                shutil.copytree(HERE.parent / d, Path(md) / d, ignore=shutil.ignore_patterns("__pycache__", "repo", "jobs"))
            lp = Path(md) / "agent" / "listener.py"
            src = lp.read_text()
            old = ('            if machine:\n                print(f"moses: ignored DO {name} on a bot-prompted turn", flush=True)\n'
                   '                continue\n')
            # The owner-only check stands behind it (a bot is never the owner), so both go.
            owner = ('                if event.get("user") != OWNER_ID:\n'
                     '                    note += "\\n_Only the owner can start a new build. Nothing was asked for._"\n'
                     '                    print(f"moses: ignored DO {name} from a non-owner", flush=True)\n'
                     '                    continue\n')
            if src.count(old) != 1 or src.count(owner) != 1:
                print("  FAIL bot-prompted: the sabotage no longer applies — update new_build_test")
                blind.append("bot-prompted")
            else:
                lp.write_text(src.replace(old, "").replace(owner, ""))
                r = subprocess.run([sys.executable, str(Path(md) / "agent" / "new_build_test.py")], capture_output=True,
                                   text=True, env={**os.environ, "NB_SOURCE_MUTANT": "1"}, timeout=300)
                red = f"FAIL {expect}" in r.stdout
                print(f"  {'ok  ' if red else 'FAIL'} a bot's DO line obeyed → '{expect}' goes red")
                if not red:
                    blind.append("bot-prompted")
    if failed or blind:
        print(f"\nFAILED: {len(failed)} check(s), {len(blind)} guard(s) not watched failing")
        sys.exit(1)
    print(f"\nall {len(passed)} pass, and every guard was watched failing")
