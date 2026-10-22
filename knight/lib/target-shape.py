#!/usr/bin/env python3
"""target-shape — is a Knight target file nothing but plain settings?

    git show <branch>:knight/targets/<t>.env | python3 knight/lib/target-shape.py <t>

Prints the first line that is not a plain setting, prefixed with the target name, and exits 1; prints
nothing and exits 0 when every line is one of: blank, a comment, `KT_NAME=` with a single-quoted,
double-quoted (no `$` or backtick) or bare value, the opening of an array whose entries are
single-quoted, a single-quoted array entry, or `)`.

WHY (review of 2026-10-17). knight-land compares the lines that name KT_SOURCE, KT_PUSH_REMOTE,
KT_AUTO_LAND and KT_CONTINUE to decide whether a branch repoints a target or switches on building
away. Text can be fooled — `declare "KT_AUTO_""LAND=1"` and `printf -v "KT_CONT""INUE" 1` set the
variable when the file is sourced and name neither — so a changed target file must be shaped such
that what its text says is what sourcing it does. Nothing here sources or runs the file.
"""
import re
import sys

LINE = re.compile(r"""^\s*(?:
      KT_[A-Z_]+=(?:'[^']*'|"[^"$`\\]*"|[A-Za-z0-9_./:@+,=-]*|\((?:\s*'[^']*')*\s*\)?)
    | '[^']*'
    | \)
  )?\s*(?:\#.*)?$""", re.X)

name = sys.argv[1] if len(sys.argv) > 1 else "target"
for i, line in enumerate(sys.stdin.read().splitlines(), 1):
    if not LINE.match(line):
        print(f"{name}:{i}: {line.strip()[:80]}")
        sys.exit(1)
