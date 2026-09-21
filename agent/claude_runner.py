"""claude_runner — the ONE place a `claude -p` command line is built for any Moses agent.

M20 (Brad, 2026-09-14: "implement M20"). Five launchers each built their own command: Moses's chat,
the alarm diagnosis, Knight's builder, Zryachiy's reviewer and Zryachiy's explorer. The tool-wall fix
of 2026-09-13 had to land in five places and the probe had to check five. Jon's side collapsed theirs
into one runner the same week; this is ours. Python callers use build_argv(); knight-run and Viatica's
explorer use the `claude-run` wrapper beside this file.

WHAT EVERY COMMAND GETS, WITH NO OPT-OUT
  --restricted          no code-running tool or WebFetch unless --tools names it; settings files ignored
  --strict-mcp-config   only the MCP servers named here. Atlas measured that --restricted alone leaves
                        the calling session's own project servers attached.
  --tools               the inventory: exactly the built-ins the profile declares, "" for none

ONE DECLARATION, BOTH LISTS. A profile names what it may use once. --tools (what exists in the turn)
and --allowedTools (what is approved) are both built from it, so they cannot disagree. Atlas measured
the failure this prevents on 2026-09-14: under --restricted in -p mode, a tool that is in --tools but
not approved is silently denied, the turn still exits 0, and the answer comes from training data.

A CALLER CAN NARROW, NEVER WIDEN. There is no parameter that adds a tool. The few per-call inputs (a
system prompt, the explorer's own browser servers, the reviewer's job folder) are accepted only by the
profiles that declare them, and the explorer's browser approvals must fall inside its ceiling.

A GRANT CARRIES ITS REASON. An app that grants anything must say why in at least 20 characters, or
this module refuses to import. "Why does this app have Bash" is then answered in the diff.

A REFUSED TOOL IS NOT A SUCCESS. classify() returns "ok", "degraded" or "failed". Exit 0 with
permission_denials is DEGRADED, a third state rather than failed: a caller that retries on failure
would otherwise keep re-running a turn that is refused the same way every time.

NOTHING ELSE BUILDS ONE. claude_runner_test.py fails if any other file in the repo carries a tool-grant
flag, apart from a named allowlist in which every entry has a reason and must still be true.
"""
from __future__ import annotations

import sys as _sys, pathlib as _pl  # noqa: E401
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent / "lib"))
import moses_env as _env  # noqa: E402 — whose home, where Moses lives, the operator's settings
import os
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache

CLI = _env.claude_cli()
GUARDS = os.environ.get("KNIGHT_GUARDS", str(_env.ROOT / "knight/guards.env"))
NO_MCP = '{"mcpServers":{}}'
MIN_REASON = 20


class ProfileError(Exception):
    """A profile that cannot be built, or a caller asking for something its profile does not allow."""


@dataclass(frozen=True)
class Profile:
    app: str
    model: str
    effort: str
    builtins: tuple[str, ...] = ()   # built-in tools offered AND approved whole
    bash: tuple[str, ...] = ()       # Bash commands approved ("git diff:*"); Bash itself is then offered
    mcp: tuple[str, ...] = ()        # MCP tools approved, qualified (mcp__server__tool)
    deny: tuple[str, ...] = ()       # a belt only (--disallowedTools). The wall is the three lines above
    mcp_config: str = NO_MCP         # the MCP servers this app may reach; "" means the caller supplies them
    permission_mode: str = ""
    setting_sources: str = ""        # "" = none of Brad's settings
    output: str = "json"
    persist_session: bool = False
    exclude_dynamic_prompt: bool = False
    takes_system_prompt: bool = False
    takes_dirs: bool = False         # the caller may add --add-dir paths
    approve_ceiling: str = ""        # a regex every per-call MCP approval must match (the explorer only)
    never_approve: tuple[str, ...] = ()  # tool names refused even inside the ceiling

    def offered(self) -> list[str]:
        return list(self.builtins) + (["Bash"] if self.bash else [])

    def approved(self) -> list[str]:
        return list(self.builtins) + [f"Bash({c})" for c in self.bash] + list(self.mcp)

    def grants(self) -> bool:
        return bool(self.builtins or self.bash or self.mcp or self.approve_ceiling)


# ── The profiles ─────────────────────────────────────────────────────────────
# Tool NAMES stay where their rationale lives (conversation.py's READ_TOOLS and ACT_TOOLS, knight's
# guards.env). The GRANT, which app gets which of them and why, is here. Built lazily, so knight-run's
# wrapper never imports the chat code.

def _chat_fields(extra_mcp: tuple[str, ...] = (), builtins: tuple[str, ...] = ()) -> dict:
    import conversation as C
    return dict(model=C.MODEL, effort=C.EFFORT, builtins=builtins,
                mcp=tuple(C._qualified(t) for t in C.READ_TOOLS) + extra_mcp,
                deny=tuple(t for t in C.NO_TOOLS.split(",") if t), mcp_config=C.MCP_CONFIG,
                output="stream-json", exclude_dynamic_prompt=True, takes_system_prompt=True)


def _chat() -> Profile:
    return Profile("moses-chat", **_chat_fields())


def _chat_directed() -> Profile:
    import conversation as C
    return Profile("moses-chat-directed", **_chat_fields(extra_mcp=tuple(C._qualified(t) for t in C.ACT_TOOLS)))


def _chat_reading() -> Profile:
    import conversation as C
    return Profile("moses-chat-reading", **_chat_fields(builtins=tuple(C.WEB_TOOLS)))


def _diagnosis() -> Profile:
    import diagnose as D
    return Profile("moses-diagnosis", model=D.MODEL, effort=D.EFFORT,
                   deny=tuple(t for t in D.NO_TOOLS.split(",") if t),
                   exclude_dynamic_prompt=True, takes_system_prompt=True)


@lru_cache(maxsize=1)
def _guards() -> dict:
    """Knight's lists, read by sourcing guards.env exactly as knight-run does."""
    scalars = ["KNIGHT_TOOLS", "KNIGHT_REVIEW_TOOLS", "KNIGHT_MODEL", "KNIGHT_EFFORT",
               "KNIGHT_REVIEW_MODEL", "KNIGHT_REVIEW_EFFORT"]
    arrays = ["KNIGHT_ALLOW", "KNIGHT_DENY", "KNIGHT_REVIEW_ALLOW", "KNIGHT_REVIEW_DENY"]
    script = [f'source "{GUARDS}" >/dev/null 2>&1 || exit 3;']
    script += [f'printf "%s\\036" "${n}";' for n in scalars]
    script += [f'printf "%s\\037" "${{{a}[@]}}"; printf "\\036";' for a in arrays]
    r = subprocess.run(["bash", "-c", " ".join(script)], capture_output=True, text=True,
                       env={**os.environ, "MOSES_HOME": str(_env.HOME)})
    if r.returncode != 0:
        raise ProfileError(f"cannot read Knight's tool lists from {GUARDS}")
    parts = r.stdout.split("\x1e")
    out = dict(zip(scalars, parts[:len(scalars)]))
    for name, raw in zip(arrays, parts[len(scalars):]):
        out[name] = [x for x in raw.split("\x1f") if x]
    return out


def _split_grants(label: str, tools_csv: str, allow: list[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Turn guards.env's two lists into one declaration, refusing if they describe different tools."""
    offered = [t for t in tools_csv.split(",") if t]
    bash, named = [], []
    for a in allow:
        m = re.fullmatch(r"Bash\((.+)\)", a)
        if m:
            bash.append(m.group(1))
        elif re.fullmatch(r"[A-Za-z]+", a):
            named.append(a)
        else:
            raise ProfileError(f"{label}: cannot express the approval {a!r} in a profile")
    if "Bash" in offered and not bash:
        raise ProfileError(f"{label}: offers Bash but approves no Bash command")
    if bash and "Bash" not in offered:
        raise ProfileError(f"{label}: approves Bash commands but does not offer Bash")
    stray = [n for n in named if n not in offered]
    if stray:
        raise ProfileError(f"{label}: approves {', '.join(stray)} without offering them")
    return tuple(t for t in offered if t != "Bash"), tuple(bash)


def _knight_builder() -> Profile:
    g = _guards()
    builtins, bash = _split_grants("knight-builder", g["KNIGHT_TOOLS"], g["KNIGHT_ALLOW"])
    # No --add-dir, ever: his sandbox is his own clone, which has no remote (guards.env, top).
    return Profile("knight-builder", model=g["KNIGHT_MODEL"], effort=g["KNIGHT_EFFORT"],
                   builtins=builtins, bash=bash, deny=tuple(g["KNIGHT_DENY"]),
                   permission_mode="acceptEdits", persist_session=True)


def _reviewer() -> Profile:
    g = _guards()
    builtins, bash = _split_grants("zryachiy-reviewer", g["KNIGHT_REVIEW_TOOLS"], g["KNIGHT_REVIEW_ALLOW"])
    return Profile("zryachiy-reviewer", model=g["KNIGHT_REVIEW_MODEL"], effort=g["KNIGHT_REVIEW_EFFORT"],
                   builtins=builtins, bash=bash, deny=tuple(g["KNIGHT_REVIEW_DENY"]),
                   takes_dirs=True, persist_session=True)


def _liveness() -> Profile:
    # "Can Moses answer right now?" (agent/moses-liveness, every 30 minutes). Asked with the model and
    # effort his real chat turns use, read from the same variables conversation.py reads, without
    # importing the chat code. Plain text, because the probe reads the word it asked for.
    return Profile("moses-liveness", model=os.environ.get("MOSES_CHAT_MODEL", "claude-opus-5"),
                   effort=os.environ.get("MOSES_CHAT_EFFORT", "low"), output="text")


def _explorer() -> Profile:
    # The browser tool names live in Viatica (src/lib/e2e/explorer.ts), next to the checks that use
    # them. This is the CEILING they must fit inside: a browser tool on one of the explorer's own
    # servers, and never browser_run_code_unsafe, which runs code in the Playwright server process,
    # outside the browser and so outside the egress proxy.
    return Profile("zryachiy-explorer", model="claude-sonnet-5", effort="medium", mcp_config="",
                   approve_ceiling=r"mcp__[a-z][a-z0-9_-]*__browser_[a-z_]+",
                   never_approve=("browser_run_code_unsafe",),
                   setting_sources="project", output="stream-json")


# app -> (reason, builder). The reason is REQUIRED for any app that grants a tool, and checked at import.
APPS: dict[str, tuple[str, object]] = {
    "moses-chat": ("reads the corpus and live status through the Moses MCP server's read tools, "
                   "to answer in Slack", _chat),
    "moses-chat-directed": ("a person addressed him by name, so the acting tools a human may direct "
                            "are approved as well as the read tools", _chat_directed),
    "moses-chat-reading": ("the message carries a link, so WebFetch reads it; every acting tool is "
                           "withdrawn for that turn", _chat_reading),
    "moses-diagnosis": ("", _diagnosis),
    # The sixth launcher, found 2026-09-15: M20's inventory searched for `-p` and this used `--print`.
    "moses-liveness": ("", _liveness),
    "knight-builder": ("writes and tests code in his own clone, which has no remote; the Bash commands "
                       "are the short list in guards.env", _knight_builder),
    "zryachiy-reviewer": ("reads Knight's diff and runs read-only git commands; a reviewer that could "
                          "edit would be an author", _reviewer),
    "zryachiy-explorer": ("drives the sandbox's browsers through their own MCP servers, with no "
                          "built-in tool at all", _explorer),
}
GRANTS_NOTHING = {"moses-diagnosis", "moses-liveness"}


def check_reasons(apps: dict | None = None) -> None:
    for app, (reason, _builder) in (APPS if apps is None else apps).items():
        if app in GRANTS_NOTHING:
            continue
        if len((reason or "").strip()) < MIN_REASON:
            raise ProfileError(f"{app} grants tools, and its reason is {len((reason or '').strip())} "
                               f"characters; a grant must say why in at least {MIN_REASON}")


check_reasons()


@lru_cache(maxsize=None)
def profile(app: str) -> Profile:
    if app not in APPS:
        raise ProfileError(f"no profile named {app!r}. Known: {', '.join(APPS)}")
    p = APPS[app][1]()
    if p.grants() and app in GRANTS_NOTHING:
        raise ProfileError(f"{app} is declared to grant nothing, and its profile grants tools")
    return p


def profiles() -> dict[str, Profile]:
    return {app: profile(app) for app in APPS}


def build_argv(app: str, prompt: str, *, system_prompt: str | None = None, mcp_config: str | None = None,
               add_dirs: tuple[str, ...] | list[str] = (), approve: tuple[str, ...] | list[str] = (),
               deny: tuple[str, ...] | list[str] = (), model: str | None = None, effort: str | None = None,
               input_stream: bool = False, cli: str | None = None) -> list[str]:
    """The command line for one run of `app`. Refuses anything the profile does not allow."""
    p = profile(app)
    if system_prompt is not None and not p.takes_system_prompt:
        raise ProfileError(f"{app} does not take a system prompt")
    if p.mcp_config == "":
        if not mcp_config:
            raise ProfileError(f"{app} needs the caller's own MCP config")
        servers = mcp_config
    else:
        if mcp_config is not None:
            raise ProfileError(f"{app} reaches only its own MCP servers; a caller cannot add any")
        servers = p.mcp_config
    if add_dirs and not p.takes_dirs:
        raise ProfileError(f"{app} takes no extra directories")
    extra = []
    for a in approve:
        inside = bool(p.approve_ceiling) and re.fullmatch(p.approve_ceiling, a) is not None
        if not inside or any(a.endswith(f"__{n}") for n in p.never_approve):
            raise ProfileError(f"{app} cannot approve {a!r}: outside what its profile allows")
        extra.append(a)

    argv = [cli or CLI, "-p"] + (["--input-format", "stream-json"] if input_stream else [prompt])
    argv += ["--model", model or p.model, "--effort", effort or p.effort]
    if system_prompt is not None:
        argv += ["--system-prompt", system_prompt]
    argv += ["--setting-sources", p.setting_sources]
    argv += ["--strict-mcp-config", "--mcp-config", servers]
    approved = p.approved() + extra
    if approved:
        argv += ["--allowedTools", *approved]
    if p.deny or deny:
        argv += ["--disallowedTools", *p.deny, *deny]
    argv += ["--tools", ",".join(p.offered()), "--restricted"]
    if p.permission_mode:
        argv += ["--permission-mode", p.permission_mode]
    for d in add_dirs:
        argv += ["--add-dir", d]
    if p.exclude_dynamic_prompt:
        argv += ["--exclude-dynamic-system-prompt-sections"]
    if not p.persist_session:
        argv += ["--no-session-persistence"]
    argv += ["--output-format", p.output]
    if p.output == "stream-json":
        argv += ["--verbose"]
    return argv


# ── What counts as going around this module (claude_runner_test.py and tool-wall-probe use this) ──
# Starting the CLI in print mode at all is going around the runner, with or without tool flags. M20's
# own inventory missed exactly that on 2026-09-14: moses-liveness ran `"$CLI" --print ...` with NO
# flags, which is the most permissive launch there is (every built-in tool, Brad's settings and hooks),
# and a check that looked only for tool-grant flags could not see a launch that passes none.
PRINT_FLAGS = ("-p", "--print")
_BIN_TOKEN = re.compile(r"^(?:claude|[\w./~-]*/claude|\$\{?\w*CLI(?::-[^}]*)?\}?)$")


def launches_cli(path) -> bool:
    """Does this file start the Claude CLI in print mode itself? Comments and docstrings do not count."""
    import ast
    import io
    import tokenize
    from pathlib import Path
    p = Path(path)
    try:
        if p.stat().st_size > 2_000_000:
            return False
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if p.suffix == ".py" or (text.startswith("#!") and "python" in text.splitlines()[0]):
        strings, names = [], set()
        try:
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type == tokenize.STRING:
                    try:
                        v = ast.literal_eval(tok.string)
                    except (ValueError, SyntaxError):
                        continue
                    if isinstance(v, str):
                        strings.append(v)
                elif tok.type == tokenize.NAME:
                    names.add(tok.string)
        except (tokenize.TokenError, SyntaxError):
            return False
        prints = any(s in PRINT_FLAGS for s in strings)
        binary = any(s == "claude" or s.endswith("/claude") for s in strings) or any(n.endswith("CLI") for n in names)
        return prints and binary
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("//"):
            continue
        s = s.split(" #")[0]
        # Split on the shell's own separators too, not only spaces: `out=$(claude --print "hi")` is one
        # space-separated word, `out=$(claude`, and the first version of this read straight past it.
        toks = [t.strip("\"'") for t in re.split(r"[\s;|&`()=]+", s) if t]
        for i, t in enumerate(toks):
            if _BIN_TOKEN.match(t) and any(x in PRINT_FLAGS for x in toks[i + 1:]):
                return True
        if re.search(r"spawn\(\s*[\"']claude[\"']", s) and re.search(r"[\"'](?:-p|--print)[\"']", text):
            return True
    return False


def denials(result) -> list[str]:
    """The names of the tools a finished run was refused."""
    names = []
    for d in (result or {}).get("permission_denials") or [] if isinstance(result, dict) else []:
        names.append(str(d.get("tool_name") if isinstance(d, dict) else d))
    return names


def classify(result) -> str:
    """ok: exit 0 and nothing refused. degraded: finished, but a tool was refused. failed: no result."""
    if not isinstance(result, dict) or result.get("is_error"):
        return "failed"
    return "degraded" if denials(result) else "ok"


def describe(app: str) -> dict:
    p = profile(app)
    return {"app": app, "reason": APPS[app][0], "model": p.model, "effort": p.effort,
            "offered": p.offered(), "approved": p.approved(), "deny": list(p.deny),
            "approve_ceiling": p.approve_ceiling, "never_approve": list(p.never_approve),
            "takes_dirs": p.takes_dirs, "caller_mcp": p.mcp_config == ""}
