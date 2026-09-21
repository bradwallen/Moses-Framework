"""runner — starts an agent session with every safety layer wired in.

This is the only place an agent is allowed to come into existence. Everything Brad ruled on is
enforced here, in one readable block, so there is one file to audit rather than a policy scattered
across call sites:

  * the guard decides every tool call            (guard.check → can_use_tool)
  * api.anthropic.com is refused at the network  (sandbox.network.deniedDomains)
  * the agent gets its own credential, never Viatica's   (a rebuilt env, not os.environ)
  * spend is capped before and during the session (budget.may_start + max_budget_usd)
  * agents cannot spawn agents                    (Task/Agent tools disallowed)

WHY THE ENV IS REBUILT RATHER THAN FILTERED
`os.environ` on Reserve carries whatever the parent process had. Inheriting it and deleting the keys
we know about is a blocklist, and a blocklist fails silently the day a new secret is added. The
agent env is constructed from nothing instead — only the variables listed below exist inside it. A
credential that is never added cannot leak, including one nobody has thought of yet.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import budget
import guard

# Refused at the network layer, so the Claude-API rule does not depend on catching every possible
# spelling of a shell command. guard.py still denies the obvious ones — that layer explains itself,
# this one is the actual wall.
DENIED_DOMAINS = [
    "api.anthropic.com",
    "console.anthropic.com",
    "statsig.anthropic.com",
]

# Tools no agent gets, whatever its roster entry says. Task/Agent is the important one: an agent
# that can spawn agents is how a $5 cap becomes $50, and one level of delegation (Moses → worker)
# is all the design calls for.
GLOBALLY_DISALLOWED = ["Task", "Agent", "KillShell", "BashOutput"]


@dataclass
class AgentRun:
    """What happened, for the caller to report and the ledger to record."""
    text: str = ""
    cost_usd: float = 0.0
    turns: int = 0
    denials: list[str] = field(default_factory=list)
    stopped_reason: str | None = None
    error: str | None = None


def _agent_env(credential_file: str = "/etc/moses/agents.env") -> dict[str, str]:
    """Build the agent's environment from nothing.

    The credential comes from the agents-only file. Viatica's production key is never a candidate —
    it is not in this dict and cannot be, because nothing copies os.environ into it.
    """
    env: dict[str, str] = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": os.environ.get("MOSES_AGENT_HOME", "/var/lib/moses/home"),
        "LANG": "C.UTF-8",
        "TERM": "dumb",
    }
    p = Path(credential_file)
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            # Only the agent credential crosses over. Slack tokens and the cron secret stay out —
            # an agent has no reason to post as Birdeye or call a cron endpoint directly.
            if key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
                env[key] = value.strip().strip('"').strip("'")
    return env


def build_options(
    *,
    agent_id: str,
    system_prompt: str,
    allowed_tools: list[str],
    cap_usd: float,
    cwd: str = "/var/lib/moses/work",
    model: str = "claude-opus-5",
    max_turns: int = 24,
) -> Any:
    """Construct ClaudeAgentOptions with every layer attached.

    Imported lazily so guard/budget stay unit-testable on a box where the SDK isn't installed yet.
    """
    from claude_agent_sdk import ClaudeAgentOptions, PermissionResultAllow, PermissionResultDeny

    denials: list[str] = []

    async def can_use_tool(tool_name: str, tool_input: dict, context: Any):
        v = guard.check(tool_name, tool_input)
        if v.decision == "allow":
            return PermissionResultAllow()
        detail = tool_input.get("command") or tool_input.get("url") or tool_input.get("file_path") or ""
        denials.append(f"{v.rule}: {str(detail)[:80]}")
        if v.decision == "deny":
            return PermissionResultDeny(message=f"Blocked by {v.rule} — {v.reason}")
        # "ask" with nobody to ask: Slack is not an interactive prompt, so an unrecognized action
        # stops and is reported rather than being assumed safe. Confirmations arrive in phase 2 via
        # a Slack round-trip; until then, unknown means no.
        return PermissionResultDeny(
            message=f"Needs Brad's confirmation ({v.rule} — {v.reason}). Not doing it unasked."
        )

    _, st = budget.may_start(agent_id, cap_usd)

    opts = ClaudeAgentOptions(
        model=model,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,
        disallowed_tools=GLOBALLY_DISALLOWED,
        can_use_tool=can_use_tool,
        max_turns=max_turns,
        max_budget_usd=budget.session_ceiling(st),
        cwd=cwd,
        env=_agent_env(),
        # Do NOT inherit Brad's personal Claude Code configuration. His user settings carry a
        # permissive terminal autoApprove and his own CLAUDE.md; an agent picking those up would
        # quietly widen its own permissions from a file nobody was thinking about.
        setting_sources=[],
        permission_mode="default",
        sandbox={
            "enabled": True,
            "autoAllowBashIfSandboxed": False,   # the guard still decides; the sandbox is a floor
            "network": {"deniedDomains": DENIED_DOMAINS},
        },
    )
    return opts, denials


async def run(
    *,
    agent_id: str,
    prompt: str,
    system_prompt: str,
    allowed_tools: list[str],
    cap_usd: float = budget.DEFAULT_DAILY_USD,
    **kw: Any,
) -> AgentRun:
    """Run one agent session end to end, recording spend whatever the outcome."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, query

    # No agent credential ⇒ refuse, loudly. Without this the SDK spawns the `claude` CLI, which
    # falls back to whatever login it already has — Brad's own. That would be spend he never
    # authorized, arriving from a process he wasn't watching, which is the exact thing he ruled out.
    # Failing with a sentence he can act on beats succeeding on the wrong account.
    env = _agent_env()
    if not (env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN")):
        return AgentRun(
            error=("No agent credential in /etc/moses/agents.env, so I won't run this — falling back "
                   "to your personal CLI login would be spending on your account without asking. "
                   "Add ANTHROPIC_API_KEY there (a key of its own, not Viatica's) to enable research."),
            stopped_reason="no_credential",
        )

    ok, st = budget.may_start(agent_id, cap_usd)
    if not ok:
        return AgentRun(
            error=(f"Daily budget spent: ${st.spent_usd:.2f} of ${st.cap_usd:.2f} across "
                   f"{st.runs} run(s). Not starting. Raise the cap in the roster to continue."),
            stopped_reason="budget_exhausted",
        )

    opts, denials = build_options(
        agent_id=agent_id, system_prompt=system_prompt,
        allowed_tools=allowed_tools, cap_usd=cap_usd, **kw,
    )

    out = AgentRun()
    try:
        async for msg in query(prompt=prompt, options=opts):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        out.text += block.text
            elif isinstance(msg, ResultMessage):
                out.cost_usd = msg.total_cost_usd or 0.0
                out.turns = msg.num_turns
                out.stopped_reason = msg.stop_reason
                if msg.is_error:
                    out.error = "; ".join(msg.errors or ["session ended in error"])
    except Exception as e:                       # never let one bad run kill the listener
        out.error = f"{type(e).__name__}: {e}"
    finally:
        out.denials = denials
        # Recorded even on failure: a session that errored still spent tokens, and a ledger that
        # only counts successes is how the cap gets quietly exceeded.
        budget.record(agent_id, out.cost_usd, turns=out.turns, denials=len(denials))

    return out
