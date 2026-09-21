#!/usr/bin/env python3
"""Pin what Moses may look at, and how. Run: python3 attachments_test.py

THE HISTORY THESE TESTS EXIST TO PROTECT. The first version of this feature fetched a Slack image to
a scratch directory and handed Moses the `Read` tool, scoped by running the CLI with that directory
as its working directory — on the belief that Claude Code confines file access to the working tree.

It was measured before shipping and it did NOT: the turn read ~/.claude/memory/MEMORY.md, well
outside that directory, with no refusal recorded. An explicit `--allowedTools Read` is a permission
grant, and it does not carry the boundary the sandbox has when Read is simply never allowed — which
is why Knight's guarantee holds and that one would not have.

So the image travels as MESSAGE CONTENT instead. Every native tool stays denied, nothing is written
to disk, and there is no boundary left to get wrong. The assertions below are what stop anybody
reintroducing the grant on the grounds that it would be simpler.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MOSES_STATE", tempfile.mkdtemp(prefix="moses-att-test-"))

import attachments        # noqa: E402
import conversation       # noqa: E402

passed = failed = 0


def ok(m):
    global passed
    passed += 1
    print(f"  PASS  {m}")


def bad(m):
    global failed
    failed += 1
    print(f"  FAIL  {m}")


def check(cond, m):
    ok(m) if cond else bad(m)


print("\nNO TOOL IS GRANTED — the whole point of the redesign")
check("Read" not in conversation.tools_for(False).split(","), "an ordinary turn is not offered Read")
check("Read" not in conversation.tools_for(True).split(","), "a directed turn is not offered Read either")
check("Read" in conversation.NO_TOOLS.split(","), "Read is still on the deny list, unconditionally")
check(not hasattr(conversation, "no_tools_for"),
      "there is no per-turn deny list — nothing can lift Read for a single turn")

print("\nTHE IMAGE BECOMES MESSAGE CONTENT")
captured = {}


def _fake_run(argv, **kw):
    captured["argv"] = argv
    captured["input"] = kw.get("input")

    class R:
        stdout = json.dumps({"type": "result", "result": "ok", "total_cost_usd": 0.0}) + "\n"
        stderr = ""
        returncode = 0
    return R()


_real_run = conversation.subprocess.run
conversation.subprocess.run = _fake_run
try:
    conversation._invoke("what is this?", conversation.tools_for(True), directed=True,
                         images=[{"mime": "image/png", "b64": "QUJD", "name": "shot.png"}])
    argv, payload = captured["argv"], captured["input"]
    check("--input-format" in argv and argv[argv.index("--input-format") + 1] == "stream-json",
          "the turn switches to a real message array")
    check("what is this?" not in argv,
          "the prompt is NOT also passed as an argument — it moved into the message")
    blocks = json.loads(payload)["message"]["content"]
    check(blocks[0]["type"] == "image" and blocks[0]["source"]["data"] == "QUJD",
          "the image arrives as a base64 image block")
    check(blocks[-1]["type"] == "text" and "what is this?" in blocks[-1]["text"],
          "and the question comes after it, the order a person would ask in")

    captured.clear()
    conversation._invoke("plain question", conversation.tools_for(True), directed=True)
    check("--input-format" not in captured["argv"] and captured["input"] is None,
          "a turn with no image is completely unchanged")
finally:
    conversation.subprocess.run = _real_run

print("\nWHAT IS FETCHED — images only")
imgs, notes = attachments.fetch(
    [{"mimetype": "application/pdf", "name": "invoice.pdf", "size": 100, "url_private": "https://x/y"}], "tok")
check(not imgs and any("not an image" in n for n in notes), "a PDF is refused, and the refusal says why")

imgs, notes = attachments.fetch(
    [{"mimetype": "image/png", "name": "huge.png", "size": 99 * 1024 * 1024, "url_private": "https://x/y"}], "tok")
check(not imgs and any("larger than" in n for n in notes),
      "an oversized image is refused before anything is downloaded")

check(attachments.fetch([], "tok") == ([], []), "a message with no files produces nothing and no noise")

print("\nA FAILED DOWNLOAD IS REPORTED, NOT SWALLOWED")
# Slack answers 200 with an HTML login page when the token cannot read files, rather than 401.
# Believing the status code would hand HTML to the model as an image, and it would report a corrupt
# screenshot — a wrong answer that looks like our bug rather than a missing scope.
import urllib.request                                             # noqa: E402


class _FakeHTML:
    headers = {"Content-Type": "text/html; charset=utf-8"}

    def read(self, *_):
        return b"<html>login</html>"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_real_open = urllib.request.urlopen
urllib.request.urlopen = lambda *a, **k: _FakeHTML()
try:
    imgs, notes = attachments.fetch(
        [{"mimetype": "image/png", "name": "shot.png", "size": 1000, "url_private": "https://x/y"}], "tok")
    check(not imgs and any("files:read" in n for n in notes),
          "an HTML login page is caught and named as a missing scope, not passed off as an image")
finally:
    urllib.request.urlopen = _real_open

print(f"\n  {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
