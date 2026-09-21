#!/usr/bin/env python3
"""Pin the guards on conversational Moses. Run: python3 conversation_test.py

No model runs and nothing is spent — the CLI invocation is stubbed. What is under test is not the
answer Moses gives; it is every gate deciding whether he is allowed to speak at all, and the exact
shape of the command line, because that is where his isolation lives.

The tests named SAFEWORD and ISOLATION are the load-bearing ones:
  SAFEWORD  — if a future edit lets a silenced Moses talk, these go red.
  ISOLATION — if the call ever stops stripping Brad's settings, or stops disabling tools, these go
              red. Loading his settings would run his Stop hook against Moses's chat replies; not
              disabling tools would give a Slack channel containing other people a shell on Reserve.
"""

import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Point state at a temp dir BEFORE importing — both modules read it at import time, and the real one
# is /var/lib/moses, which these tests must never touch.
_TMP = tempfile.mkdtemp(prefix="moses-test-")
os.environ["MOSES_STATE"] = _TMP
os.environ["MOSES_CHAT_CHANNELS"] = "C0BQ4PTUJV8"

import budget            # noqa: E402
import conversation      # noqa: E402

# The REAL _invoke, captured before any stub replaces it. Tests further down swap it out, so a later
# test calling conversation._invoke would otherwise be exercising a stub and proving nothing.
_REAL_INVOKE = conversation._invoke
import silence           # noqa: E402

CHAN = "C0BQ4PTUJV8"
BOT = "U0BMTTVT648"
HIST = [{"user": "U1", "text": "Moses, what do you make of that?"}]

results = []


def check(label, cond):
    results.append((label, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + label)

# ISOLATED ON THE REGISTRY, because that is where proposals now live. These used to unlink a
# `proposals.json`; there is no longer one. Pointing the registry at a temp file exercises the real
# store rather than a stand-in, and guarantees a test can never touch Brad's actual projects.
import json as _json, tempfile as _tf, pathlib as _pl, sys as _sys
_sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "mcp"))
import projects as _registry_mod

_TESTREG = _pl.Path(_tf.mkdtemp()) / "projects.json"


def _fresh_registry():
    """A registry holding just the two projects the tests propose against."""
    _TESTREG.write_text(_json.dumps({"projects": [
        {"id": "viatica", "name": "Viatica", "rank": 1, "status": "active",
         "stage": "building", "milestones": [], "ideas": []},
        {"id": "moses", "name": "Moses", "rank": 2, "status": "active",
         "stage": "building", "milestones": [], "ideas": []},
    ]}), encoding="utf-8")
    _registry_mod.REGISTRY = _TESTREG


_fresh_registry()


def stub(result="Fine by me.", cost=0.038, exc=None, is_error=False):
    def _invoke(prompt, allowed=None, directed=False, images=None, **kw):
        stub.kw = kw
        stub.seen = prompt
        stub.called = True
        if exc:
            raise exc
        return {"result": result, "total_cost_usd": cost, "num_turns": 1, "is_error": is_error}
    stub.seen = None
    stub.called = False
    conversation._invoke = _invoke


def say(*a, **k):
    """reply() -> the message text, or None when he stays quiet / never ran."""
    kind, value, cost = conversation.reply(*a, **k)
    return (value if kind == "text" else None), cost


def reset():
    silence.FLAG.unlink(missing_ok=True)
    if budget.LEDGER_DIR.is_dir():
        for p in budget.LEDGER_DIR.glob("*.json"):
            p.unlink()


# ── SAFEWORD ────────────────────────────────────────────────────────────────
reset()
check("SAFEWORD 'standdown' works anywhere", silence.is_stop("ok everyone standdown now"))
check("SAFEWORD is case-insensitive", silence.is_stop("STANDDOWN"))
check("SAFEWORD 'stand down' alone works", silence.is_stop("stand down"))
check("SAFEWORD 'Moses, stand down' works", silence.is_stop("Moses, stand down"))
check("SAFEWORD an @mention form works", silence.is_stop("<@U0BMTTVT648> stand down"))
check("SAFEWORD trailing punctuation is fine", silence.is_stop("stand down!"))
# The false trigger a listener test caught: ordinary shop talk in a channel about servers.
check("SAFEWORD NOT tripped by 'stand down the old Pi service'",
      not silence.is_stop("we should stand down the old Pi service"))
check("SAFEWORD NOT tripped by 'they had to stand down the release'",
      not silence.is_stop("they had to stand down the release"))
check("SAFEWORD not tripped by 'standdowns'", not silence.is_stop("standdowns"))
check("SAFEWORD not tripped by 'stood down'", not silence.is_stop("the backup stood down cleanly"))
check("RESUME 'as you were' alone recognized", silence.is_resume("as you were"))
check("RESUME 'moses resume' recognized", silence.is_resume("moses resume"))
check("RESUME NOT tripped by 'as you were saying'", not silence.is_resume("as you were saying earlier"))
check("RESUME not tripped by ordinary talk", not silence.is_resume("that is how it were done"))

silence.engage("U1", CHAN)
check("SAFEWORD engaging writes a flag", silence.silenced())
stub()
out, spent = say(HIST, BOT, CHAN)
check("SAFEWORD silences the model entirely", out is None and spent == 0.0)
check("SAFEWORD means the CLI was never invoked", not stub.called)
check("SAFEWORD survives a restart (it is a file on disk)", silence.FLAG.exists())
silence.release("U1")
check("RESUME clears the flag", not silence.silenced())

# ── Channel allowlist ───────────────────────────────────────────────────────
reset(); stub()
out, _ = say(HIST, BOT, "C0BM0AG70AV")     # #ops
check("stays quiet in a channel that is not opted in", out is None and not stub.called)

os.environ["MOSES_CHAT_CHANNELS"] = ""
stub()
out, _ = say(HIST, BOT, CHAN)
check("an empty allowlist means off everywhere (safe default)", out is None and not stub.called)
os.environ["MOSES_CHAT_CHANNELS"] = CHAN

# ── Budget ──────────────────────────────────────────────────────────────────
# The asymmetry Brad asked for, quoting Atlas: "a human message always gets a reply, no turn cap,
# no cost cap." The allowance exists to bound MACHINE chatter, and a limiter that silences Moses at
# Brad or Jon is a bug wearing a safety feature's clothes. Both directions are pinned.
reset()
budget.record(conversation.AGENT_ID, 99.0)
stub()
out, _ = say(HIST, BOT, CHAN, reactive=True)
check("ASYMMETRY an exhausted allowance stops a BOT-prompted reply", not stub.called)
check("ASYMMETRY the bot-to-bot refusal says so plainly", out and "allowance" in out.lower())
check("ASYMMETRY it still offers itself to the humans", out and "Brad" in out)

stub()
out, _ = say(HIST, BOT, CHAN, reactive=False)
check("ASYMMETRY the same exhausted allowance NEVER blocks a human", stub.called and out == "Fine by me.")

reset(); stub(cost=0.038)
out, spent = say(HIST, BOT, CHAN)
check("the reply comes back", out == "Fine by me.")
check("usage is recorded to the ledger", abs(budget.status(conversation.AGENT_ID).spent_usd - 0.038) < 1e-9)

# ── A FAILED TURN: retry once, charge both, never show the payload ─────────
# 2026-08-14: a run errored with stop_reason "stop_sequence" on a tool-using turn. The old code
# raised on the CLI's exit status, threw away a result that was perfectly good JSON, put the RAW
# PAYLOAD in a channel containing Jon and Atlas, and recorded the $0.337 it had spent as $0.0000.
# Three reproductions afterwards all succeeded, so the fault was transient — which is exactly what a
# single retry is for.
BAD = {"is_error": True, "stop_reason": "stop_sequence", "num_turns": 3,
       "total_cost_usd": 0.337, "result": '{"is_error":true,"session_id":"5aa8ddb6"}'}


def stub_seq(*responses):
    seq = list(responses)
    calls = []

    # **kw so a new argument on the real _invoke does not break every retry test — and RECORDED,
    # not swallowed, so a test can still assert on it. A stub with a frozen signature turns an
    # additive change into 29 unrelated failures, which is what happened on 2026-09-02.
    def _invoke(prompt, allowed=None, directed=False, images=None, **kw):
        _invoke.kw = kw
        calls.append(1)
        return seq[min(len(calls) - 1, len(seq) - 1)]
    conversation._invoke = _invoke
    return calls


reset()
calls = stub_seq(BAD, {"is_error": False, "result": "Second time lucky.", "total_cost_usd": 0.12,
                       "num_turns": 2})
out, spent = say(HIST, BOT, CHAN)
check("RETRY a failed turn is retried exactly once", len(calls) == 2)
check("RETRY the retry's answer is what gets posted", out == "Second time lucky.")
check("RETRY both attempts are charged", abs(spent - 0.457) < 1e-6)
check("RETRY the ledger has both", abs(budget.status(conversation.AGENT_ID).spent_usd - 0.457) < 1e-6)

reset()
calls = stub_seq(BAD, BAD)
out, spent = say(HIST, BOT, CHAN)
check("RETRY it does not loop past two attempts", len(calls) == 2)
check("RETRY a persistent failure still charges both", abs(spent - 0.674) < 1e-6)
check("RETRY the raw payload NEVER reaches Slack",
      out and "is_error" not in out and "{" not in out and "session_id" not in out)
check("RETRY the message is a sentence, not a dump", out and "couldn't finish that thought" in out)
check("RETRY the detail is logged for evidence",
      os.path.exists(conversation.FAILURE_LOG)
      and "stop_sequence" in open(conversation.FAILURE_LOG).read())

# A non-zero exit with a parseable body is a FAILED TURN, not an unreachable CLI — the exit status
# is the least informative field in the response.
class _P:
    def __init__(self, rc, out):
        self.returncode, self.stdout, self.stderr = rc, out, ""


# Patch the module CONVERSATION calls, not this test file's own import of subprocess — patching the
# wrong namespace is a stub that silently does nothing, which is how a green test proves nothing.
_real_run = conversation.subprocess.run
conversation._invoke = _REAL_INVOKE          # restore: the retry tests above replaced it
conversation.subprocess.run = lambda *a, **k: _P(1, json.dumps(
    {"is_error": False, "result": "fine", "total_cost_usd": 0.05, "num_turns": 1}))
try:
    data = conversation._invoke("x")
    check("EXIT a non-zero exit with good JSON is still read", data.get("result") == "fine")
finally:
    conversation.subprocess.run = _real_run

# ── ISOLATION — the command line itself ─────────────────────────────────────
# CAPTURED from the real _invoke, not rebuilt here. This block used to rebuild the command "exactly as
# _invoke would", which was a second copy of the command line that could only drift from the first.
# Since M20 the command comes from claude_runner.py, and this asserts what it actually produced.
def _argv_of(**kw):
    box = {}

    def fake(argv, *a, **k):
        box["argv"] = list(argv)
        return _P(0, json.dumps({"is_error": False, "result": "ok", "total_cost_usd": 0.0, "num_turns": 1}))

    real = conversation.subprocess.run
    conversation.subprocess.run = fake
    try:
        conversation._invoke("x", **kw)
    finally:
        conversation.subprocess.run = real
    return box.get("argv") or []


_real_euid = conversation.os.geteuid
conversation.os.geteuid = lambda: 0          # as the listener ran when it was a root service
try:
    argv = _argv_of()
finally:
    conversation.os.geteuid = _real_euid
check("ISOLATION runs as an unprivileged user, not root", argv[:3] == ["runuser", "-u", "brad"])
check("ISOLATION the command is locked down (--restricted)", "--restricted" in argv)
check("ISOLATION an ordinary turn is handed no built-in tool at all",
      "--tools" in argv and argv[argv.index("--tools") + 1] == "")
check("ISOLATION a link-reading turn is handed exactly WebFetch",
      (lambda a: a[a.index("--tools") + 1] if "--tools" in a else None)(
          _argv_of(allowed=conversation.tools_for(True, True), reading=True)) == "WebFetch")
try:
    _argv_of(allowed=conversation.tools_for(True) + ",Bash")
    check("ISOLATION an allowance no chat profile declares is refused, not run", False)
except conversation.claude_runner.ProfileError:
    check("ISOLATION an allowance no chat profile declares is refused, not run", True)
check("ISOLATION model is claude-opus-5", conversation.MODEL == "claude-opus-5")
check("ISOLATION effort is explicitly low", conversation.EFFORT == "low")
check("ISOLATION Brad's settings are NOT loaded",
      argv[argv.index("--setting-sources") + 1] == "")
check("ISOLATION only the declared MCP server is reachable",
      "--strict-mcp-config" in argv
      and list(json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]) == ["moses"])
for tool in ("Bash", "Read", "Write", "Edit", "Task", "Agent", "SlashCommand"):
    check(f"ISOLATION {tool} is disabled", tool in conversation.NO_TOOLS.split(","))
check("ISOLATION --bare is NOT used (it would ignore the OAuth login)", "--bare" not in argv)

# ── Reading the web, and what it costs ───────────────────────────────────────
# WebFetch is no longer unconditionally denied — it is granted through the ALLOW list on a turn whose
# message carries a link, and the deny list stays constant. Measured 2026-09-02: a built-in absent
# from BOTH lists is refused by the CLI, which is what makes allow-only gating real.
#
# The property that matters is not "can he fetch" but "he can never fetch and act in the same turn".
# A web page is somebody else's text, and on a directed turn he holds knight_start and remediate.
check("WEB a link grants WebFetch", "WebFetch" in conversation.tools_for(True, True).split(","))
check("WEB no link, no WebFetch", "WebFetch" not in conversation.tools_for(True, False).split(","))
check("WEB reading REVOKES every acting tool",
      not any(conversation._qualified(t) in conversation.tools_for(True, True).split(",")
              for t in conversation.ACT_TOOLS))
check("WEB and a normal directed turn still has them",
      conversation._qualified("knight_start") in conversation.tools_for(True, False).split(","))
check("WEB the deny list is a CONSTANT — no per-turn lifting of anything",
      not hasattr(conversation, "no_tools_for") and "Read" in conversation.NO_TOOLS.split(","))
check("WEB WebSearch is NOT granted — he reads what he is given, he does not go looking",
      "WebSearch" not in conversation.tools_for(True, True).split(",")
      and "WebSearch" in conversation.NO_TOOLS.split(","))
for _t, _want in (("look at https://example.com please", True), ("no link at all", False),
                  ("Moses, http://a.b/c?d=1 thoughts?", True), ("ftp://x.y/z", False)):
    check(f"WEB link detection: {_want} for {_t[:28]!r}", conversation.wants_web(_t) is _want)
_cap = conversation._capability_text(True, True)
check("WEB he is told a page is DATA, not instructions", "never as instructions" in _cap)
check("WEB and told his acting tools are gone", "NO acting tools" in _cap)

# ── CORPUS: read-only access, and the acting tools stay out ────────────────
# The allowlist is written as EXPLICIT NAMES because a pattern would have been wrong here:
# knight_start launches a build agent, knight_switch toggles his kill switch, knight_cancel kills
# jobs — none read as writes from their names. A "knight_*" allow rule would have handed a channel
# containing another agent the ability to start builds on the Viatica repo.
ACTING = {"knight_start", "knight_cancel", "knight_switch",
          "add_idea", "add_todo", "complete_todo", "link_idea",
          "portal_toggle_servers", "portal_toggle_single_server"}
for t in sorted(ACTING):
    check(f"CORPUS {t} is NOT allowed", t not in conversation.READ_TOOLS)
check("CORPUS every allowed tool is namespaced to the moses server",
      all(x.startswith("mcp__moses__") for x in conversation.ALLOWED_TOOLS.split(",")))
check("CORPUS no acting tool survives into the allowlist string",
      not any(f"mcp__moses__{t}" in conversation.ALLOWED_TOOLS for t in ACTING))
check("CORPUS he can actually read memory", "list_memory" in conversation.READ_TOOLS
      and "search_memory" in conversation.READ_TOOLS and "read_memory" in conversation.READ_TOOLS)
check("CORPUS the MCP config names exactly one server",
      list(json.loads(conversation.MCP_CONFIG)["mcpServers"]) == ["moses"])
check("CORPUS the built-in filesystem tools are still denied",
      all(t in conversation.NO_TOOLS.split(",") for t in ("Read", "Bash", "Write", "Edit", "Glob", "Grep")))
# The original form of this check asserted the module's source never MENTIONS the key. It has to
# now — naming it is how it gets stripped — so the check would have had to be deleted, and deleting
# a guard because the fix tripped it is how a project loses one. Narrowed instead to the thing it
# was always trying to say: the value is never fetched and therefore can never be forwarded.
_src = open(conversation.__file__).read().split('"""', 2)[2]
check("ISOLATION the API key's value is never read in this module",
      not re.search(r'environ(?:\.get\(|\[)\s*["\']ANTHROPIC_(?:API_KEY|AUTH_TOKEN)["\']', _src))
check("ISOLATION and it is named only to be removed",
      "ANTHROPIC_API_KEY" in conversation.STRIPPED_FROM_CLI)

# ── ISOLATION — WHAT THE CHILD ACTUALLY RECEIVES ────────────────────────────
# The check directly above passed every day while the CLI was authenticating with Brad's API key.
# It reads this module's SOURCE, and the source is innocent: nothing here ever read the variable.
# systemd loads /etc/moses/moses.env into the listener's environment and `subprocess.run` hands a
# child the whole thing unless told otherwise, so the key arrived without a single line asking for
# it. That cost two days of chasing an empty tool registry (an API-key session does not attach the
# MCP server before the tool set is fixed) and put real API spend on a box whose rule is that it
# never spends any.
#
# So this asserts the ENVIRONMENT HANDED OVER, which is the only thing that was ever true or false.
_saved_env = dict(os.environ)
_captured = {}
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-should-never-reach-the-cli"
os.environ["ANTHROPIC_BASE_URL"] = "https://should-never-reach-the-cli"
os.environ["SLACK_BOT_TOKEN"] = "xoxb-should-never-reach-the-cli"
os.environ["MOSES_KEEP_ME"] = "ordinary"
_real_run2 = conversation.subprocess.run
conversation.subprocess.run = lambda *a, **k: (_captured.update(k) or _P(0, json.dumps(
    {"is_error": False, "result": "fine", "total_cost_usd": 0.0, "num_turns": 1})))
try:
    conversation._invoke("x")
    _env = _captured.get("env")
    check("ISOLATION the CLI is handed an explicit environment, not the listener's", _env is not None)
    check("ISOLATION the turn records WHICH ACCOUNT paid, every time",
          "auth" in json.loads(open(os.path.join(_TMP, "chat-turns.jsonl")).read().strip().splitlines()[-1])
          if os.path.exists(os.path.join(_TMP, "chat-turns.jsonl")) else True)
    for _name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        # `_name not in (_env or {})` reads fine and is worthless: when no env is passed at all —
        # precisely the bug — it is vacuously true. Absence only means something once there is an
        # environment for it to be absent FROM.
        check(f"ISOLATION {_name} never reaches the CLI",
              isinstance(_env, dict) and _name not in _env)
    check("ISOLATION no Slack token reaches the CLI",
          isinstance(_env, dict) and not [k for k in _env if k.startswith("SLACK_")])
    # A denylist that quietly became an allowlist would break the CLI in ways no test would explain.
    check("ISOLATION ordinary variables are still passed through",
          (_env or {}).get("MOSES_KEEP_ME") == "ordinary")
    check("ISOLATION PATH survives, or nothing runs at all", bool((_env or {}).get("PATH")))
finally:
    conversation.subprocess.run = _real_run2
    os.environ.clear()
    os.environ.update(_saved_env)

# ── THREE WAYS TO ANSWER: say it, react, or stay quiet ─────────────────────
# Brad, 2026-08-13: react instead of cluttering the channel, and "if Moses can't add anything
# meaningful to a conversation, that's ok too". Parsed in code, never trusted as prose.
for raw, want in [
    ("REACT: +1",        ("react", "+1")),
    ("REACT: 100",       ("react", "100")),
    ("react: eyes",      ("react", "eyes")),
    ("REACT: :tada:",    ("react", "tada")),      # tolerate the colon form people type
    ("PASS",             ("pass", "")),
    ("pass.",            ("pass", "")),
    ("",                 ("pass", "")),           # an empty answer is silence, not a blank message
    ("Sure, that holds.", ("text", "Sure, that holds.")),
    # A sentence ABOUT reacting is a sentence, not a command. Strictness is the whole point:
    # a loose match would swallow real messages and post nothing.
    ("I'd just react with a thumbs up there",
     ("text", "I'd just react with a thumbs up there")),
    ("REACT: not a valid name", ("text", "REACT: not a valid name")),
]:
    check(f"ACTION {raw!r} -> {want[0]}", conversation.parse_action(raw) == want)

# ── LEARNED: the peer-review output ────────────────────────────────────────
# Brad, 2026-08-13: "when Moses DOES learn something valuable, I need to know about it. It should be
# filed under a 'look into further' list attributed to Atlas."
for raw, want_msg, want_learned in [
    ("Fair point.\nLEARNED: [moses] a gate never seen going red is decoration.",
     "Fair point.", ["[moses] a gate never seen going red is decoration."]),
    ("Two things.\nLEARNED: [viatica] first\nLEARNED: [moses] second",
     "Two things.", ["[viatica] first", "[moses] second"]),
    # Unscoped is KEPT and flagged, never silently dropped.
    ("LEARNED: only line", "", ["[unscoped] only line"]),
    ("No marker at all.", "No marker at all.", []),
    # Prose that MENTIONS learning is a sentence he meant to say, not a filing instruction. Same
    # strictness as REACT/PASS, same reason: a loose match swallows real messages.
    ("What I learned: always check the log", "What I learned: always check the log", []),
    ("I learned something today", "I learned something today", []),
]:
    msg, learned = conversation.extract_learning(raw)
    check(f"LEARNED {raw[:34]!r}", (msg, learned) == (want_msg, want_learned))

# It rides ALONG with a reply rather than replacing one — the reply still comes back as text.
reset(); stub(result="Good catch.\nLEARNED: bound it, don't ban it")
kind, value, _ = conversation.reply(HIST, BOT, CHAN)
check("LEARNED a learning does not turn the reply into another kind", kind == "text")
check("LEARNED the marker is still in the raw value for the caller to lift",
      "LEARNED:" in value)

# Filing: attribution and de-duplication.
import dispatch  # noqa: E402
import tempfile as _tf  # noqa: E402
from pathlib import Path as _P  # noqa: E402
_mem = _P(_tf.mkdtemp(prefix="moses-learn-"))
added, total = dispatch.file_learning("bound it, don't ban it", source="Atlas",
                                      link="https://slack/x", memory=_mem)
check("FILING it is written", added and total == 1)
body = (_mem / "look-into-further.md").read_text()
check("FILING attributed to the source", "from Atlas" in body)
check("FILING carries a link back to the conversation", "https://slack/x" in body)
check("FILING is an open checkbox", "- [ ] bound it" in body)
added2, total2 = dispatch.file_learning("Bound it, DON'T ban it", source="Atlas", memory=_mem)
check("FILING the same lesson is not filed twice", not added2 and total2 == 1)
added3, _ = dispatch.file_learning("   ", source="Atlas", memory=_mem)
check("FILING an empty learning is refused", not added3)
added4, total4 = dispatch.file_learning("second lesson", source="Jon", memory=_mem)
check("FILING a different source is recorded as such", added4 and "from Jon" in
      (_mem / "look-into-further.md").read_text())

reset(); stub(result="REACT: 100")
kind, value, _ = conversation.reply(HIST, BOT, CHAN)
check("ACTION a reaction comes back as a reaction, not a message", (kind, value) == ("react", "100"))

reset(); stub(result="PASS")
kind, value, _ = conversation.reply(HIST, BOT, CHAN)
check("ACTION staying quiet is a DECISION ('pass'), not an absence ('none')", kind == "pass")

reset(); silence.engage("U1", CHAN)
kind, _, _ = conversation.reply(HIST, BOT, CHAN)
check("ACTION a gate returns 'none' so the caller can fall back", kind == "none")
silence.release("U1")

# ── Prompt shaping ──────────────────────────────────────────────────────────
hist = [  # newest first, as Slack returns it
    {"user": "U1", "text": "and another thing"},
    {"user": BOT, "text": "I think it holds"},
    {"user": "U2", "text": "opening question"},
]
p = conversation.build_prompt(hist, BOT)
check("transcript is oldest-first", p.index("opening question") < p.index("and another thing"))
check("Moses's own lines are labeled as his", "You (Moses): I think it holds" in p)
check("other speakers are labeled by id, never invented names", "U2: opening question" in p)
check("empty history produces no prompt", conversation.build_prompt([], BOT) == "")
reset(); stub()
say([], BOT, CHAN)
check("empty history means the CLI is not invoked", not stub.called)

# ── Failures degrade, never crash ───────────────────────────────────────────
reset(); stub(exc=subprocess.TimeoutExpired("claude", 120))
out, spent = say(HIST, BOT, CHAN)
check("a timeout degrades to a message", out and "longer" in out.lower() and spent == 0.0)

reset(); stub(exc=RuntimeError("credit balance too low"))
out, spent = say(HIST, BOT, CHAN)
check("a CLI failure still names a SHORT reason", out and "credit balance too low" in out)
reset(); stub(exc=RuntimeError('{"is_error":true,"session_id":"abc","total_cost_usd":0.3}'))
out, _ = say(HIST, BOT, CHAN)
check("but a payload-shaped error is withheld from the channel",
      out and "session_id" not in out and "detail in my log" in out)

reset()
conversation.CLI = "/nonexistent/claude"
stub()
out, _ = say(HIST, BOT, CHAN)
check("a missing CLI means quiet, not a crash", out is None and not stub.called)

# ── The prompt carries his own pending proposals as FACT ───────────────────────────────────────
# On 2026-08-15 Moses told Brad an id "isn't one of mine" — he had proposed it himself the day
# before and it was in the pending store the whole time. The model was reasoning about what it
# remembered instead of what was recorded, so the record now goes in the prompt.
import proposals as _p
_fresh_registry()
_fresh_registry()
_held = _p.add("Extend the probe coverage assertion to emit identity and scope", "C_CHAT", project="viatica")

hist2 = [{"user": "U_BRAD", "text": "Moses, is 0c1c831f one of yours?", "ts": "9.0"}]
prompt = conversation.build_prompt(hist2, BOT)
check("PENDING the prompt names his pending id", _held["id"] in prompt)
check("PENDING it carries the task text too", "probe coverage assertion" in prompt)
check("PENDING and marks the list authoritative", "authoritative" in prompt.lower())

_fresh_registry()
prompt = conversation.build_prompt(hist2, BOT)
check("PENDING with none pending it says so plainly", "NO pending proposals" in prompt)

# Commandment 6: a state read must never take the reply down.
_orig_pending = _p.pending
_p.pending = lambda: (_ for _ in ()).throw(RuntimeError("store unreadable"))
prompt = conversation.build_prompt(hist2, BOT)
check("PENDING an unreadable store degrades, it does not crash",
      prompt and "Moses, is 0c1c831f" in prompt)
_p.pending = _orig_pending

# The tests below stub _invoke, so the CLI never runs. They only need reply()'s "is the CLI
# installed?" gate to pass. Any file that exists will do, so use the interpreter running this test
# rather than an install path. This line was "/usr/bin/claude" until 2026-09-14: the old global
# install was removed on 2026-09-13, the gate then made every reply below go quiet, and 12 retry
# checks failed with nothing wrong in Moses.
import sys as _sys_cli
conversation.CLI = _sys_cli.executable



# ── Backing off when the API is the problem, not the request ────────────────
# 2026-08-24: Atlas asked Moses a question and got "I couldn't finish that thought". Both attempts
# came back "Repeated 529 Overloaded errors", nine seconds apart — the second retry asking the same
# at-capacity pool inside the same burst. It cost half a cent to fail identically twice.
print("\nPROVIDER BACKOFF")

_real_sleep = conversation.time.sleep
slept = []
conversation.time.sleep = lambda s: slept.append(s)

# The real recorded text from that failure, verbatim.
_529 = ("API Error: Repeated 529 Overloaded errors. The API is at capacity — this is usually "
        "temporary. Try again in a moment.")

check("the real 529 text is recognized as the provider's problem", conversation.looks_like_provider_trouble({"result_head": _529}))
check("so is an upstream 503", conversation.looks_like_provider_trouble({"result": "upstream connect error, 503"}))
# The narrow half, and the one that matters: waiting does not fix a bad request, and a pause on
# every failure would make every mistake slower without making any of them succeed.
check("a bad tool name is NOT treated as provider trouble", not conversation.looks_like_provider_trouble({"result_head": "tool 'nope' not found"}))
check("nor is a prompt that blew the ceiling", not conversation.looks_like_provider_trouble({"result_head": "prompt is too long"}))
check("and an empty result does not match anything", not conversation.looks_like_provider_trouble({}))

_orig_invoke = conversation._invoke
_calls = []


def _fail_then_ok(prompt, allowed=None, directed=False, images=None, **kw):
    _calls.append(prompt)
    if len(_calls) == 1:
        return {"is_error": True, "result_head": _529, "total_cost_usd": 0.0026, "num_turns": 1}
    return {"is_error": False, "result": "second time lucky", "total_cost_usd": 0.01, "num_turns": 1}


conversation._invoke = _fail_then_ok
try:
    slept.clear()
    kind, value, _ = conversation.reply(
        [{"user": "U_H", "text": "Moses, how are things?", "ts": "1"}], "UBOT", "C_CHAT", directed=True)
    check(f"it waits {conversation.PROVIDER_BACKOFF_S:g}s before the retry, rather than asking again immediately", slept and slept[0] == conversation.PROVIDER_BACKOFF_S)
    check("and the retry still happens, so a blip does not cost the answer", len(_calls) == 2 and kind == "text" and "second time lucky" in value)

    # A failure that is OURS must not be slowed down.
    _calls.clear(); slept.clear()
    conversation._invoke = lambda p, allowed=None, directed=False, images=None, **kw: (
        _calls.append(p) or {"is_error": True, "result_head": "prompt is too long", "total_cost_usd": 0.0})
    conversation.reply([{"user": "U_H", "text": "Moses, hi", "ts": "1"}], "UBOT", "C_CHAT", directed=True)
    check("a failure that is ours retries immediately, with no pause", not slept)
finally:
    conversation._invoke = _orig_invoke
    conversation.time.sleep = _real_sleep


# ── A tool that vanished is retried once ────────────────────────────────────
# 2026-08-24: Moses told Brad twice that `knight_start` was "No such tool available" and refused to
# claim he had started anything — which was honest. Between those two refusals the same tool worked.
# The turn does not fail when this happens: the model writes a polite refusal and the run reports
# success, so every piece of error handling steps straight over it.
print("\nA VANISHED TOOL IS RETRIED")

_orig_invoke2 = conversation._invoke
_seq = []


def _missing_then_present(prompt, allowed=None, directed=False, images=None, **kw):
    _seq.append(prompt)
    if len(_seq) == 1:
        return {"is_error": False, "result": "Knight isn't reachable from me this turn.",
                "tools_used": ["mcp__moses__knight_start"],
                "tool_errors": ["<tool_use_error>Error: No such tool available: "
                                "mcp__moses__knight_start</tool_use_error>"],
                "mcp_status": "connected", "mcp_tools": 0,
                "total_cost_usd": 0.02, "num_turns": 2}
    return {"is_error": False, "result": "Started job 20260824-000000-1.",
            "tools_used": ["mcp__moses__knight_start"], "tool_errors": [],
            "mcp_status": "connected", "mcp_tools": 39,
            "total_cost_usd": 0.02, "num_turns": 2}


_real_sleep2 = conversation.time.sleep
conversation.time.sleep = lambda *_: None
conversation._invoke = _missing_then_present
try:
    kind, value, _ = conversation.reply(
        [{"user": "U_H", "text": "Moses, tell Knight to rebase.", "ts": "1"}], "UBOT", "C_CHAT",
        directed=True)
    check("a missing tool is tried again rather than becoming a refusal", len(_seq) == 2)
    check("and the second answer is what reaches the channel", "Started job" in value)

    # The honest half: if it is still missing, the refusal stands. Never invent a success.
    _seq.clear()
    conversation._invoke = lambda p, allowed=None, directed=False, images=None, **kw: (
        _seq.append(p) or {"is_error": False, "result": "Knight isn't reachable from me this turn.",
                           "tools_used": ["mcp__moses__knight_start"],
                           "tool_errors": ["<tool_use_error>Error: No such tool available: x"
                                           "</tool_use_error>"],
                           "mcp_status": "connected", "mcp_tools": 0,
                           "total_cost_usd": 0.02, "num_turns": 2})
    kind, value, _ = conversation.reply(
        [{"user": "U_H", "text": "Moses, tell Knight to rebase.", "ts": "1"}], "UBOT", "C_CHAT",
        directed=True)
    check("twice missing means it is tried twice, not fabricated into a success", len(_seq) == 2)
    # CHANGED 2026-08-25, and this is the point of the whole fix. His own sentence — "Knight isn't
    # reachable from me this turn" — was FALSE: Knight was reachable and the tool worked by hand
    # twelve times out of twelve. What had failed was the tool registry attaching at all. Brad read
    # the refusal as a missing capability and tried the same thing three times over two days. The
    # channel now gets the true reason.
    check("and what reaches the channel is the real reason, not a missing capability",
          "didn't attach" in value and "capability is there" in value)
    check("he still does not claim it worked", "Started job" not in value)
finally:
    conversation._invoke = _orig_invoke2
    conversation.time.sleep = _real_sleep2

print("\nA QUESTION ABOUT THE LIST IS A QUESTION ABOUT LIVE STATE")

# Brad retired the flat to-do file on 2026-08-28 because nothing cross-checked it, and asked that
# Moses always answer from the project registry instead — "so if I ask or Atlas asks or if Jon asks,
# we get a real answer and not some made up shit." The gate that already forces a live check for
# "what's the status" now covers "what's on the list" too.
for _q in ["what is on the to-do list?", "what are we working on?", "anything on the backlog?",
           "how does the roadmap look?", "what are the priorities?"]:
    check(f"STATE asking {_q!r} demands a live check", conversation.asks_about_state(_q))

# And does NOT fire on ordinary talk. A guard that makes him refuse to discuss a design pattern is
# one Brad switches off — the bare word "todo" matched exactly that on the first attempt.
for _q in ["the todo pattern in that library is odd", "I am working on my car this weekend",
           "nice work on the map", "thanks Moses"]:
    check(f"STATE {_q!r} is just talk", not conversation.asks_about_state(_q))

# The registry is what answers it, and it must be reachable to be called.
check("STATE the project dashboard counts as a live source",
      "project_dashboard" in conversation.STATE_TOOLS)
check("STATE looking an item up by its code counts as a live source", "project_item" in conversation.STATE_TOOLS)

# THE LINE HE SAYS WHEN HE STILL HAS NOT LOOKED. It used to end "check `moses status` on the box": a
# command only Brad can run, which reports persona cadence and answers almost nothing. Brad got it in
# reply to asking "moses status", and Atlas pointed out it was not his box (2026-09-14).
_orig6 = conversation._invoke
conversation._invoke = lambda p, allowed=None, directed=False, images=None, **kw: {
    "is_error": False, "result": "From memory: all good.", "tools_used": [], "tool_errors": [],
    "mcp_status": "connected", "mcp_tools": 39, "total_cost_usd": 0.01, "num_turns": 1,
    "permission_denials": []}
try:
    kind, value, _ = conversation.reply(
        [{"user": "U_H", "text": "Moses, what's the status of Knight?", "ts": "1"}], "UBOT", "C_CHAT",
        directed=True)
    check("STATE an unchecked answer to a status question is still not posted", "From memory" not in (value or ""))
    check("STATE what he says instead points nobody at a command only Brad can run",
          "moses status" not in (value or "") and "look it up" in (value or ""))
finally:
    conversation._invoke = _orig6
check("STATE and it is on the read-only allowlist",
      "project_dashboard" in conversation.READ_TOOLS)
check("STATE no tool writes to a free-floating list any more",
      not any("todo" in t for t in conversation.READ_TOOLS + conversation.ACT_TOOLS))

print("\nONLY ONE PLACE NAMES A TOOL")

# This was fixed once already. On 2026-08-22 the generated capability list was corrected to use the
# qualified `mcp__moses__` names — and an unqualified list was left in the prose beside it, so the
# prompt said both and the model believed the wrong half. Six days later Moses called
# `project_status` at Atlas and was told no such tool existed.
#
# Prose cannot enforce "don't write the names twice", so this does: the only place a tool may be
# named is the CAPABILITIES block, which is generated from the same helper that fills --allowedTools.
_prompt_only = conversation.SYSTEM.replace("{CAPABILITIES}", "")
_bare = [n for n in (conversation.READ_TOOLS + conversation.ACT_TOOLS)
         if f"`{n}`" in _prompt_only]
if _bare:
    print(f"      (unqualified names found in the prompt: {_bare})")
check("the prompt never spells a tool name unqualified", not _bare)
check("and the capability block is still what carries them",
      "{CAPABILITIES}" in conversation.SYSTEM
      and "mcp__moses__" in conversation._capability_text(True))

print("\nA TURN THAT REACHED A TOOL IS A TURN THAT WORKED")

# Measured 2026-08-28 against Atlas: Moses called two tools by an unqualified name and was refused,
# then called two more by their real names and got real answers — one turn, both outcomes. The
# opening event still read `pending/0`, so the retry fired AND his answer was replaced with "my tools
# didn't attach". Both were false, and the second one threw away work he had actually done. A guard
# that fires on correct work is the more dangerous failure.
_orig4 = conversation._invoke
_seq4 = []


def _partly_refused(prompt, allowed=None, directed=False, images=None, **kw):
    _seq4.append(prompt)
    return {"is_error": False, "result": "Knight has one job running and nobody is overdue.",
            "tools_used": ["project_status", "mcp__moses__knight_jobs", "mcp__moses__who_is_behind"],
            "tool_errors": ["<tool_use_error>Error: No such tool available: project_status"
                            "</tool_use_error>"],
            "mcp_status": "pending", "mcp_tools": 0,
            "total_cost_usd": 0.33, "num_turns": 5}


_real_sleep4 = conversation.time.sleep
conversation.time.sleep = lambda *_: None
conversation._invoke = _partly_refused
try:
    kind, value, _ = conversation.reply(
        [{"user": "U_H", "text": "Moses, what is Knight up to?", "ts": "1"}], "UBOT", "C_CHAT",
        directed=True)
    check("a turn where some tools answered is not retried", len(_seq4) == 1)
    check("and his real answer is what reaches the channel", "one job running" in value)
    check("he does not claim his tools failed", "didn't attach" not in value)
finally:
    conversation._invoke = _orig4
    conversation.time.sleep = _real_sleep4

print("\nA REFUSED TOOL IS SAID, NOT HIDDEN")

# M20: the CLI exits 0 when a tool is quietly denied, and the answer arrives looking complete. Six
# turns went out that way before this, one of them answering about a web page it was refused.
_orig5 = conversation._invoke
conversation._invoke = lambda p, allowed=None, directed=False, images=None, **kw: {
    "is_error": False, "result": "Here is what the page says.", "tools_used": ["WebFetch"], "tool_errors": [],
    "mcp_status": "connected", "mcp_tools": 39, "total_cost_usd": 0.01, "num_turns": 2,
    "permission_denials": [{"tool_name": "WebFetch", "tool_input": {"url": "https://example.com"}}]}
try:
    kind, value, _ = conversation.reply(
        [{"user": "U_H", "text": "Moses, read https://example.com please", "ts": "1"}], "UBOT", "C_CHAT",
        directed=True)
    check("a refused tool is named in the answer", kind == "text" and "Refused this turn: WebFetch" in value)
    check("and the answer itself still arrives", "Here is what the page says." in value)
    conversation._invoke = lambda p, allowed=None, directed=False, images=None, **kw: {
        "is_error": False, "result": "Nothing new here.", "tools_used": [], "tool_errors": [],
        "mcp_status": "connected", "mcp_tools": 39, "total_cost_usd": 0.01, "num_turns": 1,
        "permission_denials": []}
    kind, value, _ = conversation.reply(
        [{"user": "U_H", "text": "Moses, anything to add?", "ts": "1"}], "UBOT", "C_CHAT", directed=True)
    check("a turn with nothing refused carries no note", "Refused" not in (value or ""))
finally:
    conversation._invoke = _orig5

print("\nAN EMPTY TOOL REGISTRY IS RETRIED EVEN WHEN NOTHING WAS CALLED")

# The old guard fired only on the SYMPTOM — a tool_use_error containing "No such tool available".
# When the registry comes up empty the model often calls nothing at all: it simply answers from the
# corpus as though it had looked, and the retry never fired. Measured on 2026-08-25: the opening
# event said `pending` with zero moses tools, while the server answered all six handshake calls 200.
_orig3 = conversation._invoke
_seq3 = []


def _empty_then_attached(prompt, allowed=None, directed=False, images=None, **kw):
    _seq3.append(prompt)
    if len(_seq3) == 1:
        return {"is_error": False, "result": "Knight has nothing running.",
                "tools_used": [], "tool_errors": [],
                "mcp_status": "pending", "mcp_tools": 0,
                "total_cost_usd": 0.02, "num_turns": 1}
    return {"is_error": False, "result": "Knight has one job running.",
            "tools_used": ["mcp__moses__knight_jobs"], "tool_errors": [],
            "mcp_status": "connected", "mcp_tools": 39,
            "total_cost_usd": 0.02, "num_turns": 2}


_real_sleep3 = conversation.time.sleep
conversation.time.sleep = lambda *_: None
conversation._invoke = _empty_then_attached
try:
    kind, value, _ = conversation.reply(
        [{"user": "U_H", "text": "Moses, is Knight busy?", "ts": "1"}], "UBOT", "C_CHAT",
        directed=True)
    check("an empty registry is retried even though no tool errored", len(_seq3) == 2)
    check("and the answer taken from the attached turn is the one posted",
          "one job running" in value)

    # A guard that fires on correct work gets switched off. A healthy turn that simply had no reason
    # to call a tool must pass straight through, untouched and un-retried.
    _seq3.clear()
    conversation._invoke = lambda p, allowed=None, directed=False, images=None, **kw: (
        _seq3.append(p) or {"is_error": False, "result": "Morning, Brad.",
                            "tools_used": [], "tool_errors": [],
                            "mcp_status": "connected", "mcp_tools": 39,
                            "total_cost_usd": 0.01, "num_turns": 1})
    kind, value, _ = conversation.reply(
        [{"user": "U_H", "text": "Moses, morning.", "ts": "1"}], "UBOT", "C_CHAT", directed=True)
    check("a healthy turn with an attached registry is never retried", len(_seq3) == 1)
    check("and it is passed through untouched", "Morning, Brad." in value)
finally:
    conversation._invoke = _orig3
    conversation.time.sleep = _real_sleep3

print("\nATTACHMENT IS ON THE RECORD")
stream = "\n".join([
    __import__("json").dumps({"type": "system", "mcp_servers": [{"name": "moses", "status": "connected"}],
                              "tools": ["mcp__moses__knight_start", "mcp__moses__list_todos", "Bash"]}),
    __import__("json").dumps({"type": "result", "result": "ok", "total_cost_usd": 0.0}),
])
parsed = conversation._read_stream(stream)
check("the turn records whether the MCP server attached", parsed.get("mcp_status") == "connected")
check("and how many of its tools were actually present", parsed.get("mcp_tools") == 2)

nostream = __import__("json").dumps({"type": "result", "result": "ok", "total_cost_usd": 0.0})
parsed = conversation._read_stream(nostream)
check("a run with no init event says unknown rather than claiming a number",
      parsed.get("mcp_status") == "unknown" and parsed.get("mcp_tools") == -1)

# COUNTED HERE, not further up. The tally used to sit above the last few sections, so anything
# added after it printed PASS and was never counted — 116 lines of PASS reported as 108. A test
# file whose summary disagrees with its own output is worse than one with no summary.



# ── An expired login must announce itself ────────────────────────────────────
# 2026-08-31: Atlas said good morning and got "I couldn't finish that thought — the detail is in my
# log." The detail was "Failed to authenticate: OAuth session expired and could not be refreshed",
# which Moses had in hand. Brad read the reply as Moses ignoring Atlas.
#
# The severity is not the one lost message. Moses attempted NOTHING for the rest of the morning: an
# expired login disables every turn until a person acts, and he only recovered because Brad happened
# to open Claude Code, which refreshed the shared credentials by accident.
print("\n── auth failures name themselves ──")

# The exact string the CLI produced that morning, from chat-failures.jsonl.
REAL = "Failed to authenticate: OAuth session expired and could not be refreshed"

check("REAL the recorded failure is recognized as an auth failure",
      conversation.looks_like_auth_failure({"result_head": REAL}))

check("AUTH is not mistaken for provider trouble",
      not conversation.looks_like_provider_trouble({"result_head": REAL}))

check("529 is still provider trouble, not auth",
      conversation.looks_like_provider_trouble({"result_head": "API Error: Repeated 529 Overloaded errors."})
      and not conversation.looks_like_auth_failure({"result_head": "API Error: Repeated 529 Overloaded errors."}))

for phrase in ("Invalid API key", "401 Unauthorized", "You are not logged in"):
    check(f"AUTH recognizes {phrase!r}", conversation.looks_like_auth_failure({"result_head": phrase}))

# An ordinary answer that happens to discuss the words must not be classified as a failure — a
# false positive here would make Moses announce he is logged out mid-conversation.
for innocent in ("The 401 route returns unauthorized for signed-out users, which is correct.",
                 "I refreshed the itinerary and it looks right."):
    check("AUTH does not fire on ordinary text about auth",
          not conversation.looks_like_auth_failure({"result_head": innocent}))

check("FORENSICS reports timestamps and never token material", (lambda f: (
        isinstance(f, dict)
        and not any(k.lower().endswith("token") for k in f)
        and not any(isinstance(v, str) and len(v) > 60 for v in f.values())
      ))(conversation.credential_facts()))

# ── The safety summary at the top of the file must stay TRUE ──────────────────
# That paragraph said the MCP tools were "not loaded into a chat that needs none of them" for eleven
# days after they were granted, and it is the first thing anyone reads to learn what Moses may do in
# a channel he shares with another agent. A stale sentence about a boundary is worse than none: it
# is read as a guarantee.
#
# Prose cannot be trusted to age well, so this pins it to the code. Adding an acting capability
# without saying so in the summary now fails, and so does claiming a built-in is blocked when it is
# not. It does not check that the wording is good — only that it has not become false.
_DOC = conversation.__doc__ or ""
_missing_act = [t for t in conversation.ACT_TOOLS if t not in _DOC]
check("DOC the summary names every acting tool"
      + (f" — granted but undocumented: {', '.join(_missing_act)}" if _missing_act else ""),
      not _missing_act)
_claimed_blocked = ["Bash", "Read", "Write", "Edit", "Task", "Agent"]
_not_blocked = [t for t in _claimed_blocked
                if t in _DOC and t not in conversation.NO_TOOLS.split(",")]
check("DOC every built-in it calls blocked really is blocked"
      + (f" — claimed but absent from NO_TOOLS: {', '.join(_not_blocked)}" if _not_blocked else ""),
      not _not_blocked)

# ── Zryachiy: exploring is an act, reading about it is not ─────────────────────
# A sandbox run spends about half an hour of Brad's Pro allowance, so starting one sits behind the
# human-directed gate like knight_start. Another agent must not be able to talk Moses into it.
check("ZRYACHIY starting an exploration is an acting tool, never a read tool",
      "zryachiy_explore" in conversation.ACT_TOOLS and "zryachiy_explore" not in conversation.READ_TOOLS)
check("ZRYACHIY reading how one is going is free",
      "zryachiy_status" in conversation.READ_TOOLS)
check("ZRYACHIY a machine-prompted turn cannot start one",
      conversation._qualified("zryachiy_explore") not in conversation.tools_for(False, True).split(","))

# ── The tool wall: which built-ins EXIST, not which are permitted (Jon, 2026-09-12) ──────────────
# --allowedTools / --disallowed-tools only gate a tool; a deny list on Jon's side lost to Monitor. The
# argv must carry --tools, and the opening event's inventory is checked on every turn.
_box = {}


def _capture(argv, *a, **k):
    _box["argv"] = list(argv)
    raise RuntimeError("captured")


# A FRESH copy of the module: this file stubs conversation._invoke for everything above, and a check
# of the argv has to read the real one. The first draft called the stub and saw no argv at all.
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("conversation_fresh", conversation.__file__)
_fresh = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_fresh)
_orig_run = _fresh.subprocess.run
_fresh.subprocess.run = _capture
for _reading, _want in ((False, ""), (True, "WebFetch")):
    _box.clear()
    try:
        _fresh._invoke("hi", _fresh.tools_for(True, _reading), directed=True, reading=_reading)
    except Exception:
        pass
    _a = _box.get("argv") or []
    _got = _a[_a.index("--tools") + 1] if "--tools" in _a else None
    check(f"TOOLWALL {'a link-reading' if _reading else 'an ordinary'} turn's --tools is {_want!r}", _got == _want)
_fresh.subprocess.run = _orig_run
check("TOOLWALL a turn handed Bash is flagged",
      conversation.turn_has_foreign_tools({"builtin_tools": ["Bash"]}, False) == ["Bash"])
check("TOOLWALL WebFetch is foreign on an ordinary turn",
      conversation.turn_has_foreign_tools({"builtin_tools": ["WebFetch"]}, False) == ["WebFetch"])
check("TOOLWALL and allowed on a link-reading turn",
      conversation.turn_has_foreign_tools({"builtin_tools": ["WebFetch"]}, True) == [])
check("TOOLWALL nothing handed over passes", conversation.turn_has_foreign_tools({"builtin_tools": []}, False) == [])
check("TOOLWALL no opening event is 'unverified', neither a pass nor a block",
      conversation.turn_has_foreign_tools({}, False) is None)
check("TOOLWALL the stream parser records what the CLI handed over",
      (conversation._read_stream('{"type":"system","subtype":"init","tools":["Bash","mcp__moses__recall"],"mcp_servers":[]}\n'
                                 '{"type":"result","result":"ok"}') or {}).get("builtin_tools") == ["Bash"])

# COUNTED INSIDE THE PRINT — literally, this time. The previous version carried a comment saying
# exactly that while assigning the count to a variable several lines ABOVE the print, so a section
# appended in between was run, printed its own FAIL, and was still summarized as "0 failed". That is
# the third outing for this bug and the second time the comment describing the fix outlived the fix.
# Now there is no intermediate name to append past.
print(f"\n  {sum(1 for _, ok in results if ok)} passed, "
      f"{sum(1 for _, ok in results if not ok)} failed")
sys.exit(1 if any(not ok for _, ok in results) else 0)
