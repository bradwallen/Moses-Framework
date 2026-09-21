"""Pull Viatica's REAL state from the app, so the Projects page stops depending on my memory.

Brad, 2026-08-20: "You're also bad at updating the project status." Correct, and the answer is not
that I try harder — it is the same move that fixed the launch checklist and the Stripe readiness
page. STOP REMEMBERING, START ASKING. Anything the product can answer about itself is fetched;
what stays hand-written is only the part that is genuinely a decision (milestones, focus).

Never raises and never blocks the page: a dashboard that fails to render because a remote call timed
out is worse than one showing a slightly older number.
"""
from __future__ import annotations
import json, os, time, urllib.request

CONF = os.path.expanduser("~/.config/moses/customs.env")
CACHE = "/tmp/moses-viatica-state.json"
CACHE_TTL = 120  # seconds — the page is refreshed by humans, not polled


def _conf() -> dict:
    out = {}
    try:
        with open(CONF) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def fetch(timeout: float = 8.0) -> dict | None:
    """The live state, or None. Cached briefly so opening the page repeatedly is not a load test."""
    try:
        if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < CACHE_TTL:
            with open(CACHE) as fh:
                return json.load(fh)
    except Exception:
        pass

    c = _conf()
    base, secret = c.get("VIATICA_APP_URL", "").rstrip("/"), c.get("CRON_SECRET", "")
    if not base or not secret:
        return None
    try:
        # A User-Agent is REQUIRED, not cosmetic. Cloudflare's managed bot rules sit in front of
        # viatica.travel and answer Python's default "Python-urllib/3.x" with a 403 — which looks
        # exactly like an auth failure but never reaches our code (ours returns 401). Naming
        # ourselves both gets through and makes the caller identifiable in Cloudflare's logs.
        req = urllib.request.Request(
            f"{base}/api/state",
            headers={"Authorization": f"Bearer {secret}", "User-Agent": "Moses/1.0 (+viatica-ops)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        try:
            with open(CACHE, "w") as fh:
                json.dump(data, fh)
        except OSError:
            pass
        return data
    except Exception:
        return None


def summary() -> str | None:
    """One line for the Projects page, or None when the app could not be reached.

    Deliberately says WHEN it was measured. A number with no timestamp is indistinguishable from a
    number somebody typed in weeks ago, which is the exact failure this replaces.
    """
    s = fetch()
    if not s:
        return None
    bits = [f"v{s['app']['version']} live"]
    u = (s.get("usage") or {}).get("data") or {}
    if u:
        bits.append(f"{u.get('users', '?')} users · {u.get('trips', '?')} trips")
    real = (s.get("funnelReal") or {}).get("data") or {}
    if real:
        bits.append(f"{real.get('signups', '?')} signups / {real.get('converted', '?')} paid (real customers)")
    n = (s.get("notice") or {}).get("data") or {}
    if n.get("up"):
        bits.append(f"⚠ NOTICE UP: {n.get('kind')}")
    return " · ".join(bits)
