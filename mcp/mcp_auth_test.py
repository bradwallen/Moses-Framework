#!/usr/bin/env python3
"""Who may call the Moses MCP server (mcp_auth.py, agent/lib/mcp_key.py).

    mcp/venv/bin/python mcp/mcp_auth_test.py

Three layers. The gate itself, driven as ASGI with a stand-in app. The Access check, with tokens this
test signs with its own RSA key (a real JWKS lookup, served by an injected client). And the SEAM: the
real mcp_server.py started on a scratch port with scratch folders, called over HTTP the way the Slack
listener and the tunnel call it. Then each guard is broken on purpose and the suite must go red.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "agent" / "lib"))

import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

import mcp_auth  # noqa: E402
import mcp_key  # noqa: E402

FAILS: list[str] = []
PASSES = 0


def check(label: str, cond: bool) -> bool:
    global PASSES
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if cond:
        PASSES += 1
    else:
        FAILS.append(label)
    return cond


TMP = Path(tempfile.mkdtemp(prefix="moses-mcp-auth-"))
TEAM = "test-team.cloudflareaccess.com"
AUD = "a" * 64
KID = "test-kid"
PRIV = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJWKS:
    """Answers like PyJWKClient: the signing key for a known kid, an error for any other."""
    def get_signing_key_from_jwt(self, token):
        kid = jwt.get_unverified_header(token).get("kid")
        if kid != KID:
            raise jwt.PyJWKClientError("Unable to find a signing key that matches")
        return type("K", (), {"key": PRIV.public_key()})()


def token(*, key=PRIV, kid=KID, aud=AUD, iss=f"https://{TEAM}", exp=300, alg="RS256"):
    now = int(time.time())
    return jwt.encode({"aud": [aud], "iss": iss, "iat": now, "exp": now + exp, "sub": "svc"},
                      key, algorithm=alg, headers={"kid": kid})


VERIFIER = mcp_auth.AccessVerifier(TEAM, [AUD], jwks_client=FakeJWKS())
KEYS = {"listener": mcp_key.load_or_create("listener", TMP / "cfg", TMP / "state"),
        "operator": mcp_key.load_or_create("operator", TMP / "cfg", TMP / "state")}


def call(gate, headers: dict[str, str]) -> tuple[int, bool]:
    """(status, did the app run)."""
    ran = {"app": False}
    out = {"status": None}

    async def app(scope, receive, send):
        ran["app"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    gate.app = app
    scope = {"type": "http", "method": "POST", "path": "/mcp",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]

    asyncio.run(gate(scope, receive, send))
    return out["status"], ran["app"]


def gate(*, require_key: bool, enforce: bool, verifier=VERIFIER, status_path=None, keys=None):
    return mcp_auth.Gate(None, keys=keys or KEYS, mode="key" if require_key else "access", verifier=verifier,
                         enforce_access=enforce, status=mcp_auth.Status(status_path, 0, "test"),
                         log=lambda *a, **k: None)


def suite(tag: str = "") -> int:
    """The behavior, as checks. Returns how many failed, so a mutant run can count its reds."""
    before = len(FAILS)
    full = gate(require_key=True, enforce=True)
    bearer = {"Authorization": f"Bearer {KEYS['listener']}"}
    check(f"{tag}FULL no key is refused 401 and the app never runs", call(full, {}) == (401, False))
    check(f"{tag}FULL a wrong key is refused", call(full, {"Authorization": "Bearer " + "x" * 43})[0] == 401)
    check(f"{tag}FULL the listener key gets in", call(full, bearer) == (200, True))
    check(f"{tag}FULL the operator key gets in",
          call(full, {"Authorization": f"Bearer {KEYS['operator']}"}) == (200, True))
    check(f"{tag}FULL a valid Access token alone does NOT get in (key only)",
          call(full, {"Cf-Access-Jwt-Assertion": token()})[0] == 401)
    check(f"{tag}FULL the key as a non-Bearer scheme is refused",
          call(full, {"Authorization": f"Basic {KEYS['listener']}"})[0] == 401)

    pub = gate(require_key=False, enforce=True)
    check(f"{tag}PUBLIC enforced: a valid Access token gets in", call(pub, {"Cf-Access-Jwt-Assertion": token()}) == (200, True))
    check(f"{tag}PUBLIC enforced: a key gets in", call(pub, bearer) == (200, True))
    check(f"{tag}PUBLIC enforced: nothing at all is refused (a local process going around Access)",
          call(pub, {})[0] == 401)
    check(f"{tag}PUBLIC enforced: a token signed by another key is refused",
          call(pub, {"Cf-Access-Jwt-Assertion": token(key=OTHER)})[0] == 401)
    check(f"{tag}PUBLIC enforced: an unknown kid is refused",
          call(pub, {"Cf-Access-Jwt-Assertion": token(kid="nope")})[0] == 401)
    check(f"{tag}PUBLIC enforced: another application's token is refused",
          call(pub, {"Cf-Access-Jwt-Assertion": token(aud="b" * 64)})[0] == 401)
    check(f"{tag}PUBLIC enforced: another team's token is refused",
          call(pub, {"Cf-Access-Jwt-Assertion": token(iss="https://evil.cloudflareaccess.com")})[0] == 401)
    check(f"{tag}PUBLIC enforced: an expired token is refused",
          call(pub, {"Cf-Access-Jwt-Assertion": token(exp=-120)})[0] == 401)
    check(f"{tag}PUBLIC enforced: an unsigned (alg none) token is refused",
          call(pub, {"Cf-Access-Jwt-Assertion": token(key=None, alg="none")})[0] == 401)
    check(f"{tag}PUBLIC enforced: a foreign bearer does not block a valid token (the portal's own credential)",
          call(pub, {"Authorization": "Bearer portal-oauth", "Cf-Access-Jwt-Assertion": token()}) == (200, True))
    check(f"{tag}PUBLIC enforced: no access.conf refuses everything but a key",
          call(gate(require_key=False, enforce=True, verifier=None), {"Cf-Access-Jwt-Assertion": token()})[0] == 401)

    sp = TMP / f"status{len(FAILS)}.json"
    rep = gate(require_key=False, enforce=False, status_path=sp)
    check(f"{tag}PUBLIC report mode: NO key and NO token is still refused (a local process, not the portal)",
          call(rep, {})[0] == 401)
    check(f"{tag}PUBLIC report mode: a token that fails is let through (the aud question)",
          call(rep, {"Cf-Access-Jwt-Assertion": token(aud="b" * 64)}) == (200, True))
    call(rep, {"Cf-Access-Jwt-Assertion": token(exp=-120)})
    call(rep, {"Cf-Access-Jwt-Assertion": token()})
    doc = json.loads(sp.read_text()) if sp.exists() else {}
    check(f"{tag}PUBLIC report mode: what it would have refused is counted",
          doc.get("counts", {}).get("would-refuse") == 2 and doc.get("counts", {}).get("access") == 1
          and doc.get("counts", {}).get("refused") == 1)
    check(f"{tag}PUBLIC report mode: the status file names the aud that passed",
          doc.get("last", {}).get("access", {}).get("aud") == AUD)
    check(f"{tag}STATUS never holds a key or a token", sp.exists() and all(
        k not in sp.read_text() for k in KEYS.values()) and "eyJ" not in sp.read_text())
    live = dict(KEYS)
    lg = gate(require_key=True, enforce=True, keys=lambda: live)
    check(f"{tag}KEYS live: a key works while its file is there", call(lg, bearer) == (200, True))
    live["listener"] = "r" * 43
    check(f"{tag}KEYS live: once replaced (revoked), the old key is refused", call(lg, bearer)[0] == 401)

    ws = {"status": None}
    async def _ws():
        async def app(*a):
            ws["status"] = "app ran"
        g = gate(require_key=False, enforce=False); g.app = app
        sent = []
        async def send(m): sent.append(m)
        async def recv(): return {"type": "websocket.connect"}
        await g({"type": "websocket", "path": "/mcp", "headers": []}, recv, send)
        return sent
    sent = asyncio.run(_ws())
    check(f"{tag}a websocket is closed and never reaches the app",
          ws["status"] is None and sent and sent[0].get("type") == "websocket.close")
    return len(FAILS) - before


print("── the auth mode follows the address, never the tool profile ──")
check("MODE the tailnet address takes a key only", mcp_auth.auth_mode("100.64.0.1") == "key")
check("MODE loopback (the tunnel's origin) takes a key or an Access token", mcp_auth.auth_mode("127.0.0.1") == "access")
check("MODE ::1 is loopback too", mcp_auth.auth_mode("::1") == "access")
check("MODE an explicit key on loopback is allowed", mcp_auth.auth_mode("127.0.0.1", "key") == "key")
for bad in [("100.64.0.1", "access"), ("0.0.0.0", "access"), ("127.0.0.1", "open")]:
    try:
        mcp_auth.auth_mode(*bad); check(f"MODE {bad[1]} on {bad[0]} is refused at start", False)
    except ValueError:
        check(f"MODE {bad[1]} on {bad[0]} is refused at start", True)
check("ENFORCE '' is the default, and the default is ENFORCED since 2026-11-06",
      mcp_auth.parse_enforce("") is True and mcp_auth.ACCESS_ENFORCE_DEFAULT is True)
check("ENFORCE '0' puts report mode back for diagnosis", mcp_auth.parse_enforce("0") is False)
check("ENFORCE 'true' and 'yes' mean on", mcp_auth.parse_enforce("true") and mcp_auth.parse_enforce("yes"))
try:
    mcp_auth.parse_enforce("ture"); check("ENFORCE a typo is an error, not silently off", False)
except ValueError:
    check("ENFORCE a typo is an error, not silently off", True)

print("── unknown kids cannot turn every request into a fetch ──")
fetches = {"n": 0}
clock = {"t": 1000.0}
pub_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(PRIV.public_key()))
pub_jwk["kid"] = KID


def _fetch():
    fetches["n"] += 1
    return {"keys": [pub_jwk]}


tj = mcp_auth.ThrottledJWKS("https://x/cdn-cgi/access/certs", fetch=_fetch, clock=lambda: clock["t"])
tv = mcp_auth.AccessVerifier(TEAM, [AUD], jwks_client=tj)
check("JWKS a real token verifies through the throttled key set", tv.check(token())[0])
for _ in range(20):
    tv.check(token(kid="made-up"))
check("JWKS twenty made-up kids in a minute cost at most one more fetch", fetches["n"] <= 2)
clock["t"] += 61
tv.check(token(kid="made-up-later"))
check("JWKS after a minute an unknown kid may fetch again (a real key rotation is picked up)", fetches["n"] <= 3)
check("JWKS the known key still verifies without a fetch", tv.check(token())[0])

print("── two processes making the key at once agree on one ──")
race = TMP / "race"
RACER = ("import sys; sys.path.insert(0, sys.argv[1]); import mcp_key, pathlib; "
         "r = pathlib.Path(sys.argv[2]); print(mcp_key.load_or_create('listener', r / 'cfg', r / 'state'))")
ok_rounds = 0
for _round in range(5):
    kf = race / "cfg" / "mcp-keys" / "listener.key"
    if kf.exists():
        kf.unlink()
    ps = [subprocess.Popen([sys.executable, "-c", RACER, str(HERE.parent / "agent" / "lib"), str(race)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(6)]
    outs = [p.communicate(timeout=30) for p in ps]
    got = {o.strip() for o, _ in outs}
    if len(got) == 1 and all(p.returncode == 0 for p in ps) and kf.read_text().strip() in got:
        ok_rounds += 1
check(f"KEYS six processes racing to make the key, five rounds: all succeed and agree ({ok_rounds}/5)",
      ok_rounds == 5)


print("── the gate ──")
suite()

print("── keys ──")
lk = TMP / "cfg" / "mcp-keys" / "listener.key"
ok_ = TMP / "state" / "mcp-operator.key"
check("KEYS listener key is mode 600", (lk.stat().st_mode & 0o777) == 0o600)
check("KEYS operator key is mode 640 (the operator reads it through moses-share)", (ok_.stat().st_mode & 0o777) == 0o640)
check("KEYS the two keys differ", KEYS["listener"] != KEYS["operator"])
check("KEYS a second start reads the same key, it does not make a new one",
      mcp_key.load_or_create("listener", TMP / "cfg", TMP / "state") == KEYS["listener"])
(TMP / "s2").mkdir()
(TMP / "s2" / "mcp-operator.key").symlink_to(lk)
check("KEYS a key file that is a link is not read (someone pointing it at another key)",
      mcp_key.load("operator", TMP / "cfg", TMP / "s2") is None)
try:
    mcp_key.load_or_create("operator", TMP / "cfg", TMP / "s2")
    check("KEYS a link where a key should be is refused, not overwritten", False)
except RuntimeError:
    check("KEYS a link where a key should be is refused, not overwritten", lk.read_text().strip() == KEYS["listener"])


print("── the seam: the real server over HTTP ──")


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


INIT = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}).encode()


def post(port: int, headers: dict[str, str]) -> int:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", data=INIT, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream", **headers})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1


def serve(profile: str, extra: dict[str, str] | None = None):
    port = free_port()
    d = TMP / f"srv-{profile}-{port}"
    (d / "cfg").mkdir(parents=True); (d / "state").mkdir(); (d / "mem").mkdir()
    env = {**os.environ, "MOSES_HOME": str(d), "MOSES_CONFIG_DIR": str(d / "cfg"), "MOSES_STATE": str(d / "state"),
           "MOSES_MEMORY_DIR": str(d / "mem"), "MOSES_SETTINGS": str(d / "cfg" / "none.env"),
           "MOSES_MCP_HOST": "127.0.0.1", "MOSES_MCP_PORT": str(port), "MOSES_MCP_PROFILE": profile,
           "MOSES_MCP_ACCESS_CONF": str(d / "access.conf"), **(extra or {})}
    (d / "access.conf").write_text(json.dumps({"team_domain": TEAM, "aud": [AUD]}))
    p = subprocess.Popen([sys.executable, str(HERE / "mcp_server.py")], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(100):                    # ready = the port accepts; no request, so the counts stay clean
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    return p, port, d


p, port, d = serve("full", {"MOSES_MCP_AUTH": "key"})       # stands in for the tailnet listener
try:
    key = (d / "cfg" / "mcp-keys" / "listener.key").read_text().strip()
    check("SEAM full: no key is refused 401", post(port, {}) == 401)
    check("SEAM full: the listener key it made on start gets in", post(port, {"Authorization": f"Bearer {key}"}) == 200)
    check("SEAM full: a valid-looking Access header does not get in", post(port, {"Cf-Access-Jwt-Assertion": token()}) == 401)
    st = json.loads((d / "state" / f"mcp-auth-{port}.json").read_text())
    check("SEAM full: the status file counts the refusals", st.get("counts", {}).get("refused") == 2)
finally:
    p.terminate(); out = p.communicate(timeout=10)[0]
check("SEAM full: the key never appears in the server's log", key not in out)

# RESERVE'S SHAPE: the tunnel's origin runs profile=FULL on loopback (review 2026-11-06). A test that ran
# only profile=public passed while the real unit would have refused every portal request.
p, port, d = serve("full", {"MOSES_MCP_AUTH": "", "MOSES_MCP_ACCESS_ENFORCE": "0"})
try:
    check("SEAM loopback profile=full, report mode: a portal-shaped request (an Access token) gets in",
          post(port, {"Cf-Access-Jwt-Assertion": token(aud="b" * 64)}) == 200)
    check("SEAM loopback profile=full, report mode: a local process with nothing is refused",
          post(port, {}) == 401)
    st = json.loads((d / "state" / f"mcp-auth-{port}.json").read_text())
    check("SEAM loopback profile=full: counted (would-refuse 1, refused 1)",
          st.get("counts", {}).get("would-refuse") == 1 and st.get("counts", {}).get("refused") == 1)
finally:
    p.terminate(); p.communicate(timeout=10)

p, port, d = serve("full", {"MOSES_MCP_AUTH": ""})            # the default, as deployed: enforced
try:
    check("SEAM loopback profile=full, DEFAULT: enforced — a token that fails is refused",
          post(port, {"Cf-Access-Jwt-Assertion": token(aud="b" * 64)}) == 401)
finally:
    p.terminate(); p.communicate(timeout=10)

p, port, d = serve("public", {"MOSES_MCP_ACCESS_ENFORCE": "1"})
check("SEAM a listener told MOSES_MCP_AUTH=access on a non-loopback address will not start",
      subprocess.run([sys.executable, str(HERE / "mcp_server.py")], capture_output=True, text=True, timeout=60,
                     env={**os.environ, "MOSES_MCP_HOST": "0.0.0.0", "MOSES_MCP_PORT": str(free_port()),
                          "MOSES_MCP_AUTH": "access", "MOSES_STATE": str(TMP / "nostart"),
                          "MOSES_CONFIG_DIR": str(TMP / "nostart"), "MOSES_HOME": str(TMP)}).returncode != 0)
try:
    check("SEAM public, enforced: a local process with nothing is refused", post(port, {}) == 401)
    check("SEAM public, enforced: a token that fails is refused", post(port, {"Cf-Access-Jwt-Assertion": token(aud="b" * 64)}) == 401)
    key = (d / "cfg" / "mcp-keys" / "listener.key").read_text().strip()
    check("SEAM public, enforced: a key still gets in", post(port, {"Authorization": f"Bearer {key}"}) == 200)
finally:
    p.terminate(); p.communicate(timeout=10)


print("── watch it fail: each guard broken on purpose must turn the suite red ──")
saved = len(FAILS)


def mutant(label, patch, undo):
    patch()
    try:
        reds = suite("    [mutant] ")
    finally:
        undo()
    del FAILS[saved:]
    check(f"MUTANT {label} is caught ({reds} red)", reds > 0)


real_matches = mcp_key.matches
mutant("any bearer accepted", lambda: setattr(mcp_key, "matches", lambda p, k: "listener"),
       lambda: setattr(mcp_key, "matches", real_matches))
real_check = mcp_auth.AccessVerifier.check
mutant("Access check that trusts every token",
       lambda: setattr(mcp_auth.AccessVerifier, "check", lambda self, t: (True, "x", AUD)),
       lambda: setattr(mcp_auth.AccessVerifier, "check", real_check))
real_aud = VERIFIER.audiences
mutant("audience not checked", lambda: setattr(VERIFIER, "audiences", None),
       lambda: setattr(VERIFIER, "audiences", real_aud))
real_decide = mcp_auth.Gate._decide


async def _open(self, scope):
    return True, "access", "x", {}
mutant("gate that lets everything through", lambda: setattr(mcp_auth.Gate, "_decide", _open),
       lambda: setattr(mcp_auth.Gate, "_decide", real_decide))

print(f"\n  {PASSES} passed, {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
