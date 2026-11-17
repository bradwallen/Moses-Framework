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


# Two OSV records for starlette 1.0.0 as osv.dev returns them, plus the same HIGH one under its PYSEC name.
OSV_STARLETTE = [
    {"id": "GHSA-82w8-qh3p-5jfq", "aliases": ["CVE-2026-54283", "PYSEC-2026-249"], "summary": "form limits ignored",
     "database_specific": {"severity": "HIGH"},
     "affected": [{"package": {"name": "starlette"}, "ranges": [{"events": [{"introduced": "0.4.1"}, {"fixed": "1.3.1"}]}]}]},
    {"id": "PYSEC-2026-249", "aliases": ["CVE-2026-54283", "GHSA-82w8-qh3p-5jfq"], "summary": "same one",
     "affected": [{"package": {"name": "starlette"}, "ranges": [{"events": [{"introduced": "0"}, {"fixed": "1.3.1"}]}]}]},
    {"id": "GHSA-86qp-5c8j-p5mr", "aliases": ["CVE-2026-48710"], "summary": "Host header poisons request.url.path",
     "database_specific": {"severity": "MODERATE"},
     "affected": [{"package": {"name": "Starlette"}, "ranges": [{"events": [{"introduced": "0"}, {"fixed": "0.9.0"},
                                                                         {"introduced": "0.10"}, {"fixed": "1.0.1"}]}]}]},
]


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

    # ── 5. Python packages (2026-11-16: Atlas's Starlette heads-up; nothing here looked at Python) ──
    check("a METADATA header gives name and version", vc.read_metadata(
        "Metadata-Version: 2.4\nName: Starlette\nVersion: 1.0.0\n\nName: not-this\n") == ("Starlette", "1.0.0"))
    f = vc.summarize_osv("starlette", "1.0.0", OSV_STARLETTE)
    check("one advisory under two names is counted once, by its CVE, with the first fix past the installed version",
          len(f) == 2 and f[0]["id"] == "CVE-2026-54283" and f[0]["fix"] == "1.3.1"
          and {x["id"] for x in f} == {"CVE-2026-54283", "CVE-2026-48710"}, f)
    check("worst first, and an advisory with no severity is not ranked as mild",
          [x["severity"] for x in f] == ["HIGH", "MODERATE"]
          and vc.summarize_osv("x", "1", [{"id": "GHSA-1", "affected": []}])[0]["severity"] == "UNKNOWN", f)
    finding = [{"pkg": "starlette", "version": "1.0.0", **x} for x in f]
    running = {"venv": "/srv/v", "label": "mcp_server.py (agent)", "running": True, "findings": finding}
    text = vc.render({**base, "python": [running]})
    check("a running venv with a known vulnerability is a finding, with the version that fixes it all",
          "1 thing(s) need fixing" in text and "mcp_server.py (agent)" in text and "starlette 1.0.0 → 1.3.1" in text, text)
    text = vc.render({**base, "python": [dict(running, running=False, label="~/old/venv")]})
    check("a venv nothing runs from is mentioned, not counted", "all clear" in text and "(not running)" in text, text)
    text = vc.render({**base, "python": [dict(running, findings=[])]})
    check("a clean venv is said as checked", "Python packages in 1 venv(s), 1 of them running" in text, text)

    fake = {"running_venvs": lambda: {"/home/agent/venv": {"who": "agent", "runs": ["mcp_server.py"]},
                                      "/home/op/venv": {"who": "op", "runs": ["app.py"]}},
            "project_venvs": lambda: ["/home/op/venv", "/home/op/proj/.venv"],
            "venv_packages": lambda v: None if v.startswith("/home/agent") else {"starlette": "1.4.1"},
            "peer_venvs": lambda now: ({}, ""),
            "osv_lookup": lambda pairs: {}}
    saved_fns = {k: getattr(vc, k) for k in fake}
    try:
        for k, fn in fake.items():
            setattr(vc, k, fn)
        envs, blind = vc.collect_python(0)
        check("a running venv this account cannot read is said by name, never skipped",
              any("/home/agent/venv" in b for b in blind) and [e["venv"] for e in envs] == ["/home/op/venv", "/home/op/proj/.venv"],
              (envs, blind))
        check("a venv is running only when a process runs from it",
              [e["running"] for e in envs] == [True, False], envs)
        vc.peer_venvs = lambda now: ({"/home/agent/venv": {"starlette": "1.0.0"}}, "")
        vc.osv_lookup = lambda pairs: {("starlette", "1.0.0"): OSV_STARLETTE} if ("starlette", "1.0.0") in pairs else {}
        envs, blind = vc.collect_python(0)
        agent = next((e for e in envs if e["venv"] == "/home/agent/venv"), None)
        check("the agent's own report stands in for the venv this account cannot read",
              agent and agent["running"] and agent["findings"] and not blind, (envs, blind))
        def _down(pairs):
            raise OSError("no route")
        vc.osv_lookup = _down
        envs, blind = vc.collect_python(0)
        check("OSV not answering is said, never read as clean", not envs and any("osv.dev" in b for b in blind), (envs, blind))
    finally:
        for k, fn in saved_fns.items():
            setattr(vc, k, fn)
    return passed, failed


_real = {k: getattr(vc, k) for k in ("node_verdict", "apt_findings", "summarize_audit", "render", "INTERNET_FACING",
                                     "section", "summarize_osv", "collect_python")}


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
    ("Python dropped from the post", "render", lambda r: _real["render"]({**r, "python": []}),
     "a running venv with a known vulnerability is a finding, with the version that fixes it all"),
    ("aliases counted twice", "summarize_osv",
     lambda pkg, ver, vulns: [x for v in vulns for x in _real["summarize_osv"](pkg, ver, [v])],
     "one advisory under two names is counted once, by its CVE, with the first fix past the installed version"),
    ("an unreadable venv skipped", "collect_python",
     lambda now: (lambda e, b: (e, [x for x in b if "cannot read" not in x]))(*_real["collect_python"](now)),
     "a running venv this account cannot read is said by name, never skipped"),
    ("OSV failure read as clean", "collect_python",
     lambda now: (lambda e, b: (e, [x for x in b if "osv.dev" not in x]))(*_real["collect_python"](now)),
     "OSV not answering is said, never read as clean"),
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
