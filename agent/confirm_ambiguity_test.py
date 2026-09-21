#!/usr/bin/env python3
"""Whose "yes" was that?

    /home/brad/moses-venv/bin/python confirm_ambiguity_test.py

2026-08-22: Jon opened Atlas up to Brad in #the_4_horsemen. Atlas offered to take on Viatica work,
Brad replied "Sounds good!" — to Atlas — and Moses filed his own oldest pending proposal, for work
that had already shipped the day before.

The bare-affirmation matcher skips the addressing check on purpose: nobody re-says a bot's name to
agree with it. That reasoning was sound while Moses was the only thing talking to Brad. These pin
the narrower rule that replaces it.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MOSES_STATE", tempfile.mkdtemp())
import proposals as P                                            # noqa: E402

fails = []
def check(label, cond, detail=""):
    print(f"  {'✓' if cond else '✗'} {label}" + ("" if cond else f"  — {detail}"))
    if not cond: fails.append(label)

MOSES, ATLAS, BRAD = "moses", "atlas", "brad"
def msg(ts, who): return {"ts": str(ts), "who": who}
is_self    = lambda m: m["who"] == MOSES
is_machine = lambda m: m["who"] in (MOSES, ATLAS)
def verdict(history, ts): return P.confirmation_is_for_me(history, ts, is_self=is_self, is_machine=is_machine)

print("the vocabulary is still generous — that part was right")
check("'Sounds good!' still reads as a confirmation", bool(P.CONFIRM.match("Sounds good!")))
check("'affirm' still works (Jon's original complaint)", bool(P.CONFIRM.match("affirm")))

print("\nbut a bare yes is only MINE when nothing else spoke in between")
# history is newest-first, as recent() returns it.
check("THE REAL CASE: Atlas spoke after my proposal — refuse to assume",
      not verdict([msg(300, BRAD), msg(200, ATLAS), msg(100, MOSES)], "100"),
      "this is the exchange that filed a completed task")

check("nobody spoke since my proposal — take it",
      verdict([msg(300, BRAD), msg(100, MOSES)], "100"))

check("Brad and Jon talking in between does NOT make it ambiguous",
      verdict([msg(400, BRAD), msg(300, "jon"), msg(200, BRAD), msg(100, MOSES)], "100"),
      "humans conversing is normal; a second AGENT addressing Brad is the ambiguity")

check("my own follow-up message does not count as another agent",
      verdict([msg(400, BRAD), msg(250, MOSES), msg(100, MOSES)], "100"))

check("an agent speaking BEFORE my proposal is irrelevant",
      verdict([msg(400, BRAD), msg(100, MOSES), msg(50, ATLAS)], "100"))

print("\nand it degrades rather than crashing on bad input")
check("no proposal timestamp — do not block", verdict([msg(1, ATLAS)], ""))
check("unparseable timestamp — do not block", verdict([msg(1, ATLAS)], "not-a-number"))
check("junk in history is skipped", verdict([{"ts": None, "who": ATLAS}, msg(100, MOSES)], "100"))

print(f"\n{'FAILED: ' + ', '.join(fails) if fails else 'all confirmation-ambiguity checks passed'}")
sys.exit(1 if fails else 0)
