"""Viatica's incident queue, for Moses.

Brad, 2026-08-23: *"I absolutely need Moses and Knight to be able to see and resolve incidents if
tasked."*

The queue is what the product noticed going wrong for real people, as opposed to what somebody wrote
in about. Reading it needs nothing new — the app has a machine door at /api/incidents/agent that
takes the shared secret Moses already holds.

TWO THINGS DELIBERATELY NOT DONE HERE:

  * No caching. The projects page caches state for two minutes because it is a dashboard nobody acts
    on. This is read in order to DO something, and a stale queue means closing an incident that has
    fired forty more times since, or reporting one that a person already handled.

  * Nothing decides on its own. Closing is a separate call that has to be asked for, and the name
    goes on the record. See [[project_btp_incident_capture]].
"""
from __future__ import annotations
import json, os, urllib.error, urllib.request

CONF = os.path.expanduser("~/.config/moses/customs.env")

# REQUIRED, not cosmetic. Cloudflare's managed bot rules sit in front of viatica.travel and answer
# Python's default "Python-urllib/3.x" with a 403 — which reads exactly like an auth failure but
# never reaches Viatica's code, because ours returns 401. Naming ourselves gets through and makes the
# caller identifiable in Cloudflare's logs.
UA = "Moses/1.0 (+viatica-ops)"


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


def _call(path: str, method: str = "GET", body: dict | None = None, timeout: float = 15.0):
    """Returns (data, error). Never raises — a broken call must be reportable, not fatal."""
    c = _conf()
    base, secret = c.get("VIATICA_APP_URL", "").rstrip("/"), c.get("CRON_SECRET", "")
    missing = [n for n, v in (("VIATICA_APP_URL", base), ("CRON_SECRET", secret)) if not v]
    if missing:
        return None, f"not configured: {', '.join(missing)} missing from {CONF}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{base}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {secret}", "User-Agent": UA,
                 **({"Content-Type": "application/json"} if data else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}"), None
    except urllib.error.HTTPError as e:
        # SAY WHAT THE APP SAID. A bare "HTTP 400" sends whoever is reading this to the logs; the
        # body already explains that the id was missing or the incident does not exist.
        detail = ""
        try:
            detail = (json.loads(e.read().decode() or "{}") or {}).get("error", "")
        except Exception:
            pass
        return None, f"HTTP {e.code}{': ' + detail if detail else ''}"
    except Exception as e:                                        # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def _ago(iso: str) -> str:
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:                                             # noqa: BLE001
        return "?"
    secs = (datetime.now(timezone.utc) - t).total_seconds()
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def queue(state: str = "open", limit: int = 20) -> str:
    """The incident queue as a person would read it, ids included so it can be acted on."""
    q = f"?limit={int(limit)}" + ("&state=all" if state == "all" else "")
    data, err = _call(f"/api/incidents/agent{q}")
    if err:
        # NEVER report a failed read as an empty queue. "Nothing is broken" and "I could not look"
        # are different facts, and collapsing them is how a silence gets mistaken for health.
        return f"COULD NOT READ THE INCIDENT QUEUE — {err}\nThis is not the same as 'no incidents'."

    rows = data.get("incidents") or []
    if not rows:
        return "No open incidents. (Read live from Viatica just now, not from memory.)"

    out = [f"{data.get('open', '?')} open incident(s) in Viatica:"]
    for i in rows:
        mark = "" if not i.get("resolvedAt") else f"  [CLOSED by {i.get('resolvedBy') or '?'}]"
        out.append(
            f"\n  {i['id']}{mark}\n"
            f"    {i.get('summary') or i.get('message')}\n"
            f"    {i.get('kind')} · {i.get('route')}"
            + (f" · HTTP {i['status']}" if i.get("status") else "")
            + f" · {i.get('count')}x · {i.get('usersAffected')} person(s)"
            f" · first {_ago(i.get('firstSeenAt', ''))}, last {_ago(i.get('lastSeenAt', ''))}"
        )
        if i.get("sampleContext"):
            out.append(f"    context: {str(i['sampleContext'])[:300]}")
    out.append("\nTo close one: resolve_incident(id, note='what was found or done').")
    return "\n".join(out)


def resolve(incident_id: str, note: str = "", by: str = "moses", reopen: bool = False) -> str:
    """Mark one incident investigated, or reopen it. The name goes on the record."""
    incident_id = (incident_id or "").strip()
    if not incident_id:
        return "Which incident? Pass the id shown by the incident queue."
    data, err = _call(
        "/api/incidents/agent", method="POST",
        body={"id": incident_id, "resolved": not reopen, "by": by, "note": note or ""},
    )
    if err:
        return f"Could not change incident {incident_id} — {err}"
    inc = (data or {}).get("incident") or {}
    if reopen:
        return f"Reopened {incident_id}. It is back in the open queue."
    return (
        f"Closed {incident_id} as investigated, signed {inc.get('resolvedBy') or by}."
        + (f"\nNote recorded: {inc.get('note')}" if inc.get("note") else "")
        + "\nIt reopens by itself if anyone hits the same bug again — a bug that comes back is a new"
          " problem, not a closed one."
    )
