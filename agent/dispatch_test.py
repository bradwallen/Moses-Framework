#!/usr/bin/env python3
"""dispatch.classify — what the listener answers itself, before any model is asked.

    python3 agent/dispatch_test.py

A deterministic answer is only right when the message is unambiguous. Brad's real phrasing on
2026-10-17, "put the listings page at example.com/listings", was answered with the retired to-do
list's refusal because it started with "put". These pin the line: a list named → captured; anything
else that merely starts with a capture verb → the model.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dispatch  # noqa: E402

fails = []


def check(text, kind, target=""):
    got = dispatch.classify(text)
    ok = got.kind == kind and (not target or got.target == target)
    print(f"  {'ok  ' if ok else 'FAIL'} {text!r} → {got.kind}{'/' + got.target if got.target else ''}")
    if not ok:
        fails.append(text)


print("TO THE MODEL — a capture verb, but no list named")
check("put the listings page at example.com/listings", "question")
check("add a CSV export button to the trip page", "question")
check("note that the deploy failed twice", "question")
check("log the outage on the incident", "question")
print("\nCAPTURED — a list is named")
check("add buy a domain to my ideas", "capture", "ideas")
check("put dark mode on the idea list", "capture", "ideas")
check("add renew the cert to my to-do list", "capture", "todo")
print("\nUNCHANGED")
check("I have an idea: a map of every trip", "capture", "ideas")
check("research GIS internship boards", "research")
check("add to my list", "question")
print(f"\n  {'FAILED: ' + str(len(fails)) if fails else 'all pass'}")
sys.exit(1 if fails else 0)
