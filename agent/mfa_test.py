#!/usr/bin/env python3
"""The new-build code gate refuses everything it is not sure about — and each refusal is watched failing.

    python3 agent/mfa_test.py

Runs the REAL agent/moses-mfa as the operator, against a scratch seed and state (the MOSES_MFA_*
overrides, which the helper honors only when it is not root). Then it breaks the helper on purpose,
one guard at a time, and requires the check for that guard to go red. A suite that stays green
against a helper with no replay guard is not testing the replay guard. (Commandment 8.)
"""
import base64
import hashlib
import hmac
import importlib.machinery
import importlib.util
import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HELPER = HERE / "moses-mfa"
T0 = 1_800_000_000.0                    # a fixed clock; every test sets MOSES_MFA_NOW
SECRET = base64.b32encode(b"moses-mfa-test-seed!").decode().rstrip("=")


def hotp(secret: str, counter: int) -> str:
    """Written here, not imported, so the suite does not grade the helper's arithmetic with itself."""
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    return str((struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000).zfill(6)


def code_at(t: float) -> str:
    return hotp(SECRET, int(t // 30))


def suite(helper: Path) -> tuple[list[str], list[str]]:
    """Every check against one copy of the helper. Returns (passed, failed) check names."""
    passed: list[str] = []
    failed: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        (passed if cond else failed).append(name)
        if helper == HELPER:
            print(f"  {'ok  ' if cond else 'FAIL'} {name}{('' if cond else ' — ' + str(detail)[:200])}")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        seed = tmp / "etc" / "totp.seed"
        state = tmp / "state"
        ledger = state / "audit.jsonl"
        clean = {k: v for k, v in os.environ.items() if not k.startswith("MOSES_MFA_")}

        sent: list[str] = []

        def run(*args, t=T0, stdin="", cwd=None, seed_path=seed):
            if stdin.strip().isdigit():
                sent.append(stdin.strip())
            env = {**clean, "MOSES_MFA_SEED": str(seed_path), "MOSES_MFA_STATE": str(state),
                   "MOSES_MFA_NOW": str(t)}
            return subprocess.run([str(helper), *args], input=stdin, capture_output=True, text=True,
                                  env=env, cwd=cwd or td, timeout=30)

        def challenge(t=T0, project="example-app"):
            r = run("challenge", "new-build", project, t=t)
            try:
                return json.loads(r.stdout)["challenge"], r
            except (ValueError, KeyError):
                return "", r

        def ledger_text() -> str:
            return ledger.read_text() if ledger.exists() else ""

        def reset():
            for p in (state / "state.json",):
                if p.exists():
                    p.unlink()

        # ── Not enrolled: COULD NOT CHECK, never "refused", never success ───────
        r = run("challenge", "new-build", "example-app")
        check("no seed: challenge says it could not check (exit 2)",
              r.returncode == 2 and "COULD NOT CHECK" in r.stderr, (r.returncode, r.stderr))

        seed.parent.mkdir(parents=True)
        seed.write_text(SECRET + "\n")
        seed.chmod(0o600)

        # ── What a code can authorize ─────────────────────────────────────────
        r = run("challenge", "edit-target", "viatica")
        check("only new-build can be authorized", r.returncode == 1 and "not something a code can authorize" in r.stderr, r.stderr)
        r = run("challenge", "new-build", "../../etc")
        check("a project id that is not a plain name is refused", r.returncode == 1 and "not a project id" in r.stderr, r.stderr)

        # ── The happy path ────────────────────────────────────────────────────
        cid, r = challenge()
        check("challenge opens and names the project", bool(cid) and '"new-build:example-app"' in r.stdout, r.stdout + r.stderr)
        good = code_at(T0)
        r = run("verify", cid, good)
        check("a code in ARGV is refused (sudo logs argv)", r.returncode == 1 and "usage" in r.stderr, r.stderr)
        r = run("verify", cid, "--who", "U-test", stdin=good)
        check("the right code on stdin authorizes", r.returncode == 0 and '"authorized": "new-build:example-app"' in r.stdout,
              (r.returncode, r.stdout, r.stderr))
        auth = json.loads(r.stdout)["authorization"] if r.returncode == 0 else ""
        r = run("authorized", "new-build", "example-app")
        check("the authorization is waiting for that project", r.returncode == 0 and r.stdout.strip() == auth, r.stdout + r.stderr)
        r = run("authorized", "new-build", "some-other-project")
        check("it authorizes that project and no other", r.returncode == 1, r.stdout)
        r = run("consume", auth)
        check("the build consumes it", r.returncode == 0 and "new-build:example-app" in r.stdout, r.stderr)
        r = run("consume", auth)
        check("a consumed authorization cannot be used twice", r.returncode == 1 and "already used" in r.stderr, r.stderr)
        r = run("authorized", "new-build", "example-app")
        check("nothing is waiting once it is consumed", r.returncode == 1, r.stdout)

        # ── The code and the seed never leave ────────────────────────────────
        check("the seed is never written to the ledger", SECRET not in ledger_text(), "found in ledger")
        check("the ledger is readable (0644), the state is not (0600)",
              ledger.exists() and stat.S_IMODE(ledger.stat().st_mode) == 0o644
              and stat.S_IMODE((state / "state.json").stat().st_mode) == 0o600,
              (oct(stat.S_IMODE(ledger.stat().st_mode)) if ledger.exists() else "no ledger"))

        # ── Replay: the same code, the same time step, a fresh challenge ─────
        cid, _ = challenge(t=T0 + 5)
        r = run("verify", cid, stdin=good, t=T0 + 5)
        check("a used code cannot authorize again (replay)", r.returncode == 1 and "already used" in r.stderr, (r.returncode, r.stderr))
        # The NEXT step's code is a different code and is fine.
        r = run("verify", cid, stdin=code_at(T0 + 30), t=T0 + 30)
        check("the next time step's code works", r.returncode == 0, r.stderr)

        # ── Three tries per challenge ─────────────────────────────────────────
        reset()
        t = T0 + 1000
        cid, _ = challenge(t=t)
        outs = [run("verify", cid, stdin="000000" if code_at(t) != "000000" else "111111", t=t) for _ in range(3)]
        r = run("verify", cid, stdin=code_at(t), t=t)
        check("three wrong codes close the challenge — the right one is then refused",
              all(o.returncode == 1 for o in outs) and r.returncode == 1 and "no open challenge" in r.stderr,
              [o.stderr for o in outs] + [r.stderr])

        # ── Expiry: the code is not even compared ─────────────────────────────
        reset()
        t = T0 + 2000
        cid, _ = challenge(t=t)
        r = run("verify", cid, stdin=code_at(t + 301), t=t + 301)
        check("an expired challenge refuses even the right code", r.returncode == 1 and "expired" in r.stderr, r.stderr)

        # ── One open challenge: a different project cannot replace it ────────
        reset()
        t = T0 + 3000
        first, _ = challenge(t=t, project="first-project")
        second, r = challenge(t=t + 1, project="second-project")
        check("while one request is open, a request for another project is refused",
              second == "" and r.returncode == 1 and "still open" in r.stderr, r.stdout + r.stderr)
        r = run("verify", first, stdin=code_at(t + 1), t=t + 1)
        check("and the open one still authorizes the project Brad read",
              r.returncode == 0 and '"new-build:first-project"' in r.stdout, r.stdout + r.stderr)
        again, _ = challenge(t=t + 40, project="third-project")
        same, _ = challenge(t=t + 41, project="third-project")
        r = run("verify", again, stdin=code_at(t + 41), t=t + 41)
        check("asking again for the SAME project replaces the old id", bool(same) and r.returncode == 1
              and "no open challenge" in r.stderr, r.stderr)
        later, _ = challenge(t=t + 40 + 301, project="fourth-project")
        check("once the open one expires, another project may ask", bool(later))

        # ── Only ASCII digits are a code ─────────────────────────────────────
        reset()
        t = T0 + 4000
        cid, _ = challenge(t=t)
        arabic = code_at(t).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
        r = run("verify", cid, stdin=arabic, t=t)
        check("non-ASCII digits are a wrong code, refused and counted — never a crash",
              r.returncode == 1 and "wrong code" in r.stderr and "Traceback" not in r.stderr, r.stderr)

        # ── Lockout: 5 wrong codes in a day lock verify, across challenges ────
        reset()
        t = T0 + 5000
        wrong = "000000" if code_at(t) != "000000" else "111111"
        for i in range(5):
            cid, _ = challenge(t=t + i)
            run("verify", cid, stdin=wrong, t=t + i)
        cid, r = challenge(t=t + 10)
        check("locked: no new challenge for a day", cid == "" and r.returncode == 1 and "locked" in r.stderr, r.stdout + r.stderr)
        r = run("status", t=t + 10)
        check("status says it is locked", '"locked_until": "' in r.stdout and '"locked_until": null' not in r.stdout, r.stdout)
        cid, r = challenge(t=t + 86410)
        r2 = run("verify", cid, stdin=code_at(t + 86410), t=t + 86410)
        check("the lock lifts after a day", bool(cid) and r2.returncode == 0, r.stderr + r2.stderr)

        # ── The authorization itself expires ──────────────────────────────────
        reset()
        t = T0 + 200000
        cid, _ = challenge(t=t)
        r = run("verify", cid, stdin=code_at(t), t=t)
        a = json.loads(r.stdout)["authorization"] if r.returncode == 0 else ""
        r = run("consume", a, t=t + 1801)
        check("an authorization unused for 30 minutes expires", bool(a) and r.returncode == 1 and "expired" in r.stderr, r.stderr)

        # ── Two verifies racing on one challenge: exactly one wins ────────────
        reset()
        t = T0 + 300000
        cid, _ = challenge(t=t)
        env = {**clean, "MOSES_MFA_SEED": str(seed), "MOSES_MFA_STATE": str(state), "MOSES_MFA_NOW": str(t), "MOSES_MFA_PAUSE": "0.4"}
        procs = [subprocess.Popen([str(helper), "verify", cid], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, env=env, cwd=td) for _ in range(4)]
        # Every one gets its code BEFORE any is waited on. communicate() one at a time would finish each
        # process before the next had read stdin, and the "race" would be four calls in a row.
        for p in procs:
            p.stdin.write(code_at(t))
            p.stdin.close()
        rcs = [p.wait(timeout=30) for p in procs]
        check("four racing verifies of one code: exactly one authorizes", rcs.count(0) == 1, rcs)

        # Every code this run submitted, right or wrong, accepted or refused.
        leaked = [c for c in sent if c in ledger_text()]
        check("the code is never written to the ledger", bool(sent) and not leaked, leaked)

        # ── A planted module beside the cwd is not imported ──────────────────
        plant = tmp / "plant"
        plant.mkdir()
        (plant / "json.py").write_text("print('PLANTED')\nraise SystemExit(0)\n")
        r = run("status", cwd=str(plant))
        check("a planted json.py in the cwd is not imported", "PLANTED" not in r.stdout + r.stderr and r.returncode == 0,
              r.stdout + r.stderr)

        # ── Enrollment keeps a seed only after a code from it checks out ─────
        eseed = tmp / "enroll" / "totp.seed"
        p = subprocess.Popen([str(helper), "enroll"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, cwd=td,
                             env={**clean, "MOSES_MFA_SEED": str(eseed), "MOSES_MFA_STATE": str(tmp / "es"),
                                  "MOSES_MFA_NOW": str(T0)})
        out = ""
        secret = ""
        while p.stdout and "to confirm" not in out:
            line = p.stdout.readline()
            if not line:
                break
            out += line
            if line.strip().startswith("secret:"):
                secret = line.split(":", 1)[1].strip()
        o, e = p.communicate("999999\n" if not secret or hotp(secret, int(T0 // 30)) != "999999" else "000000\n", timeout=30)
        check("enroll with a wrong confirmation saves nothing", p.returncode == 1 and not eseed.exists(), (o, e))
        p = subprocess.Popen([str(helper), "enroll"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, cwd=td,
                             env={**clean, "MOSES_MFA_SEED": str(eseed), "MOSES_MFA_STATE": str(tmp / "es"),
                                  "MOSES_MFA_NOW": str(T0)})
        out, secret = "", ""
        while p.stdout and "to confirm" not in out:
            line = p.stdout.readline()
            if not line:
                break
            out += line
            if line.strip().startswith("secret:"):
                secret = line.split(":", 1)[1].strip()
        o, e = p.communicate(hotp(secret, int(T0 // 30)) + "\n" if secret else "\n", timeout=30)
        check("enroll with the right confirmation saves the seed, 0600",
              p.returncode == 0 and eseed.exists() and stat.S_IMODE(eseed.stat().st_mode) == 0o600
              and eseed.read_text().strip() == secret, (o, e))
        r = subprocess.run([str(helper), "enroll"], capture_output=True, text=True, cwd=td,
                           env={**clean, "MOSES_MFA_SEED": str(eseed), "MOSES_MFA_STATE": str(tmp / "es")})
        check("enroll will not silently replace a seed", r.returncode == 1 and "--rotate" in r.stderr, r.stderr)

    # ── As root, the overrides are ignored ───────────────────────────────────
    # Loaded with geteuid faked to 0: the paths must be the fixed ones, whatever the environment says.
    real = os.geteuid
    saved = {k: os.environ.get(k) for k in ("MOSES_MFA_SEED", "MOSES_MFA_STATE", "MOSES_MFA_NOW", "MOSES_MFA_PAUSE")}
    try:
        os.environ.update(MOSES_MFA_SEED="/tmp/attacker.seed", MOSES_MFA_STATE="/tmp/attacker", MOSES_MFA_NOW="1",
                          MOSES_MFA_PAUSE="9")
        os.geteuid = lambda: 0
        loader = importlib.machinery.SourceFileLoader("moses_mfa_as_root", str(helper))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        m = importlib.util.module_from_spec(spec)
        loader.exec_module(m)
        check("as root, MOSES_MFA_SEED/STATE/NOW are ignored",
              m.SEED == "/etc/moses/mfa/totp.seed" and m.STATE == "/var/lib/moses/mfa" and m.now() > 1e9,
              (m.SEED, m.STATE))
        # RFC 6238 appendix B, SHA-1, T=59 → 94287082; six digits is the tail.
        check("TOTP matches the RFC 6238 test vector", m.hotp(b"12345678901234567890", 1) == "287082", m.hotp(b"12345678901234567890", 1))
    finally:
        os.geteuid = real
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    return passed, failed


# ── Sabotage: each guard removed must turn its own check red ─────────────────
MUTANTS = [
    ("no replay guard", "if counter is not None and counter <= s.d[\"last_counter\"]:",
     "if False:", "a used code cannot authorize again (replay)"),
    ("unlimited tries", "if left <= 0:\n                s.d[\"challenge\"] = None",
     "if False:\n                s.d[\"challenge\"] = None",
     "three wrong codes close the challenge — the right one is then refused"),
    ("no lockout", "if len(s.d[\"failures\"]) >= LOCK_FAILS:", "if False:", "locked: no new challenge for a day"),
    ("expired codes compared", "if c[\"expires\"] <= t:", "if False:", "an expired challenge refuses even the right code"),
    ("overrides honored as root", "if not ROOT:\n    SEED", "if True:\n    SEED", "as root, MOSES_MFA_SEED/STATE/NOW are ignored"),
    ("code logged", "detail=detail,\n", "detail=detail + code,\n", "the code is never written to the ledger"),
    ("no lock around state", "fcntl.flock(self.lock, fcntl.LOCK_EX)", "pass",
     "four racing verifies of one code: exactly one authorizes"),
    ("any purpose", "if kind not in KINDS:", "if False:", "only new-build can be authorized"),
    ("a new project replaces the open request", "            if old[\"purpose\"] != purpose:", "            if False:",
     "while one request is open, a request for another project is refused"),
    ("not single-use", "if not a or a[\"consumed\"] or", "if not a or", "a consumed authorization cannot be used twice"),
]

if __name__ == "__main__":
    print("THE REAL HELPER")
    passed, failed = suite(HELPER)
    print(f"\n  {len(passed)}/{len(passed) + len(failed)} checks pass against agent/moses-mfa")

    print("\nWATCH EACH GUARD FAIL — the helper broken on purpose, one guard at a time")
    src = HELPER.read_text()
    blind = []
    with tempfile.TemporaryDirectory() as md:
        for name, old, new, expect in MUTANTS:
            if src.count(old) != 1:
                blind.append(name)
                print(f"  FAIL {name}: the sabotage no longer applies — the guard moved; update MUTANTS")
                continue
            mutant = Path(md) / "moses-mfa"
            mutant.write_text(src.replace(old, new))
            mutant.chmod(0o755)
            _, mfailed = suite(mutant)
            red = expect in mfailed
            print(f"  {'ok  ' if red else 'FAIL'} {name} → '{expect}' goes red")
            if not red:
                blind.append(name)

    if failed or blind:
        print(f"\nFAILED: {len(failed)} check(s), {len(blind)} guard(s) the suite cannot see fail")
        sys.exit(1)
    print(f"\nall {len(passed)} checks pass, and all {len(MUTANTS)} guards were watched failing")
