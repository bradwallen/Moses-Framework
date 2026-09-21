#!/usr/bin/env python3
"""What a machine says about itself. Run locally for Reserve, piped over SSH for Labs.

ONE SCRIPT FOR BOTH HOSTS, on purpose. The first version embedded a Python one-liner inside an SSH
argument inside a bash function, which meant three levels of quoting and a shape that could drift
from the local collector without anyone noticing. Piping this file to `python3 -` on the far end
means Reserve and Labs are measured by identical code by construction.

NOTHING HERE NEEDS ROOT. Every field comes from /proc, /sys or an unprivileged tool. RAM type and
speed live behind `dmidecode`, which needs root, so they are simply absent rather than guessed at.
A field that cannot be read is omitted, never invented.
"""
import glob, json, os, re, subprocess, time


def read(p, d=""):
    try:
        with open(p) as fh:
            return fh.read().strip().replace("\0", "")
    except OSError:
        return d


def sh(cmd, timeout=8):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def first(pattern, text, group=1):
    m = re.search(pattern, text, re.M)
    return m.group(group).strip() if m else ""


def cpu_facts():
    ci, ls = read("/proc/cpuinfo"), sh("lscpu")
    out = {}
    # x86 reports "model name"; the Pi reports "Model" for the board and gives the core via lscpu.
    out["model"] = (first(r"^model name\s*:\s*(.+)$", ci)
                    or first(r"^Model name:\s*(.+)$", ls)
                    or first(r"^Hardware\s*:\s*(.+)$", ci))
    out["arch"] = first(r"^Architecture:\s*(.+)$", ls) or os.uname().machine
    out["vendor"] = first(r"^vendor_id\s*:\s*(.+)$", ci)
    for label, pat in (("family", r"^CPU family:\s*(.+)$"), ("model_id", r"^Model:\s*(.+)$"),
                       ("stepping", r"^Stepping:\s*(.+)$"), ("l3", r"^L3 cache:\s*(.+)$"),
                       ("max_mhz", r"^CPU max MHz:\s*(.+)$"),
                       ("cores_per_socket", r"^Core\(s\) per socket:\s*(.+)$"),
                       ("threads_per_core", r"^Thread\(s\) per core:\s*(.+)$"),
                       ("sockets", r"^Socket\(s\):\s*(.+)$")):
        v = first(pat, ls)
        if v and v != "-":          # lscpu prints "-" on the Pi; a dash is not a fact
            out[label] = v
    out["threads"] = os.cpu_count() or 0
    # What the cores are doing RIGHT NOW, which is the half a spec sheet cannot tell you.
    cur = [int(read(f) or 0) for f in glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq")]
    if cur:
        out["now_mhz"] = round(sum(cur) / len(cur) / 1000)
    return {k: v for k, v in out.items() if v not in ("", None)}


def board_facts():
    """DMI, the world-readable half. RAM type and speed need root and are therefore absent."""
    d = "/sys/devices/virtual/dmi/id"
    out = {k: read(f"{d}/{k}") for k in
           ("sys_vendor", "product_name", "board_vendor", "board_name", "bios_version", "bios_date")}
    if not any(out.values()):
        # A Pi has no DMI at all. Fall back to what it does publish about itself.
        model = read("/proc/device-tree/model")
        if model:
            out = {"sys_vendor": "Raspberry Pi Foundation", "product_name": model,
                   "board_name": first(r"^Revision\s*:\s*(.+)$", read("/proc/cpuinfo"))}
    return {k: v for k, v in out.items() if v}


def pci_facts():
    """GPU and network controllers, named by the vendor's own PCI database.

    This is where the microarchitecture comes from — lspci prints "CoffeeLake-S GT2 [UHD Graphics
    630]" because the PCI ID database says so. Deliberately NOT a CPUID-to-codename table of my own:
    a lookup table I maintain is a lookup table that goes stale and lies, and the machine already
    knows the answer.
    """
    out = {"gpu": [], "net": []}
    for ln in sh("lspci").splitlines():
        low = ln.lower()
        addr = ln.split()[0] if ln.split() else ""
        body = ln.split(": ", 1)[1] if ": " in ln else ln
        if "vga compatible" in low or " 3d controller" in low:
            out["gpu"].append(body)
        elif "ethernet controller" in low or "network controller" in low:
            # ADDRESS KEPT, because the page matches an interface to its controller by it. The first
            # version dropped it and the lookup silently fell back to "whichever line", which on a
            # three-NIC board is a coin toss dressed as a fact.
            out["net"].append({"addr": addr, "desc": body})
    return {k: v for k, v in out.items() if v}


def net_facts():
    """Real interfaces, not just the controllers on the bus.

    lspci alone lists every NIC the board has and says nothing about which one is CARRYING traffic —
    on this machine that is one of three. Each interface is matched back to its PCI description
    through /sys/class/net/<if>/device, so the row can say both what the part is and whether it is
    plugged in.

    `speed` reads -1 on a down interface, which is the kernel saying "no link", not a speed.
    """
    out = []
    for path in sorted(glob.glob("/sys/class/net/*")):
        name = os.path.basename(path)
        if name == "lo":
            continue
        state = read(f"{path}/operstate")
        speed = read(f"{path}/speed")
        pci = ""
        try:
            dev = os.path.realpath(f"{path}/device")
            if "/" in dev and ":" in os.path.basename(dev):
                pci = os.path.basename(dev)
        except OSError:
            pass
        out.append({k: v for k, v in {
            "name": name,
            "state": state,
            # Only a positive number is a link speed; -1 means the kernel has no link to measure.
            "mbps": int(speed) if speed.lstrip("-").isdigit() and int(speed) > 0 else 0,
            "duplex": read(f"{path}/duplex"),
            "mtu": read(f"{path}/mtu"),
            "pci": pci,
            "virtual": "virtual" in os.path.realpath(path),
        }.items() if v not in ("", None)})
    return out


def failed_user_units():
    """Failed units in brad's user manager, as (names, None) — or ([], why) when it could not look.

    Most of Moses runs as user services: the MCP server, the listener, the liveness probe, the weekly
    self-tests. The check above asks the SYSTEM manager only, so from 2026-08-13 a red handoff-gate
    self-test would have sat in the journal with nothing on the dashboard. Found 2026-09-13.

    Not sh(): it returns "" on any error, and "" here reads as "nothing failed". A query that could
    not reach the user bus must say so, not pass as clean.
    """
    try:
        r = subprocess.run("systemctl --user list-units --state=failed --no-legend --plain",
                           shell=True, capture_output=True, text=True, timeout=8)
    except Exception as e:  # noqa: BLE001 — a timeout and a missing binary both mean "could not look"
        return [], f"systemctl --user did not run ({type(e).__name__})"
    if r.returncode != 0:
        return [], (r.stderr.strip().splitlines() or ["no user bus"])[0][:80]
    return [ln.split()[0] for ln in r.stdout.splitlines() if ln.strip()], None


def health_facts():
    """Things that are wrong or pending, and nothing else.

    Deliberately narrow. Every entry here has to be something a person would act on — a page that
    reports twenty green ticks trains you to stop reading it, which is how the one red line gets
    missed.
    """
    out = {}
    failed = sh("systemctl list-units --state=failed --no-legend --plain 2>/dev/null")
    names = [ln.split()[0] for ln in failed.splitlines() if ln.strip()]
    user_names, why = failed_user_units()
    names += [f"{n} (user)" for n in user_names]
    if names:
        out["failed_units"] = names
    if why:
        out["unchecked"] = [f"user units: {why}"]
    if os.path.exists("/var/run/reboot-required"):
        pkgs = read("/var/run/reboot-required.pkgs").splitlines()
        out["reboot_required"] = True
        if pkgs:
            out["reboot_for"] = sorted(set(pkgs))[:4]
    # A filesystem that went read-only is a disk problem wearing a mount option.
    ro = [ln.split()[1] for ln in read("/proc/mounts").splitlines()
          if len(ln.split()) > 3 and ln.split()[3].startswith("ro,")
          and not ln.split()[1].startswith(("/sys", "/proc", "/run", "/snap"))]
    if ro:
        out["readonly_mounts"] = ro
    return out


def disk_facts():
    """Physical disks. -e7,1 drops loop and ramdisk devices, which are not hardware."""
    disks = []
    for ln in sh("lsblk -dno NAME,MODEL,SIZE,ROTA,TRAN -e7,1").splitlines():
        f = ln.split()
        if len(f) < 3:
            continue
        name, rest = f[0], f[1:]
        tran = rest[-1] if rest and not rest[-1][0].isdigit() else ""
        rota = ""
        for i, tok in enumerate(rest):
            if tok in ("0", "1") and i > 0:
                rota = tok
        model = " ".join(rest[:max(0, len(rest) - (2 if rota else 1) - (1 if tran else 0))]).strip()
        size = next((t for t in rest if re.match(r"^[\d.]+[KMGTP]$", t)), "")
        disks.append({"name": name, "model": model or "—", "size": size,
                      # rota=0 means no spinning platter — true of an SSD and of the Pi's SD card
                      # alike, so it says what was measured rather than guessing which one it is.
                      "kind": ("solid-state" if rota == "0" else "spinning" if rota == "1" else ""),
                      "bus": tran})
    return disks


def memory_facts():
    """Installed DIMMs, from SMBIOS. The one field on this page that needs privilege.

    Reached through `sudo -n dmi-memory`, a wrapper pinned to `dmidecode -t 17` — Memory Device
    tables only, no arguments accepted. If the wrapper is not installed, or sudo declines, this
    returns nothing and the page simply omits the section: the check is `sudo -n`, which fails
    immediately rather than sitting on a password prompt inside a timed collector.

    Everything else here comes from /proc and /sys. EDAC exposes no memory controllers on this
    hardware and unprivileged `lshw` reports only a total, both verified — so this is not a
    convenience, it is the only route to type and speed.
    """
    raw = sh("sudo -n dmi-memory 2>/dev/null", timeout=10)
    if not raw:
        return {}
    slots, filled = [], 0
    # Parsed as blocks of "Key: Value" rather than by matching an expected layout, because the exact
    # shape of dmidecode output varies by version and by board and this must not be brittle about it.
    for block in re.split(r"\n(?=Handle )", raw):
        if "Memory Device" not in block:
            continue
        f = dict(re.findall(r"^\s*([A-Za-z ]+):\s*(.+?)\s*$", block, re.M))
        size = f.get("Size", "")
        locator = f.get("Locator", "") or f.get("Bank Locator", "")
        if not size or "No Module" in size:
            slots.append({"slot": locator, "empty": True})
            continue
        filled += 1
        slots.append({k: v for k, v in {
            "slot": locator,
            "size": size,
            "type": f.get("Type", ""),
            # Configured speed is what it RUNS at; the other is what the part is rated for. They
            # differ often enough that showing only one is misleading.
            "speed": f.get("Configured Memory Speed", "") or f.get("Speed", ""),
            "rated": f.get("Speed", ""),
            "maker": f.get("Manufacturer", ""),
            "part": f.get("Part Number", ""),
        }.items() if v and v.lower() not in ("unknown", "not specified", "none")})
    if not slots:
        return {}
    return {"slots": slots, "filled": filled, "total_slots": len(slots)}


def update_facts():
    """Pending package updates, and whether anything is handling them.

    THE COUNT ALONE IS NOISE. 53 pending means nothing without two other facts: how many are
    SECURITY, and whether the machine installs those by itself. On Reserve the answer is "none are
    security, and unattended-upgrades installed 54 packages this morning" — which is reassurance, not
    a chore. On Labs it is "no unattended-upgrades installed at all", which is the opposite.
    ALSO REPORTS ITS OWN STALENESS. `apt list --upgradable` reads a local cache, so a number from a
    three-week-old cache is a number about three weeks ago. Labs' lists had not refreshed since
    2026-08-09 when this was written, which makes its count a floor rather than a total.
    """
    if not os.path.exists("/usr/bin/apt"):
        return {}
    raw = sh("apt list --upgradable 2>/dev/null", timeout=25)
    pkgs, security = [], 0
    for ln in raw.splitlines():
        if "/" not in ln or ln.startswith("Listing"):
            continue
        name = ln.split("/", 1)[0]
        origin = ln.split("/", 1)[1].split()[0] if "/" in ln else ""
        pkgs.append(name)
        if "security" in origin:
            security += 1
    out = {"count": len(pkgs), "security": security, "packages": sorted(pkgs)}

    # How old is the cache the count came from?
    for stamp in ("/var/lib/apt/periodic/update-success-stamp", "/var/lib/apt/lists"):
        try:
            out["lists_age_days"] = int((time.time() - os.path.getmtime(stamp)) / 86400)
            break
        except OSError:
            continue

    # Is anything installing them? Three distinct answers, not two.
    if not sh("dpkg -l unattended-upgrades 2>/dev/null | grep -c '^ii'").strip("0 \n"):
        out["auto"] = "not installed"
    elif sh("systemctl is-enabled unattended-upgrades 2>/dev/null") == "enabled":
        out["auto"] = "installs security automatically"
        last = ""
        for ln in reversed(read("/var/log/unattended-upgrades/unattended-upgrades.log").splitlines()):
            if "All upgrades installed" in ln or "No packages found" in ln:
                last = ln.split(",")[0]
                break
        if last:
            out["auto_last"] = last
    else:
        out["auto"] = "installed but not enabled"
    return out


def os_facts():
    return {k: v for k, v in {
        "os": first(r'^PRETTY_NAME="?([^"\n]+)"?$', read("/etc/os-release")),
        "kernel": os.uname().release,
    }.items() if v}


# Guarded so hwprobe_test.py can import the functions without running a full probe; moses-hardware
# runs this file as a script, so its output is unchanged.
if __name__ == "__main__":
    print(json.dumps({"cpu": cpu_facts(), "board": board_facts(), "pci": pci_facts(),
                      "disks": disk_facts(), "memory": memory_facts(),
                      "net": net_facts(), "health": health_facts(),
                      "updates": update_facts(), **os_facts()}))
