#!/usr/bin/env python3
"""moses-seal against a scratch config folder, with the real systemd-creds and this machine's TPM.

    python3 agent/seal_test.py

What must hold: a sealed token's old path is a link to a mode-400 copy in memory with the same bytes; the
disk keeps no plaintext; a "reboot" (the memory copy gone) is undone by unseal, including inside a real
systemd unit; a program writing the token in place fails; one replacing it is reported and never
overwritten; restore gives the plain file back; a damaged sealed copy is said; no value reaches the status
file; the drift check reads all of that. Then each property is broken on purpose and must go red.
Skips (exit 0, saying so) on a machine where systemd-creds --user cannot seal.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEAL = HERE / "moses-seal"
VALUE = "SEALTESTvalue0000000000000000001"
OTHER = "SEALTESTother0000000000000000002"
LEAK = "SEALTESTpemline00000000000000003"
HOOK = "SEALTESThook0000000000000000004"
FAILS: list[str] = []


def check(label: str, cond: bool, quiet: bool = False) -> None:
    if not quiet:
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        FAILS.append(label)


def fresh() -> dict:
    root = Path(tempfile.mkdtemp(prefix="moses-seal-"))
    cfg, run, state = root / "config", root / "run", root / "state"
    cfg.mkdir()
    (cfg / "moses.env").write_text("MOSES_STATE_NOTE=x\n")
    (cfg / "svc.env").write_text(f"SVC_TOKEN={VALUE}\nSVC_CHANNEL=C1\n")
    (cfg / "plain.env").write_text("SOME_URL=https://x\n")
    (cfg / "other.env").write_text(f"OTHER_API_KEY={OTHER}\n")
    # A multi-line value: its second line must never be taken for a key name (review 2026-11-06).
    (cfg / "multi.env").write_text(f'GOOGLE_CREDENTIALS={{\n  "private_key": "{LEAK}==",\n}}\n')
    (cfg / "hook.env").write_text(f"SLACK_WEBHOOK_URL=https://hooks.example/{HOOK}\n")
    for p in cfg.iterdir():
        os.chmod(p, 0o600)
    env = dict(os.environ, MOSES_CONFIG_DIR=str(cfg), MOSES_SETTINGS=str(cfg / "moses.env"),
               MOSES_STATE=str(state), MOSES_SEAL_RUNTIME=str(run), PYTHONPATH=str(HERE / "lib"))
    return {"root": root, "cfg": cfg, "run": run, "state": state, "env": env}


def run(t: dict, *args: str, script: Path = SEAL) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *args], env=t["env"], capture_output=True, text=True,
                          timeout=120)


def status(t: dict) -> dict:
    return json.loads((t["state"] / "sealed.json").read_text())


def all_text(t: dict) -> str:
    """Every byte on 'disk' (config + state), the in-memory folder excluded."""
    out = []
    for base in (t["cfg"], t["state"]):
        for p in base.rglob("*"):
            if p.is_file() and not p.is_symlink():
                out.append(p.read_bytes().decode(errors="replace"))
    return "\n".join(out)


probe = subprocess.run(["systemd-creds", "--user", "encrypt", "--name=p", "-", "-"], input=b"x",
                       capture_output=True, timeout=60)
if probe.returncode != 0:
    print(f"  SKIP  systemd-creds --user cannot seal here ({probe.stderr.decode().strip()[:120]})")
    sys.exit(0)


def suite(script: Path = SEAL, quiet: bool = False) -> int:
    before = len(FAILS)
    c = lambda label, cond: check(label, cond, quiet)  # noqa: E731
    t = fresh()
    cfg, rt = t["cfg"], t["run"]
    plan = run(t, "plan", script=script).stdout
    c("plan names the token files, and not moses.env or a file with no token",
      "would seal svc.env" in plan and "would seal other.env" in plan
      and "moses.env" not in plan and "plain.env" not in plan)
    c("a webhook URL and a multi-line credential are token files too",
      "would seal hook.env" in plan and "would seal multi.env" in plan)
    c("no line of a multi-line value is reported as a name", LEAK not in plan and "private_key" not in plan)
    r = run(t, "seal", "svc.env", script=script)
    c("seal succeeds", r.returncode == 0)
    p = cfg / "svc.env"
    c("the old path is a link into the in-memory folder", p.is_symlink() and Path(os.readlink(p)) == rt / "svc.env")
    c("the in-memory copy is mode 400 with the same bytes",
      (rt / "svc.env").is_file() and stat.S_IMODE(os.lstat(rt / "svc.env").st_mode) == 0o400
      and (rt / "svc.env").read_text() == f"SVC_TOKEN={VALUE}\nSVC_CHANNEL=C1\n")
    cred = cfg / "creds" / "svc.env.cred"
    c("the sealed copy is on disk, mode 600", cred.is_file() and stat.S_IMODE(cred.stat().st_mode) == 0o600)
    c("NO plaintext of the sealed token is left on disk, the status file included", VALUE not in all_text(t))
    s = status(t)
    c("status: svc.env sealed ok, other.env still plaintext (by name)",
      s["sealed"] == {"svc.env": "ok"} and s["plaintext"].get("other.env") == ["OTHER_API_KEY"])
    c("no value of any token in the status file",
      not any(v in json.dumps(s) for v in (VALUE, OTHER, LEAK, HOOK)))
    c("a reader that sources the old path gets the token",
      subprocess.run(["bash", "-c", f'set -a; . "{p}"; [ "$SVC_TOKEN" = "{VALUE}" ]'], timeout=10).returncode == 0)

    # A reboot: the memory copy is gone. unseal puts it back.
    shutil.rmtree(rt)
    r = run(t, "unseal", script=script)
    c("after a 'reboot', unseal brings the token back", r.returncode == 0 and (rt / "svc.env").is_file()
      and status(t)["sealed"] == {"svc.env": "ok"})

    try:
        with open(p, "a") as fh:
            fh.write("X=1\n")
        wrote = True
    except PermissionError:
        wrote = False
    c("a program rewriting the token in place fails loudly (it would vanish at reboot)", not wrote)

    # A program that replaces the file: plaintext is back. It is reported, and unseal never overwrites it.
    p.unlink()
    p.write_text(f"SVC_TOKEN={VALUE}-newer\n")
    r = run(t, "unseal", script=script)
    c("a plain file back at a sealed path is reported (unseal exits 1)",
      r.returncode == 1 and status(t)["sealed"]["svc.env"].startswith("PLAINTEXT"))
    c("and unseal did not overwrite it (it may be someone's newer token)",
      not p.is_symlink() and p.read_text() == f"SVC_TOKEN={VALUE}-newer\n")
    p.unlink()
    r = run(t, "unseal", script=script)
    c("a missing link is put back by unseal", r.returncode == 0 and p.is_symlink())

    r = run(t, "restore", "svc.env", script=script)
    c("restore: a plain mode-600 file with the same bytes, and the sealed copy gone",
      r.returncode == 0 and not p.is_symlink() and p.read_text() == f"SVC_TOKEN={VALUE}\nSVC_CHANNEL=C1\n"
      and stat.S_IMODE(p.stat().st_mode) == 0o600 and not cred.exists())

    # A damaged sealed copy is said out loud.
    run(t, "seal", "other.env", script=script)
    oc = cfg / "creds" / "other.env.cred"
    data = bytearray(oc.read_bytes())
    data[len(data) // 2] ^= 0xFF
    oc.write_bytes(bytes(data))
    shutil.rmtree(rt)
    r = run(t, "unseal", script=script)
    c("a damaged sealed copy fails unseal, and says which", r.returncode == 1 and "other.env" in r.stderr)
    run(t, "seal", "hook.env", script=script)
    shutil.rmtree(rt)
    r = run(t, "unseal", script=script)
    c("and the other sealed files are still unsealed, and reported", (rt / "hook.env").is_file()
      and status(t)["sealed"].get("hook.env") == "ok" and status(t)["sealed"].get("other.env") != "ok")

    c("a name outside ~/.config/moses is refused", run(t, "seal", "../x.env", script=script).returncode == 1)
    c("moses.env is never sealed", run(t, "seal", "moses.env", script=script).returncode == 1)
    c("a name with a trailing newline is refused", run(t, "seal", "multi.env\n", script=script).returncode == 1)
    shutil.rmtree(cfg / "creds")
    os.symlink(t["root"], cfg / "creds")
    c("a creds folder that is a link is refused", run(t, "seal", "multi.env", script=script).returncode == 1
      and not (cfg / "multi.env").is_symlink())
    shutil.rmtree(t["root"], ignore_errors=True)
    return len(FAILS) - before


print("── moses-seal ──")
suite()

print("── the boot path: unseal inside a real systemd unit ──")
t = fresh()
run(t, "seal", "svc.env")
shutil.rmtree(t["run"])
props = [f"--setenv={k}={t['env'][k]}" for k in ("MOSES_CONFIG_DIR", "MOSES_SETTINGS", "MOSES_STATE",
                                                    "MOSES_SEAL_RUNTIME", "PYTHONPATH")]
r = subprocess.run(["systemd-run", "--user", "--wait", "--quiet", "--collect", *props,
                    "/usr/bin/python3", str(SEAL), "unseal"], capture_output=True, text=True, timeout=120)
check("a systemd user unit (no terminal) unseals the token", r.returncode == 0 and (t["run"] / "svc.env").is_file())
shutil.rmtree(t["root"], ignore_errors=True)

print("── the root script's probe: encrypt and decrypt both through stdin ──")
enc = subprocess.run(["systemd-creds", "--user", "encrypt", "--with-key=host+tpm2", "--name=probe", "-", "-"],
                     input=b"PROBE_TOKEN=not-a-secret\n", capture_output=True, timeout=60)
dec = subprocess.run(["systemd-creds", "--user", "decrypt", "--name=probe", "-", "-"], input=enc.stdout,
                     capture_output=True, timeout=60)
check("decrypt reads a sealed blob from stdin (mf8-seal.sh's probe line)", dec.stdout == b"PROBE_TOKEN=not-a-secret\n")

print("── the drift check ──")
loader = importlib.machinery.SourceFileLoader("moses_drift", str(HERE / "moses-drift"))
spec = importlib.util.spec_from_loader("moses_drift", loader)
D = importlib.util.module_from_spec(spec)
loader.exec_module(D)
NOW = 1_800_000_000


def judge(doc) -> str:
    D.DRIFT.clear(); D.BLIND.clear(); D.AGREED.clear()
    real = (D._env.read_state, D._env.read_peer)
    fake = lambda name: doc if doc is None or isinstance(doc, str) else json.dumps(doc)  # noqa: E731
    D._env.read_state = D._env.read_peer = fake
    try:
        D.check_sealed_tokens(now=NOW)
    finally:
        D._env.read_state, D._env.read_peer = real
    return "red" if D.DRIFT else "blind" if D.BLIND else "green" if D.AGREED else "?"


def drift_suite(quiet: bool = False) -> int:
    before = len(FAILS)
    c = lambda label, cond: check(label, cond, quiet)  # noqa: E731
    base = {"written": NOW - 60, "account": "moses"}
    c("all sealed, none plaintext: green", judge({**base, "sealed": {"a.env": "ok"}, "plaintext": {}}) == "green")
    c("a sealed path that is plaintext again: red",
      judge({**base, "sealed": {"a.env": "PLAINTEXT: …"}, "plaintext": {}}) == "red")
    c("plaintext beside sealed: red",
      judge({**base, "sealed": {"a.env": "ok"}, "plaintext": {"b.env": ["B_TOKEN"]}}) == "red")
    c("nothing sealed yet: blind, not green", judge({**base, "sealed": {}, "plaintext": {"b.env": ["B_TOKEN"]}}) == "blind")
    c("no report: blind", judge(None) == "blind")
    c("a report of no token files at all: blind, not green", judge({**base, "sealed": {}, "plaintext": {}}) == "blind")
    c("a report 3h old: blind", judge({**base, "written": NOW - 3 * 3600, "sealed": {"a.env": "ok"},
                                       "plaintext": {}}) == "blind")
    c("a report it cannot parse: blind", judge("not json") == "blind")
    return len(FAILS) - before


drift_suite()
check("the check is registered in moses-drift", D.CHECKS.get("sealed") is D.check_sealed_tokens)

print("── watch it fail ──")
saved = len(FAILS)
MUT = Path(tempfile.mkdtemp(prefix="moses-seal-mut-"))


def mutant(label: str, old: str, new: str) -> None:
    src = SEAL.read_text()
    assert old in src, f"mutant anchor missing: {old!r}"
    m = MUT / "moses-seal"
    m.write_text(src.replace(old, new, 1))
    reds = suite(script=m, quiet=True)
    del FAILS[saved:]
    check(f"MUTANT {label} is caught ({reds} red)", reds > 0)


mutant("the memory copy is writable (600)", "_write_new(run / name, data, 0o400)\n    now",
       "_write_new(run / name, data, 0o600)\n    now")
mutant("unseal overwrites a plain file at the old path", "if not os.path.lexists(p):\n                    _link_over",
       "if True:\n                    _link_over")
mutant("the plain file is left on disk beside the seal", "    _link_over(p, run / name)\n    print(f\"sealed",
       "    print(f\"sealed")
mutant("a multi-line value's line taken for a name",
       'return [k for k in env_keys(data.decode(errors="replace")) if is_secret_name(k)]',
       'return [l.split("=", 1)[0].strip() for l in data.decode().splitlines() if "=" in l]')
mutant("values leak into the status file", '"plaintext": plaintext_token_files(),',
       '"plaintext": plaintext_token_files(), "leak": [(CONFIG / n).read_text() for n in names if (CONFIG / n).exists()],')

real_check = D.check_sealed_tokens
src = (HERE / "moses-drift").read_text()
D.check_sealed_tokens = lambda now=None: D.agreed("everything is fine")
reds = drift_suite(quiet=True)
del FAILS[saved:]
D.check_sealed_tokens = real_check
check(f"MUTANT a drift check that always agrees is caught ({reds} red)", reds > 0)
shutil.rmtree(MUT, ignore_errors=True)

print(f"\n  {'all good' if not FAILS else str(len(FAILS)) + ' FAILED'}")
sys.exit(1 if FAILS else 0)
