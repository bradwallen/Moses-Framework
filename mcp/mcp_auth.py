"""mcp_auth — who may call the Moses MCP server, checked on every request before the SDK sees it.

Two ways in, and nothing else (2026-11-06, after both listeners were found answering anyone):

  1. A bearer key (agent/lib/mcp_key.py): `Authorization: Bearer <key>`. The Slack listener and the
     operator's own jobs use this.

  2. A Cloudflare Access token: the `Cf-Access-Jwt-Assertion` header Access adds to a request it let
     through. It is how the claude.ai connector arrives (portal -> moses-origin tunnel -> 127.0.0.1:8766).
     Checked the way Cloudflare's docs say an origin must: RS256 signature against the team's published
     keys, the `aud` of our Access application, the team `iss`, and expiry.

WHICH LISTENER TAKES WHICH is the AUTH MODE, chosen from the address it listens on, never from the tool
profile (review 2026-11-06: the public unit runs profile=full, so keying the mode off the profile would
have refused every portal request). A loopback listener is the tunnel's origin: `access` (key or Access
token). Anything else (the tailnet address): `key` only. MOSES_MCP_AUTH=key|access overrides, and
`access` on a non-loopback address is refused at start.

REPORT MODE, for `access` only, until real portal traffic proves the aud: a request that carries an
Access token which fails the check is let through and counted as `would-refuse`. A request with NO key
and NO token is refused even in report mode: the portal always sends a token, so that is a local process
going around Access, which is exactly the hole being closed. Once mcp-auth-<port>.json shows portal
requests passing, ENFORCE becomes the default (`ACCESS_ENFORCE_DEFAULT`).

Every decision is counted in <state>/mcp-auth-<port>.json, rewritten whole on every request (a merged
file can never drop a retired key). It records outcomes and reasons only, never a key or a token.
"""
from __future__ import annotations

import ipaddress
import json
import os
import threading
import time
from pathlib import Path

# ENFORCED since 2026-11-06 10:3x: the first portal requests through the deployed gate (10:22) carried
# valid Access tokens with moses-origin's aud (mcp-auth-8766.json), which is what report mode waited for.
# MOSES_MCP_ACCESS_ENFORCE=0 puts report mode back if a connector ever needs diagnosing.
ACCESS_ENFORCE_DEFAULT = True
REFETCH_EVERY = 60          # at most one fetch of the team's keys per minute for an unknown kid


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def auth_mode(host: str, override: str = "") -> str:
    """'key' or 'access'. Raises ValueError for an override this address may not have."""
    mode = (override or "").strip().lower() or ("access" if is_loopback(host) else "key")
    if mode not in ("key", "access"):
        raise ValueError(f"MOSES_MCP_AUTH={override!r}: use key or access")
    if mode == "access" and not is_loopback(host):
        raise ValueError(f"MOSES_MCP_AUTH=access on {host}: only a loopback listener (the tunnel's origin) "
                         "may accept Access tokens; anything else must take a key")
    return mode


def parse_enforce(flag: str, default: bool = ACCESS_ENFORCE_DEFAULT) -> bool:
    """'' -> the default. Anything else must say yes or no in a form we know; a typo is not 'off'."""
    v = (flag or "").strip().lower()
    if v == "":
        return default
    if v in ("1", "true", "yes", "on", "enforce"):
        return True
    if v in ("0", "false", "no", "off", "report"):
        return False
    raise ValueError(f"MOSES_MCP_ACCESS_ENFORCE={flag!r}: use 1 or 0")


def load_access_config(path: Path) -> dict | None:
    """{"team_domain": "<team>.cloudflareaccess.com", "aud": ["<tag>", ...]} or None when absent/unusable."""
    try:
        cfg = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    team = str(cfg.get("team_domain", "")).strip()
    aud = [a for a in cfg.get("aud", []) if isinstance(a, str) and a.strip()]
    if not team or not aud:
        return None
    return {"team_domain": team, "aud": aud}


class ThrottledJWKS:
    """The team's signing keys by kid. An unknown kid refetches at most once per REFETCH_EVERY, so a
    stream of made-up kids cannot turn every request into a network fetch (review 2026-11-06)."""

    def __init__(self, url: str, fetch=None, clock=time.monotonic):
        import jwt
        self._jwt, self.url, self.clock = jwt, url, clock
        self._fetch = fetch or self._http
        self._keys: dict[str, object] = {}
        self._last = float("-inf")
        self._lock = threading.Lock()

    def _http(self) -> dict:
        import urllib.request
        # Cloudflare refuses Python's default urllib User-Agent (gotcha-cloudflare-blocks-python-urllib).
        req = urllib.request.Request(self.url, headers={"User-Agent": "moses-mcp"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    def _refresh(self) -> None:
        jwks = self._jwt.PyJWKSet.from_dict(self._fetch())
        self._keys = {k.key_id: k.key for k in jwks.keys if k.key_id}

    def get_signing_key_from_jwt(self, token: str):
        kid = self._jwt.get_unverified_header(token).get("kid")
        with self._lock:
            if kid not in self._keys and self.clock() - self._last >= REFETCH_EVERY:
                self._last = self.clock()
                try:
                    self._refresh()
                except Exception as e:                   # noqa: BLE001
                    raise self._jwt.PyJWKClientError(f"could not fetch the team's keys: {type(e).__name__}")
            key = self._keys.get(kid)
        if key is None:
            raise self._jwt.PyJWKClientError("no signing key with that kid")
        return type("SigningKey", (), {"key": key})()


class AccessVerifier:
    """Checks a Cf-Access-Jwt-Assertion. `jwks_client` is injectable so the tests can sign their own."""

    def __init__(self, team_domain: str, audiences: list[str], jwks_client=None):
        import jwt
        self._jwt = jwt
        self.issuer = f"https://{team_domain}"
        self.audiences = list(audiences)
        self.jwks = jwks_client or ThrottledJWKS(f"{self.issuer}/cdn-cgi/access/certs")

    def check(self, token: str) -> tuple[bool, str, str]:
        """(ok, reason, aud-or-''). Never raises: a failure to check is a refusal with its reason."""
        jwt = self._jwt
        try:
            key = self.jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.audiences,
                                issuer=self.issuer, options={"require": ["exp", "iat", "aud", "iss"]})
        except jwt.ExpiredSignatureError:
            return False, "access token expired", ""
        except jwt.InvalidAudienceError:
            return False, "access token for another application", ""
        except jwt.InvalidIssuerError:
            return False, "access token from another team", ""
        except jwt.PyJWKClientError as e:
            return False, f"access keys unavailable or unknown kid ({e})", ""
        except jwt.InvalidTokenError as e:
            return False, f"access token invalid ({type(e).__name__})", ""
        except Exception as e:                       # never let a checker bug become an open door
            return False, f"access check failed ({type(e).__name__})", ""
        aud = claims.get("aud")
        return True, "access token valid", aud if isinstance(aud, str) else ",".join(aud or [])


class Status:
    """Counts per outcome, written whole to one JSON file. Best effort: a write failure never blocks."""

    def __init__(self, path: Path | None, port: int, mode: str):
        self.path = path
        self.doc = {"port": port, "mode": mode, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "pid": os.getpid(), "counts": {}, "last": {}}

    def record(self, outcome: str, reason: str, extra: dict | None = None) -> None:
        c = self.doc["counts"]
        c[outcome] = c.get(outcome, 0) + 1
        self.doc["last"][outcome] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "reason": reason,
                                     **(extra or {})}
        if not self.path:
            return
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.doc, indent=1))
            os.replace(tmp, self.path)
        except OSError:
            pass


def _header(scope, name: bytes) -> str:
    for k, v in scope.get("headers") or []:
        if k.lower() == name:
            return v.decode("latin-1")
    return ""


class Gate:
    """ASGI middleware.

    mode 'key':    only a bearer key gets in.
    mode 'access': a key, or a valid Access token. With enforce_access False (report mode) a token that
                   FAILS the check is let through and counted as `would-refuse`; no key and no token is
                   always refused.
    keys: a dict, or a callable returning one (re-read live, so a deleted key file is a revocation)."""

    def __init__(self, app, *, keys, mode: str, verifier: AccessVerifier | None,
                 enforce_access: bool, status: Status, log=print):
        assert mode in ("key", "access")
        self.app, self._keys, self.mode = app, keys, mode
        self.verifier, self.enforce_access, self.status, self.log = verifier, enforce_access, status, log

    def keys(self) -> dict:
        return self._keys() if callable(self._keys) else self._keys

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind == "lifespan":
            return await self.app(scope, receive, send)
        if kind != "http":                           # websocket or anything new: not ours to pass
            self.status.record("refused", f"{kind} scope")
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        ok, outcome, reason, extra = await self._decide(scope)
        self.status.record(outcome, reason, extra)
        if outcome != "key":                         # key passes are the listener's every turn: too chatty
            self.log(f"mcp-auth: {outcome}: {reason}" + (f" aud={extra['aud']}" if extra.get("aud") else ""),
                     flush=True)
        if ok:
            return await self.app(scope, receive, send)
        body = json.dumps({"error": "unauthorized", "reason": reason}).encode()
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                                (b"www-authenticate", b'Bearer realm="moses"')]})
        await send({"type": "http.response.body", "body": body})

    async def _decide(self, scope) -> tuple[bool, str, str, dict]:
        from mcp_key import matches
        auth = _header(scope, b"authorization")
        if auth[:7].lower() == "bearer ":
            who = matches(auth[7:].strip(), self.keys())
            if who:
                return True, "key", f"key:{who}", {"who": who}
            if self.mode == "key":
                return False, "refused", "wrong key", {}
            # On the access listener a bearer that is not ours may be the portal's own credential passed
            # along; it proves nothing either way, so the Access check below decides.
        elif self.mode == "key":
            return False, "refused", "no key", {}
        token = _header(scope, b"cf-access-jwt-assertion")
        if not token:
            return False, "refused", "no key and no Access token", {}
        if self.verifier is None:
            reason = "no Access configuration (mcp/access.conf)"
            return (False, "refused", reason, {}) if self.enforce_access else (True, "would-refuse", reason, {})
        import anyio
        good, reason, aud = await anyio.to_thread.run_sync(self.verifier.check, token)
        if good:
            return True, "access", reason, {"aud": aud}
        if self.enforce_access:
            return False, "refused", reason, {}
        return True, "would-refuse", reason, {}
