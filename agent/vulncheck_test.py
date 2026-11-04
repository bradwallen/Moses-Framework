#!/usr/bin/env python3
"""moses-vulncheck's decisions, driven with fixed data — then each guard is removed and its check must fail.

    python3 agent/vulncheck_test.py

The cases are the ones that started it (2026-10-23): a Node one patch behind its line's security release,
a Node line past end of life, a critical package that ships versus one that is only build tooling, apt's
security pocket, an internet-facing package with an update in an ordinary pocket, and — the one that
matters most — a check that could not run must never read as all clear. Nothing here touches the network.
"""
import datetime as dt
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_loader = importlib.machinery.SourceFileLoader("moses_vulncheck", str(HERE / "moses-vulncheck"))
_spec = importlib.util.spec_from_loader("moses_vulncheck", _loader)
vc = importlib.util.module_from_spec(_spec)
_loader.exec_module(vc)

TODAY = dt.date(2026, 10, 23)
INDEX = [
    {"version": "v26.12.1", "date": "2026-10-21", "security": True},
    {"version": "v24.22.1", "date": "2026-10-20", "security": True},
    {"version": "v24.22.0", "date": "2026-10-01", "security": False},
    {"version": "v24.21.0", "date": "2026-09-10", "security": False},
    {"version": "v22.23.4", "date": "2026-10-21", "security": True},
    {"version": "v22.23.3", "date": "2026-09-15", "security": False},
    {"version": "v20.20.0", "date": "2026-03-01", "security": True},
]
SCHEDULE = {"v20": {"end": "2026-04-30"}, "v22": {"end": "2027-04-30"}, "v24": {"end": "2028-04-30"},
            "v26": {"end": "2029-04-30"}}
APT = """Listing...
openssl/resolute-updates,resolute-security 3.5.1-1ubuntu1.2 amd64 [upgradable from: 3.5.1-1ubuntu1.1]
cloudflared/unknown 2026.10.3 amd64 [upgradable from: 2026.8.3]
alsa-ucm-conf/resolute-updates 1.2.15.3-1ubuntu1.7 all [upgradable from: 1.2.15.3-1ubuntu1.5]
"""
AUDIT = {"metadata": {"vulnerabilities": {"critical": 1, "high": 1, "moderate": 2}},
         "vulnerabilities": {
             "sharp": {"severity": "high", "isDirect": True, "fixAvailable": True,
                       "via": [{"title": "Vulnerability in librsvg"}]},
             "next": {"severity": "critical", "isDirect": True,
                      "fixAvailable": {"name": "next", "version": "16.4.3"},
                      "via": [{"title": "Remote Code Execution in next/og ImageResponse"}]},
             "vite": {"severity": "moderate", "isDirect": False, "fixAvailable": True, "via": ["esbuild"]}}}
CLEAN = {"critical": 0, "high": 0, "moderate": 0, "worst": []}


def suite(verbose):
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        if verbose:
            print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' — ' + str(detail)[:200]}")

    v = vc.node_verdict("v24.20.0", INDEX, SCHEDULE, TODAY)
    check("a Node behind its line's security release is vulnerable, and the fix is the line's latest",
          v["state"] == "vulnerable" and v["fix"] == "24.22.1", v)
    v = vc.node_verdict("v22.23.4", INDEX, SCHEDULE, TODAY)
    check("a Node at its line's security release is fine", v["state"] == "ok", v)
    v = vc.node_verdict("v24.21.0", INDEX, SCHEDULE, TODAY)
    check("newer releases that fix no security bug are not a finding on their own — 24.22.0 is not one",
          v["state"] == "vulnerable" and "1 security release" in v["why"], v)
    v = vc.node_verdict("v20.20.0", INDEX, SCHEDULE, TODAY)
    check("a line past end of life is dead even when it is that line's latest", v["state"] == "end-of-life", v)
    v = vc.node_verdict("not a version", INDEX, SCHEDULE, TODAY)
    check("an unreadable version is unknown, never fine", v["state"] == "unknown", v)

    sec, facing = vc.apt_findings(vc.parse_apt(APT))
    check("apt's security pocket is recognized in a multi-pocket line", [u["pkg"] for u in sec] == ["openssl"], sec)
    check("an internet-facing package with an update in an ordinary pocket is flagged",
          [u["pkg"] for u in facing] == ["cloudflared"], facing)

    a = vc.summarize_audit(AUDIT)
    check("npm audit counts by severity, critical first, with the fixed version",
          (a["critical"], a["high"]) == (1, 1) and a["worst"][0]["pkg"] == "next" and a["worst"][0]["fix"] == "next@16.4.3", a)

    check("code-server behind its latest release is said",
          vc.code_server_verdict("4.140.0", "v4.141.0")["state"] == "behind")
    check("code-server at its latest is fine", vc.code_server_verdict("4.141.0", "v4.141.0")["state"] == "ok")

    base = {"host": "devbox", "runtimes": [], "projects": [], "apt_security": [], "apt_facing": [],
            "code_server": {"state": "ok", "why": ""}, "could_not_check": []}
    text = vc.render({**base, "runtimes": [{"where": "the app in production", "version": "v22.23.3",
                                            "verdict": vc.node_verdict("v22.23.3", INDEX, SCHEDULE, TODAY)}]})
    check("the post names a vulnerable runtime, where it runs, and what to update to",
          "1 thing(s) need fixing" in text and "the app in production" in text and "update to *22.23.4*" in text, text)
    text = vc.render({**base, "projects": [{"name": "app", "prod": vc.summarize_audit(AUDIT), "all": vc.summarize_audit(AUDIT)}]})
    check("a critical package that SHIPS is a finding", "1 thing(s) need fixing" in text and "next" in text, text)
    text = vc.render({**base, "projects": [{"name": "app", "prod": CLEAN, "all": vc.summarize_audit(AUDIT)}]})
    check("one only in build tooling is mentioned, not counted as a finding",
          "all clear" in text and "build tooling only" in text, text)
    text = vc.render({**base, "could_not_check": ["apt (exit 100)"]})
    check("a check that could not run is never 'all clear'", "all clear" not in text and "could not run" in text
          and "apt (exit 100)" in text, text)
    text = vc.render({**base, "runtimes": [{"where": "the app in production", "version": "v22.23.4",
                                            "verdict": vc.node_verdict("v22.23.4", INDEX, SCHEDULE, TODAY)}]})
    check("an all-clear says what it checked", "all clear" in text and "Checked and fine: the app in production" in text, text)

    mon = dt.datetime(2026, 10, 26, 8, 0)        # a Monday, at the standup
    saved = {"checked_at": "2026-10-26T07:40:05", "report": "🦅 *Weekly vulnerability check* — all clear"}
    check("the standup prints the report saved this morning", vc.section(saved, mon, "Mon") == saved["report"])
    old_run = {"checked_at": "2026-10-19T07:40:05", "report": "last week's"}
    out = vc.section(old_run, mon, "Mon")
    check("on the check day, an old result is said, never printed as today's",
          "did not run this morning" in out and "last week's" not in out and "Mon Oct 19" in out, out)
    check("on the check day, no result at all is said too", "did not run" in vc.section(None, mon, "Mon"))
    check("any other day the standup carries nothing", vc.section(old_run, dt.datetime(2026, 10, 27, 8, 0), "Mon") == "")
    return passed, failed


_real = {k: getattr(vc, k) for k in ("node_verdict", "apt_findings", "summarize_audit", "render", "INTERNET_FACING", "section")}


def _no_security_check(version, index, schedule, today):
    v = _real["node_verdict"](version, [dict(r, security=False) for r in index], schedule, today)
    return v


def _no_eol(version, index, schedule, today):
    return _real["node_verdict"](version, index, {}, today)


def _render_ignoring_blind(r):
    return _real["render"]({**r, "could_not_check": []})


def _prod_ignored(r):
    return _real["render"]({**r, "projects": [{**p, "prod": CLEAN} for p in r.get("projects", [])]})


MUTANTS = [
    ("security flag ignored", "node_verdict", _no_security_check,
     "a Node behind its line's security release is vulnerable, and the fix is the line's latest"),
    ("end of life ignored", "node_verdict", _no_eol, "a line past end of life is dead even when it is that line's latest"),
    ("security pocket ignored", "apt_findings", lambda ups: ([], [u for u in ups if u["pkg"] in vc.INTERNET_FACING]),
     "apt's security pocket is recognized in a multi-pocket line"),
    ("internet-facing list empty", "INTERNET_FACING", (), "an internet-facing package with an update in an ordinary pocket is flagged"),
    ("could-not-check dropped", "render", _render_ignoring_blind, "a check that could not run is never 'all clear'"),
    ("shipping packages ignored", "render", _prod_ignored, "a critical package that SHIPS is a finding"),
    ("an old result passed off as today's", "section",
     lambda saved, now, day="Mon": (saved or {}).get("report", "") or _real["section"](saved, now, day),
     "on the check day, an old result is said, never printed as today's"),
    ("a missed run left silent", "section",
     lambda saved, now, day="Mon": _real["section"](saved, now, day) if saved and "did not run" not in _real["section"](saved, now, day) else "",
     "on the check day, no result at all is said too"),
]

if __name__ == "__main__":
    print("WEEKLY VULNERABILITY CHECK")
    passed, failed = suite(True)
    print(f"\n  {len(passed)}/{len(passed) + len(failed)} pass\n\nWATCH EACH GUARD FAIL")
    blind = []
    for name, attr, sabotage, expect in MUTANTS:
        setattr(vc, attr, sabotage)
        try:
            _, mf = suite(False)
        finally:
            for k, x in _real.items():
                setattr(vc, k, x)
        red = expect in mf
        print(f"  {'ok  ' if red else 'FAIL'} {name} → '{expect}' goes red")
        if not red:
            blind.append(name)
    if failed or blind:
        print(f"\nFAILED: {len(failed)} check(s), {len(blind)} guard(s) not watched failing")
        sys.exit(1)
    print(f"\nall {len(passed)} pass, and every guard was watched failing")
