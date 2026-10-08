#!/usr/bin/env python3
"""Pin how the transcript index answers. Run: python3 mcp/transcript_index_test.py

On 2026-10-05 search_transcripts' snippets were taken for the whole store: the reader went to the raw
session files for full text and told Brad his messages weren't in the database. Every one was. These
pin the two answers that prevent it, on a throwaway index, never the real one.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import transcript_index as T  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"  — {detail}"))
    if not cond:
        fails.append(label)


db = Path(tempfile.mkdtemp(prefix="ti-test-")) / "t.db"
c = T.connect(db)
c.execute("INSERT INTO sessions(session_id,project,title,started) VALUES ('abcdef1234','proj','Moses','2026-09-03T17:00')")
LONG = "a name is just a name, the role defines what it does. " + "More words here. " * 200
c.execute("INSERT INTO messages(session_id,seq,ts,role,text) VALUES ('abcdef1234',7,'2026-09-03T17:07','user',?)", (LONG,))
c.execute("INSERT INTO msg_fts(msg_fts) VALUES ('rebuild')")
c.commit()
c.close()

out = T.search('"a name is just a name"', db=db)
check("SEARCH finds the message and says where it is", "session abcdef12 #7" in out, out)
check("SEARCH says it shows snippets, and how to get the whole message",
      "These are snippets" in out and "read_session" in out and "count=1" in out, out)
one = T.read_session("abcdef12", start=7, count=1, db=db)
check("READ one message comes back whole, never truncated", LONG.strip() in one and "truncated" not in one, one[-120:])
many = T.read_session("abcdef12", start=1, count=30, db=db)
check("READ a page of messages truncates long ones, and says how to get one whole",
      "count=1 gives it whole" in many, many[-120:])

print(f"\n  {len(fails)} failed" if fails else "\n  all good")
sys.exit(1 if fails else 0)
