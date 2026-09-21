"""Images pasted into Slack, made readable to Moses — and nothing else.

Brad, 2026-08-23, after pasting a screenshot of a live bug and being told nobody could see it:
*"Go ahead and let him read to a scratch directory."*

NO TOOL IS GRANTED, AND THAT IS THE POINT. The first design fetched the image to a scratch directory
and handed Moses the `Read` tool scoped to it, on the belief that Claude Code confines file access to
its working directory. **Measured before shipping, and it did not:** the turn read
~/.claude/memory/MEMORY.md from outside the working directory with no refusal at all. An explicit
`--allowedTools Read` is a permission grant, and it does not carry the boundary the sandbox has when
Read is simply never allowed — which is why Knight's guarantee holds and this one would not have.

So the image travels as MESSAGE CONTENT instead, through `--input-format stream-json`, exactly the
way a picture reaches any Claude conversation. Moses keeps every native tool denied, there is no
directory to scope, nothing to clean up, and nothing written to disk at all. Strictly less surface
than the version that was approved, arrived at because the guard was tested rather than assumed.

WHAT IS DELIBERATELY REFUSED. Images only. A PDF, a .zip or a text file is not fetched, because
"look at this screenshot" is the request being served and every other file type widens what an
attacker in a shared channel could put in front of the model for the same benefit of none.
"""
from __future__ import annotations

import base64
import urllib.error
import urllib.request

# What Claude can actually look at. Kept narrow on purpose — see the module docstring.
ALLOWED_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp"}

# A Slack screenshot is well under this. The cap exists so a large upload cannot fill /tmp on the
# box that runs the listener, which would take Moses down for everybody.
MAX_BYTES = 12 * 1024 * 1024
MAX_FILES = 4


def fetch(files: list[dict], token: str) -> tuple[list[dict], list[str]]:
    """Download the image attachments on one message, into memory.

    Returns (images, notes). Each image is {"mime", "b64", "name"} ready to become a content block.
    `notes` is what could NOT be fetched and why — surfaced to the model rather than swallowed,
    because "I can\'t see an image you believe I can see" is the exact failure this whole change
    exists to remove. Silence would recreate it one layer down.
    """
    notes: list[str] = []
    wanted = []
    for f in files or []:
        if not isinstance(f, dict):
            continue
        mime = (f.get("mimetype") or "").lower()
        name = f.get("name") or f.get("id") or "file"
        if mime not in ALLOWED_MIME:
            notes.append(f"{name}: not an image ({mime or 'unknown type'}), so it was not fetched")
            continue
        if int(f.get("size") or 0) > MAX_BYTES:
            notes.append(f"{name}: larger than {MAX_BYTES // (1024 * 1024)}MB, so it was not fetched")
            continue
        wanted.append(f)
        if len(wanted) >= MAX_FILES:
            break

    images: list[dict] = []
    for i, f in enumerate(wanted, 1):
        url = f.get("url_private_download") or f.get("url_private")
        name = f.get("name") or f.get("id") or f"image{i}"
        if not url:
            notes.append(f"{name}: Slack gave no download URL")
            continue
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=20) as r:
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                # SLACK ANSWERS 200 WITH A LOGIN PAGE when the token cannot read files, rather than
                # 401. Trusting the status code would send HTML to the model as an image and produce
                # a report about a corrupt screenshot — a wrong answer that looks like our bug rather
                # than a missing scope.
                if ctype not in ALLOWED_MIME:
                    notes.append(f"{name}: Slack returned {ctype or 'no content type'} instead of the "
                                 "image — the bot token is probably missing files:read")
                    continue
                blob = r.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as e:
            notes.append(f"{name}: Slack refused the download (HTTP {e.code})")
            continue
        except Exception as e:                                    # noqa: BLE001
            notes.append(f"{name}: could not be downloaded ({type(e).__name__})")
            continue

        if len(blob) > MAX_BYTES:
            notes.append(f"{name}: bigger than it claimed, so it was discarded")
            continue

        images.append({"mime": ctype, "b64": base64.b64encode(blob).decode(), "name": name})

    return images, notes
