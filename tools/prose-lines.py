#!/usr/bin/env python3
"""prose-lines.py — print the 1-based line numbers of a Python file that are PROSE, not code.

Prose here means a docstring: the module, class and function strings that record why this code is
shaped the way it is. This repository's value is largely in those, so a portability metric that
counts them as debt rewards deleting its own documentation.

WHY THIS EXISTS. check-portability.sh classified a line as prose if it started with `#`, `//`, `*`
or `>`. That is right for shell and wrong for Python: a docstring opens with a triple quote and its body
starts with an ordinary word, so every incident note in `addressing.py` and `diagnose.py` counted as
code. The persona-name total sat at 36 "in code" while the genuinely actionable literals numbered
about eight, and the number did not move when the underlying names were changed — which is how the
miscount was found.

A STRING THAT IS A VALUE IS STILL CODE. `OPS_NAME = "Birdeye"` must keep counting; only strings in
docstring position are exempt. That distinction is the whole point, and the self-test pins it.

Falls back to printing nothing if the file will not parse — a syntax error must not silently turn a
file's contents into prose and make the number look better.
"""

from __future__ import annotations

import ast
import sys


def docstring_lines(src: str) -> set[int]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()          # count everything as code; never flatter the number
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            out.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: prose-lines.py FILE.py", file=sys.stderr)
        return 2
    try:
        src = open(argv[0], encoding="utf-8").read()
    except OSError:
        return 0
    for n in sorted(docstring_lines(src)):
        print(n)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
