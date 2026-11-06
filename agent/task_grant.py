"""Task grants over Slack (#17): Moses asks for a task from chat, and says where a code counts.

Root does everything that matters, with its own Slack app (the Moses Gate, token at
/etc/moses/mfa/slack-gate.env, readable by root alone): it checks the task's shape, posts the task Brad
reads, reads his reply from THAT thread, checks the code, and posts the result there. This module never
sees, carries or passes the code. A review on 2026-11-04 refused the earlier design, in which the
operator's own process carried the prompt to Slack: anything that carries the prompt can swap it.

  chat()      Brad asks in plain words ("restart the backup timer"); Moses declares
              `DO: task <title> | <action>; <action>`. Code asks root for the task, waits through root
              for Brad's code (`verify-slack --wait`), runs each action, REVOKES the grant, and reports.
              A chat task leaves nothing open behind it.
  gate_open() Is a request open that is answered in the gate's thread? The listener then tells Brad a
              code typed anywhere else did not count, instead of handing it to anything.

From chat, only unit and apt actions: install-file needs a file staged from a session (`moses-task`).
"""
import json
import os
import random
import shlex
import subprocess

NAME = "task"
MFA_CMD = shlex.split(os.environ.get("MOSES_MFA_CMD", "sudo -n /usr/local/bin/moses-mfa"))
UNIT_VERBS = ("start", "stop", "restart", "reload", "enable", "disable")
CHAT_HOURS = 1                     # revoked as soon as its actions ran; this only bounds a crash
WAIT_TIMEOUT = 900                 # above root's own wait (6 min) plus its Slack calls: root ends first
DO_TIMEOUT = 3600                  # an apt upgrade can take a while; killing sudo would not stop root
# Brad, 2026-11-05: "WAY too robotic. Moses has a voice, let him use it." The gate post lights up as
# unread, so the ask need not say where to answer, and a clean revoke is not news.
ASKS = ("🔐 Need your code for that one.", "🔐 Happy to, just need your code.",
        "🔐 Code me and I'm on it.", "🔐 Sure thing. Your code, when you get a sec?")


def actions() -> list[tuple[str, str]]:
    return [(NAME,
             "do root work on Reserve that needs Brad's one-time code: restart, start, stop, reload, enable "
             "or disable a system unit, reload systemd, refresh apt, or install or upgrade packages. Use it "
             "whenever Brad asks for one of these, however he puts it (\"restart the backup timer\", "
             "\"install jq\", \"bounce tailscale\"). The argument is a short title, `|`, then the actions "
             "separated by `;`, each one of: `<verb> <unit>` (verb is start, stop, restart, reload, enable "
             "or disable; the unit's full name, like reserve-backup.timer), `daemon-reload`, `apt-update`, "
             "`apt-install <pkg> <pkg>…`, `apt-upgrade <pkg> <pkg>…`. Example: "
             "`DO: task Restart the backup timer | restart reserve-backup.timer`. The title is plain ASCII "
             "(no backticks, no dashes other than -), under 120 characters. Code asks Brad for his "
             "code in the Moses Gate channel and runs only what he approves; NEVER ask him for a code "
             "yourself, never repeat one, and never say it was done — code says what happened. Anything "
             "else that needs root (editing a file as root, deleting under /mnt) is not this action: say "
             "it needs Brad at a terminal.")]


def _mfa(*args: str, stdin: str = "", timeout: int = 60) -> tuple[int, str, str]:
    try:
        r = subprocess.run([*MFA_CMD, *args], input=stdin, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 2, "", f"could not run the code check ({type(e).__name__})"
    err = r.stderr.strip()
    if r.returncode != 0 and (err.startswith("sudo:") or "a password is required" in err):
        return 2, "", f"the code check is not installed for this account ({err.splitlines()[0][:120]})"
    return r.returncode, r.stdout.strip(), err.replace("moses-mfa: ", "")


def gate_open() -> bool:
    """Is a code request open that is answered in the Moses Gate's thread? Asked of root."""
    rc, out, _ = _mfa("status")
    if rc != 0:
        return False
    try:
        c = json.loads(out).get("open_challenge") or {}
    except ValueError:
        return False
    return bool(c.get("in_gate_thread"))


def parse(arg: str) -> tuple[str, list[dict]]:
    """`<title> | <action>; <action>` → (title, typed actions). Raises ValueError with what was wrong.
    Only the shape is read here; root checks every action again, and is the one that decides."""
    title, sep, rest = (arg or "").partition("|")
    title = title.strip()
    if not sep or not title or not rest.strip():
        raise ValueError("a task is `<title> | <action>; <action>`")
    acts = []
    for part in rest.split(";"):
        w = part.split()
        if not w:
            continue
        verb = w[0].lower()
        if verb in UNIT_VERBS and len(w) == 2:
            acts.append({"type": "unit", "verb": verb, "unit": w[1]})
        elif verb == "daemon-reload" and len(w) == 1:
            acts.append({"type": "unit", "verb": "daemon-reload"})
        elif verb == "apt-update" and len(w) == 1:
            acts.append({"type": "apt-update"})
        elif verb in ("apt-install", "apt-upgrade") and len(w) >= 2:
            acts.append({"type": verb, "packages": w[1:]})
        else:
            raise ValueError(f"`{part.strip()}` is not an action I can ask for from chat")
    if not acts:
        raise ValueError("the task names no actions")
    return title, acts


def chat(arg: str):
    """Ask, wait through root, run, revoke, report. A generator: the listener says each line as it comes."""
    try:
        title, acts = parse(arg)
    except ValueError as e:
        yield f"⚠️ Hm, I couldn't turn that into something I can ask root for ({e}), so I didn't."
        return
    spec = {"title": title, "actions": acts, "hours": CHAT_HOURS}
    rc, out, err = _mfa("challenge", "task", stdin=json.dumps(spec))
    if rc != 0:
        lead = "⚠️ Couldn't reach the code check" if rc == 2 else "⚠️ Root won't take that one"
        yield f"{lead}: {err or 'no reason given'}."
        return
    try:
        c = json.loads(out)
    except ValueError:
        yield "⚠️ The code check said something I can't make sense of, so I left it alone."
        return
    yield random.choice(ASKS)
    rc, out, err = _mfa("verify-slack", "--wait", timeout=WAIT_TIMEOUT)
    if rc != 0:
        if "TimeoutExpired" in err:
            # sudo cannot pass our SIGKILL to root, so root may still grant after we gave up: revoke any
            # grant of this request so nothing stays open behind a "nothing ran" (review, 2026-11-04).
            _revoke_purpose(c.get("purpose", ""))
        yield (f"⚠️ Couldn't check the code ({err}), so I didn't touch anything." if rc == 2
               else f"❌ No go: {err or 'no reason given'}. Didn't touch anything.")
        return
    try:
        got = json.loads(out.splitlines()[-1])
        grant = got["authorization"]
    except (ValueError, KeyError, IndexError):
        yield "⚠️ The code check said something I can't make sense of, so I didn't touch anything."
        return
    if got.get("authorized") != c.get("purpose"):
        # The wait reads whichever request is open when it starts. If ours closed first and another
        # opened, this grant is someone else's: never run or revoke it as ours (review, 2026-11-04).
        yield (f"⚠️ That code went to a different request (`{got.get('authorized')}`), not this one, "
               f"so I didn't touch anything.")
        return
    done, failed = [], ""
    for n in range(1, len(acts) + 1):
        rc, o, e = _mfa("do", grant, str(n), timeout=DO_TIMEOUT)
        if rc != 0:
            last = ((o + "\n" + e).strip().splitlines() or [""])[-1]
            failed = f"{_describe(acts[n - 1], past=False)} failed (exit {rc}: {last[:200]})"
            break                                  # the rest may depend on this one: stop, and say so
        done.append(_describe(acts[n - 1]))
    left = len(acts) - len(done) - (1 if failed else 0)
    if not failed:
        msg = f"✅ Done. {_sentence(done)}."
    elif done:
        msg = f"❌ {_sentence(done)}, but {failed}."
    else:
        msg = f"❌ {failed[0].upper()}{failed[1:]}."
    if left:
        msg += f" I stopped there, so the other {left} didn't run." if left > 1 else " I stopped there, so the last one didn't run."
    rrc, _, rerr = _mfa("revoke", grant)
    if rrc != 0:                                   # a clean revoke is not news; a failed one is
        msg += f"\n⚠️ Heads up: I couldn't revoke the grant ({rerr}). It expires on its own within {CHAT_HOURS}h."
    yield msg


def _revoke_purpose(purpose: str) -> None:
    """Revoke any live grant for this request, by the purpose root gave it."""
    rc, out, _ = _mfa("grants")
    try:
        live = json.loads(out) if rc == 0 else []
    except ValueError:
        return
    for g in live:
        if g.get("purpose") == purpose:
            _mfa("revoke", g["grant"])


_PAST = {"start": "started", "stop": "stopped", "restart": "restarted", "reload": "reloaded",
         "enable": "enabled", "disable": "disabled"}
_ING = {"start": "starting", "stop": "stopping", "restart": "restarting", "reload": "reloading",
        "enable": "enabling", "disable": "disabling"}


def _describe(a: dict, past: bool = True) -> str:
    """One action in plain words: "restarted reserve-backup.timer", or "restarting …" for a failure."""
    if a["type"] == "unit" and a["verb"] == "daemon-reload":
        return "reloaded systemd" if past else "reloading systemd"
    if a["type"] == "unit":
        verb = _PAST[a["verb"]] if past else _ING[a["verb"]]
        return f"{verb} {a['unit']}"
    if a["type"] == "apt-update":
        return "refreshed apt" if past else "refreshing apt"
    pkgs = " ".join(a["packages"])
    if a["type"] == "apt-install":
        return f"installed {pkgs}" if past else f"installing {pkgs}"
    return f"upgraded {pkgs}" if past else f"upgrading {pkgs}"


def _sentence(parts: list[str]) -> str:
    """["restarted x", "installed jq"] → "Restarted x, then installed jq"."""
    s = ", then ".join(parts)
    return s[:1].upper() + s[1:]
