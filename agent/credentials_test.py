#!/usr/bin/env python3
"""moses-credentials against a scratch home with planted secrets. Run: python3 agent/credentials_test.py

What must hold: every planted secret is found by NAME, no value appears anywhere in the output, a .bak
copy is told apart as a duplicate or an OLD value, a loose mode is a finding, an unreadable folder is
said out loud. Then each property is broken on purpose and must go red (commandment 8).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="moses-cred-"))
os.environ["MOSES_CREDENTIALS_HOME"] = str(TMP)
os.environ["MOSES_CREDENTIAL_OPAQUE"] = str(TMP / "shut" / "inner")
loader = importlib.machinery.SourceFileLoader("moses_credentials", str(HERE / "moses-credentials"))
spec = importlib.util.spec_from_loader("moses_credentials", loader)
M = importlib.util.module_from_spec(spec)
loader.exec_module(M)
M.DEFAULT_OPAQUE = []
M.SCRIPT_DIRS = ["scripts"]

VALUES = {"live": "LIVEsecret0000000000000001", "old": "OLDsecret00000000000000002",
          "loose": "LOOSEsecret000000000000003", "app": "APPsecret00000000000000004",
          "json": "JSONsecret0000000000000005"}


def put(rel: str, text: str, mode: int = 0o600) -> Path:
    p = TMP / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    os.chmod(p, mode)
    return p


put(".config/moses/svc.env", f"SVC_TOKEN={VALUES['live']}\nSVC_CHANNEL=C123\n")
put(".config/moses/svc.env.bak-1", f"SVC_TOKEN={VALUES['live']}\n")
put(".config/moses/svc.env.bak-2", f"SVC_TOKEN={VALUES['old']}\n")
put(".config/moses/loose.env", f"OTHER_API_KEY={VALUES['loose']}\n", 0o644)
put("Projects/app/.env", f"AUTH_SECRET={VALUES['app']}\nNEXT_PUBLIC_APP_URL=https://x\n")
put(".claude/.credentials.json", '{"claudeAiOauth": {"accessToken": "%s"}}' % VALUES["json"])
put(".ssh/id_test", "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n")
put("scripts/uses-svc", "#!/bin/sh\n. ~/.config/moses/svc.env\n", 0o755)
(TMP / "shut" / "inner").mkdir(parents=True)
os.chmod(TMP / "shut", 0o000)

FAILS: list[str] = []


def check(label: str, cond: bool, quiet: bool = False) -> None:
    if not quiet:
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        FAILS.append(label)


def suite(quiet: bool = False) -> int:
    before = len(FAILS)
    out = M.build()
    c = lambda label, cond: check(label, cond, quiet)  # noqa: E731
    c("every planted secret is found by name",
      all(n in out for n in ("SVC_TOKEN", "OTHER_API_KEY", "AUTH_SECRET", "claudeAiOauth.accessToken",
                              "(private key)")))
    c("a name that only looks related (SVC_CHANNEL, NEXT_PUBLIC_APP_URL) is not called a secret",
      "SVC_CHANNEL" not in out and "NEXT_PUBLIC_APP_URL" not in out)
    c("NO VALUE appears anywhere in the output", not any(v in out for v in VALUES.values()) and "AAAA" not in out)
    c("a .bak with the live value is a duplicate", "svc.env.bak-1` is a duplicate copy" in out)
    c("a .bak with another value is an OLD secret", "svc.env.bak-2` holds an OLD value of SVC_TOKEN" in out)
    c("a secret file readable beyond its owner is a finding", "loose.env` is mode 644" in out)
    c("the script that reads a file is named", "~/scripts/uses-svc" in out)
    c("an app's .env is read by the app, not 'no reader'",
      "the app in Projects/app" in out and "Projects/app/.env` has no reader" not in out)
    c("a folder this account cannot look into is said out loud", "shut/inner" in out and "cannot read it" in out)
    return len(FAILS) - before


print("── the inventory ──")
suite()
p = TMP / "out.md"
os.environ["MOSES_CREDENTIALS_OUT"] = str(p)
M.OUT = p
sys.argv = ["moses-credentials"]
M.main()
check("the written file is mode 600 (it maps where every secret lives)", (p.stat().st_mode & 0o777) == 0o600)

print("── --live: an old copy the provider still accepts ──")
asked = []
real_live_check = M.check_live
M.check_live = lambda k, v: (asked.append(v), "Cloudflare: active, expires NEVER" if v == VALUES["old"] else
                             "provider answered 401 (dead or not this kind of token)")[1]
try:
    out_live = M.build(live=True)
finally:
    M.check_live = real_live_check
check("an OLD value the provider still accepts is a finding: revoke it",
      "svc.env.bak-2` holds a SVC_TOKEN that the provider still ACCEPTS" in out_live)
check("a .bak's copy of the live value is not asked about again", asked.count(VALUES["live"]) == 1)
check("and still no value in the output", not any(v in out_live for v in VALUES.values()))

print("── watch it fail ──")
saved = len(FAILS)


def mutant(label, patch, undo):
    patch()
    try:
        reds = suite(quiet=True)
    finally:
        undo()
    del FAILS[saved:]
    check(f"MUTANT {label} is caught ({reds} red)", reds > 0)


real_secret = M.is_secret_name
mutant("nothing looks like a secret", lambda: setattr(M, "is_secret_name", lambda n: False),
       lambda: setattr(M, "is_secret_name", real_secret))
real_read = M.read_vars


def leaky(path):
    v = real_read(path)
    return {f"{k}={val}": val for k, val in v.items()} if v else v
mutant("values leak into the names", lambda: setattr(M, "read_vars", leaky), lambda: setattr(M, "read_vars", real_read))
real_live = M.live_of
mutant(".bak copies not traced to their live file", lambda: setattr(M, "live_of", lambda p: None),
       lambda: setattr(M, "live_of", real_live))

os.chmod(TMP / "shut", 0o700)
print(f"\n  {'all good' if not FAILS else str(len(FAILS)) + ' FAILED'}")
sys.exit(1 if FAILS else 0)
