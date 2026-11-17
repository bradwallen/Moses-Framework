"""secret_names — which KEY in a KEY=value file names a secret. One rule, for every program that asks.

moses-credentials (the inventory) and moses-seal (the TPM sealing) both use it, so "a token file" means
the same thing to the program that lists them and the one that seals them (review 2026-11-06: two copies
had drifted, and the sealer missed CREDENTIAL, DATABASE_URL, _DSN and AUTH_ names).

A key must look like a variable name. A line of a multi-line value (a pretty-printed JSON key, say) is
not a key, and treating the text before its first "=" as a name would put part of the secret into the
report that lists names (review 2026-11-06).
"""
from __future__ import annotations

import re

KEY_SHAPE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
SECRET_NAME = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|_KEY$|^KEY$|API_KEY|PRIVATE|CREDENTIAL|WEBHOOK|"
                         r"DATABASE_URL|_DSN$|AUTH_|_PAT$)", re.I)
NOT_SECRET = re.compile(r"(_ID$|_URL$|_CHANNEL|_NAME$|_HOST$|_PORT$|_EMAIL$|_FROM_)", re.I)
# A name that matches NOT_SECRET is still a secret when it also says one of these: SLACK_WEBHOOK_URL's
# URL is the secret, and so is DATABASE_URL's.
STILL_SECRET = re.compile(r"(TOKEN|SECRET|PASSWORD|KEY|WEBHOOK|CREDENTIAL|DATABASE_URL)", re.I)


def is_secret_name(name: str) -> bool:
    if not KEY_SHAPE.fullmatch(name):
        return False
    return bool(SECRET_NAME.search(name)) and not (NOT_SECRET.search(name) and not STILL_SECRET.search(name))


def env_keys(text: str) -> list[str]:
    """The KEY of every KEY=value line that has a variable-shaped key. Values never leave this function."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k = line.split("=", 1)[0].strip().removeprefix("export ").strip()
        if KEY_SHAPE.fullmatch(k):
            out.append(k)
    return out
