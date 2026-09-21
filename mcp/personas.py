"""personas — Big Pipe and Tagilla, answering on demand from Reserve.

Their data lives on Customs (Railway/Postgres), not here, so these tools are thin clients over the
read-only `/api/agents/report` endpoint. That endpoint posts nothing: asking Moses for the P&L from
a phone must never fire a message at #finance, or a message in the channel stops meaning "the
scheduled report ran" — which is the only reason the morning reports are worth reading.

The scheduled Slack reports are untouched and still come from `/api/cron/*` on their own timers.
This is the same numbers by a different door, computed by the same shared modules so the on-demand
answer and the morning report cannot disagree.

FAILURE IS LOUD, ALWAYS. Every error path here returns the reason, never a blank or a zero. These
are financial and backlog figures: a plausible-looking wrong number is far worse than an error, and
"$0.00 net" reads like a fact when it actually means the request 401'd.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 25

# CLOUDFLARE BLOCKS `Python-urllib` BY NAME, and urllib sends it unless told otherwise.
#
# Measured 2026-08-26 against the live site: the identical request — same URL, same bearer token,
# same Accept — answers 200 with a curl, browser or custom User-Agent and 403 with the default one.
# It is the UA string alone.
#
# This started when the domain moved to viatica.travel on 20 August and put these calls behind
# Cloudflare; the old Railway host is not behind it. So Big Pipe and Tagilla had both been answering
# "Customs answered HTTP 403" to every on-demand question for six days — including every question
# about money — while their SCHEDULED reports stayed green, because those go through curl by a
# different route. Nothing exercised this path, so nothing noticed.
#
# Identify honestly rather than pretending to be a browser: a real name is what we would want to see
# in our own logs, and if it is ever blocked deliberately that should be a decision someone can make.
USER_AGENT = "Moses-MCP/1.0 (+https://viatica.travel; Reserve)"


class CustomsError(RuntimeError):
    pass


def _config() -> tuple[str, str]:
    base = (os.environ.get("VIATICA_APP_URL") or "").strip().rstrip("/")
    secret = (os.environ.get("CRON_SECRET") or "").strip()
    if not base or not secret:
        missing = [n for n, v in (("VIATICA_APP_URL", base), ("CRON_SECRET", secret)) if not v]
        raise CustomsError(
            f"{' and '.join(missing)} not set for this service — "
            f"Big Pipe and Tagilla cannot reach Customs. Check the EnvironmentFile on moses-mcp."
        )
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    return base, secret


def fetch(persona: str, **params) -> dict:
    base, secret = _config()
    qs = urllib.parse.urlencode({"persona": persona, **{k: v for k, v in params.items() if v is not None}})
    req = urllib.request.Request(
        f"{base}/api/agents/report?{qs}",
        headers={"Authorization": f"Bearer {secret}", "Accept": "application/json",
                 "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = json.loads(e.read().decode("utf-8")).get("error", "")
        except Exception:                                    # noqa: BLE001 — error body may be HTML
            pass
        if e.code == 401:
            raise CustomsError("Customs rejected the credential (401) — CRON_SECRET on Reserve does "
                               "not match the one on Railway.") from e
        if e.code == 503:
            raise CustomsError("Customs has no CRON_SECRET configured (503).") from e
        # Named because it cost six days: the credential is fine and the app never saw the request.
        if e.code == 403:
            raise CustomsError("Cloudflare refused the request (403) before Customs saw it — the "
                               "credential is not the problem. Check the User-Agent this client "
                               "sends; the default Python one is on its bot list.") from e
        raise CustomsError(f"Customs answered HTTP {e.code}{': ' + body if body else ''}") from e
    except urllib.error.URLError as e:
        raise CustomsError(f"Could not reach Customs: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise CustomsError(f"Customs returned something that is not JSON: {e}") from e


def cfo(period: str = "ytd") -> str:
    d = fetch("cfo", period=period)
    out = ["*Big Pipe* — Viatica finance"]

    if d.get("period") == "ytd":
        out.append(f"YTD {d.get('year')} as of {d.get('asOf')}")
        out.append(f"   income          {d.get('income')}")
        out.append(f"   Stripe fees     {d.get('stripeFees')}")
        out.append(f"   recurring costs {d.get('costs')}")
        out.append(f"   ─ net           {d.get('net')}")
        lines = d.get("costLines") or []
        if lines:
            out.append("\n   cost lines (YTD):")
            for c in lines:
                out.append(f"     • {c.get('label')} — {c.get('ytd')} ({c.get('provider')}, {c.get('cadence')})")
    else:
        w = d.get("window") or {}
        out.append(f"{d.get('period')} — {w.get('label')}")
        out.append(f"   income {d.get('periodIncome')} · fees {d.get('periodFees')} · net {d.get('periodNet')}")
        out.append(f"   YTD: income {d.get('ytdIncome')} · expenses {d.get('ytdExpenses')} · net {d.get('ytdNet')}")
        out.append(f"   tax set-aside (rough, ~30% of YTD net): {d.get('taxReserve')}")

    # Never let an incomplete P&L pass as a complete one. If Stripe did not answer, the income line
    # is missing revenue and every figure derived from it is wrong in the same direction.
    if d.get("stripeAvailable") is False or d.get("ytdStripeAvailable") is False:
        out.append("\n⚠️  Stripe did not answer, so income is INCOMPLETE and net is understated. "
                   "Do not quote these figures until it is back.")
    return "\n".join(out)


def support(hours: int = 24) -> str:
    d = fetch("support", hours=hours)
    out = [f"*Tagilla* — support, last {d.get('windowHours')}h"]
    received = d.get("received", 0)
    if received == 0:
        out.append("   nothing came in — queue is quiet")
    else:
        out.append(f"   {received} in — {d.get('questions')} question(s), {d.get('feedback')} feedback")
        out.append(f"   answered {d.get('answered')}, routed {d.get('routed')} to Brad"
                   + (f" ({d.get('lowConfidence')} low-confidence)" if d.get("lowConfidence") else ""))

    open_n = d.get("open", 0)
    if open_n:
        out.append(f"\n   {open_n} still open — oldest first:")
        for t in d.get("oldestOpen") or []:
            out.append(f"     • {t.get('subject')} — {t.get('ageHours')}h old")
        shown = len(d.get("oldestOpen") or [])
        if open_n > shown:
            out.append(f"     • …and {open_n - shown} more")
    elif received:
        out.append("\n   nothing waiting on Brad")
    return "\n".join(out)
