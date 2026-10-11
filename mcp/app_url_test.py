#!/usr/bin/env python3
"""The product's URL setting: MOSES_APP_URL is the name, VIATICA_APP_URL is still read.

    ~/moses-venv/bin/python mcp/app_url_test.py

The setting was named after the author's product. It has a generic name now, and the old one must keep
working, because the operator's files on a running machine still say VIATICA_APP_URL. Checked on the
real readers — the request each one builds, caught before it leaves — not on a copy of their logic.
Nothing is fetched: urlopen is stubbed, and the config files are temporary.
"""
import io, json, os, sys, tempfile, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import live_state           # noqa: E402
import personas             # noqa: E402
import viatica_incidents    # noqa: E402

fails = []
def check(label, cond, detail=""):
    print(f"  {'✓' if cond else '✗'} {label}" + ("" if cond else f"  — {detail}"))
    if not cond: fails.append(label)

NEW, OLD = "https://new.example.test", "https://old.example.test"
_tmp = tempfile.mkdtemp(prefix="moses-appurl-")

seen = []
class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False
def _fake_urlopen(req, timeout=None):
    seen.append(req.full_url)
    return _Resp(json.dumps({"incidents": [], "open": 0}).encode())
urllib.request.urlopen = _fake_urlopen

def _conf_file(lines):
    path = os.path.join(_tmp, f"customs-{len(seen)}-{len(lines)}.env")
    with open(path, "w") as fh:
        fh.write("\n".join(lines + ["CRON_SECRET=s3cret"]) + "\n")
    return path

def _incidents_url(lines):
    viatica_incidents.CONF = _conf_file(lines)
    seen.clear()
    data, err = viatica_incidents._call("/api/incidents")
    return (seen[0] if seen else None), err

def _live_state_url(lines):
    live_state.CONF = _conf_file(lines)
    live_state.CACHE = os.path.join(_tmp, "state.json")
    if os.path.exists(live_state.CACHE):
        os.remove(live_state.CACHE)
    seen.clear()
    live_state.fetch()
    return seen[0] if seen else None

print("the incident queue (customs.env)")
url, err = _incidents_url([f"MOSES_APP_URL={NEW}", f"VIATICA_APP_URL={OLD}"])
check("MOSES_APP_URL wins when both are set", url and url.startswith(NEW), f"{url} / {err}")
url, err = _incidents_url([f"VIATICA_APP_URL={OLD}"])
check("VIATICA_APP_URL alone still works", url and url.startswith(OLD), f"{url} / {err}")
url, err = _incidents_url([])
check("neither set: not configured, and it says the new name", url is None and "MOSES_APP_URL" in (err or ""), err)

print("the live state (customs.env)")
url = _live_state_url([f"MOSES_APP_URL={NEW}", f"VIATICA_APP_URL={OLD}"])
check("MOSES_APP_URL wins when both are set", url and url.startswith(NEW), url)
url = _live_state_url([f"VIATICA_APP_URL={OLD}"])
check("VIATICA_APP_URL alone still works", url and url.startswith(OLD), url)

print("Big Pipe and Tagilla (the service's environment)")
os.environ["CRON_SECRET"] = "s3cret"
os.environ["MOSES_APP_URL"], os.environ["VIATICA_APP_URL"] = NEW, OLD
check("MOSES_APP_URL wins when both are set", personas._config()[0] == NEW, personas._config())
del os.environ["MOSES_APP_URL"]
check("VIATICA_APP_URL alone still works", personas._config()[0] == OLD, personas._config())
del os.environ["VIATICA_APP_URL"]
try:
    personas._config()
    check("neither set: refused", False, "no error raised")
except personas.CustomsError as e:
    check("neither set: refused, naming the new setting", "MOSES_APP_URL" in str(e), str(e))

print(f"\n{'FAILED: ' + ', '.join(fails) if fails else 'all passed'}")
sys.exit(1 if fails else 0)
