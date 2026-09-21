"""hwprobe_test — the health strip must see failed USER units, and must never read "could not look" as clean.

    python3 agent/lib/hwprobe_test.py

Most of Moses runs in brad's user manager. health_facts() used to ask only the system manager, so a
failed weekly self-test showed nothing on the dashboard. These cases pin both directions: a failed
user unit is reported, and an unreachable user bus is reported as unchecked rather than as healthy.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("hwprobe", HERE / "hwprobe.py")
hwprobe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hwprobe)

dspec = importlib.util.spec_from_file_location("dashboard_server", HERE.parent.parent / "mcp" / "dashboard_server.py")

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label} {detail}")


def fake_run(user_result):
    """subprocess.run stand-in: the --user query returns user_result; nothing else is called."""
    def run(cmd, **kw):
        assert "--user" in cmd, cmd
        if isinstance(user_result, Exception):
            raise user_result
        return user_result
    return run


def with_fakes(system_out, user_result):
    real_sh, real_run = hwprobe.sh, hwprobe.subprocess.run
    hwprobe.sh = lambda cmd, timeout=8: system_out if "list-units" in cmd else ""
    hwprobe.subprocess.run = fake_run(user_result)
    try:
        return hwprobe.health_facts()
    finally:
        hwprobe.sh, hwprobe.subprocess.run = real_sh, real_run


ok = SimpleNamespace(returncode=0, stdout="", stderr="")
red = SimpleNamespace(returncode=0, stdout="moses-gate-selftest.service loaded failed failed Prove the gate\n", stderr="")
nobus = SimpleNamespace(returncode=1, stdout="", stderr="Failed to connect to user scope bus via local transport\n")

print("hwprobe — failed units")
h = with_fakes("", ok)
check("all clean → no failed_units, nothing unchecked", "failed_units" not in h and "unchecked" not in h, h)

h = with_fakes("", red)
check("a failed USER unit is reported", h.get("failed_units") == ["moses-gate-selftest.service (user)"], h)

h = with_fakes("nginx.service loaded failed failed nginx\n", red)
check("system and user failures both reported", h.get("failed_units") == ["nginx.service", "moses-gate-selftest.service (user)"], h)

h = with_fakes("", nobus)
check("no user bus → unchecked, NOT clean", "failed_units" not in h and h.get("unchecked", [""])[0].startswith("user units: Failed to connect"), h)

h = with_fakes("", subprocess.TimeoutExpired("systemctl", 8))
check("a timeout → unchecked, NOT clean", h.get("unchecked", [""])[0].startswith("user units: systemctl --user did not run"), h)

# The dashboard must render it — a fact nobody sees is the bug this test exists for.
try:
    ds = importlib.util.module_from_spec(dspec)
    dspec.loader.exec_module(ds)
    strip = ds._health_strip({"failed_units": ["moses-gate-selftest.service (user)"]})
    check("dashboard shows the failed user unit", "moses-gate-selftest.service (user)" in strip, strip)
    strip = ds._health_strip({"unchecked": ["user units: no user bus"]})
    check("dashboard shows an unchecked source", "not checked" in strip and "no user bus" in strip, strip)
    check("dashboard stays silent when healthy", ds._health_strip({}) == "")
except Exception as e:  # noqa: BLE001
    check("dashboard module imports", False, repr(e))

print(f"  {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
