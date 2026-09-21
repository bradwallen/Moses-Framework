#!/usr/bin/env python3
"""The 'every page has the same nav' gate.

    .venv/bin/python mcp/dashboard_nav_test.py

The nav pills were inconsistent across the dashboard, and the cause was structural rather than
cosmetic: `shell()` said "the nav is written once so a new page cannot be orphaned", but only Moses
and Architecture went through it. Projects built its own document and rendered NO nav — you could
click into it and not back out — and both error frames were the same dead end.

So this does not check colors. It checks the one property that makes colors irrelevant: every page
is framed by the same `shell()`, so the pill markup is byte-identical everywhere and there is
exactly one place left that can change it.
"""
import inspect, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dashboard_server as d                                    # noqa: E402

# The nav is the subject here, not the live numbers. Viatica's pill asks the running app how it is
# doing, which is a network call that has no business in a gate.
d.live_state.summary = lambda: None

fails = []
def check(label, cond, detail=""):
    print(f"  {'✓' if cond else '✗'} {label}" + ("" if cond else f"  — {detail}"))
    if not cond: fails.append(label)


def nav_of(page: str):
    m = re.search(r"<nav>(.*?)</nav>", page or "", re.S)
    return m.group(1) if m else None

def skeleton(nav: str) -> str:
    """The nav with the 'you are here' marker taken off — what must be identical on every page."""
    return nav.replace(' aria-current="page"', "").replace('class="on"', 'class=""')


PAGES = sorted(set(d.ROUTES) | set(d.ALIASES))

print("every route renders, and every route has a nav")
navs = {}
for p in PAGES:
    out = d.render(p)
    check(f"{p} renders", bool(out))
    navs[p] = nav_of(out)
    check(f"{p} has exactly one nav", (out or "").count("<nav>") == 1,
          f"found {(out or '').count('<nav>')}")

print("\nthe pills are byte-identical from page to page")
# THE WHOLE POINT. If a page ever hand-rolls its own bar again, these stop matching.
base = skeleton(navs["/"] or "")
for p in PAGES:
    check(f"{p} carries the same pills as /", skeleton(navs[p] or "") == base)
_TABS = re.findall(r'\("(/[^"]*)",\s*"([^"]+)"\)',
                   inspect.getsource(d.shell).split("tabs = [")[1].split("]")[0])
check("shell() defines the tabs this asserts against", len(_TABS) >= 3, str(_TABS))
check("every pill shell() defines reaches the page", base.count("<a href=") == len(_TABS),
      f"shell defines {len(_TABS)} tabs, the page renders {base.count('<a href=')}: {base[:120]}")
for _href, _label in _TABS:
    check(f"the {_label} pill is present", f'href="{_href}"' in base)
check("no page styles a pill inline", 'style=' not in base,
      "an inline style is drift that this comparison must catch")

# A comparison that cannot fail proves nothing (commandment 8).
check("the comparison would catch a drifted pill",
      skeleton('<a href="/" class="">Moses</a>')
      != skeleton('<a href="/" class="" style="padding:2px">Moses</a>'))

print("\nexactly one pill says 'you are here', and it is the right one")
for p, expect in [("/", "Moses"), ("/index.html", "Moses"),
                  ("/projects", "Projects"), ("/architecture", "Architecture")]:
    nav = navs[p] or ""
    n_on = nav.count('class="on"')
    check(f"{p} marks one active pill", n_on == 1, f"found {n_on}")
    m = re.search(r'<a href="[^"]*" class="on"[^>]*>([^<]+)</a>', nav)
    check(f"{p} highlights {expect}", bool(m) and m.group(1) == expect,
          m.group(1) if m else "no active pill")
    check(f"{p} marks it for screen readers too", 'aria-current="page"' in nav)

print("\nan error page is still a page you can navigate away from")
# Deliberately break a route and watch the frame hold — the failure this gate exists for.
d.ROUTES["/_boom"] = lambda: 1 / 0
try:
    broken = d.render("/_boom")
    check("a route that raises still renders a nav", "<nav>" in (broken or ""))
    check("its pills match every other page", skeleton(nav_of(broken) or "") == base)
    check("and it names what broke", "ZeroDivisionError" in (broken or ""))
finally:
    del d.ROUTES["/_boom"]

check("an unknown path is a 404, not a nav-less page", d.render("/nope") is None)
check("a query string still finds the page", bool(d.render("/projects?x=1")))

print("\none CSS rule owns the pills — sizing, spacing, hover and active")
check("there is only one nav pill rule", len(re.findall(r"nav a\{", d.CSS)) == 1)
base_rule = re.search(r"nav a\{([^}]*)\}", d.CSS).group(1)
for prop in ["padding", "font-size", "border-radius", "line-height", "display:inline-block"]:
    check(f"{prop} is set once, for all pills", prop in base_rule)
check("the bar sets the spacing between pills", "gap:" in re.search(r"nav\{([^}]*)\}", d.CSS).group(1))

# HOVER MUST NOT IMPERSONATE ACTIVE. The old rule gave a hovered pill the accent border that means
# "you are here", so the bar showed two current pages while the pointer was down.
hover = re.search(r"nav a:hover\{([^}]*)\}", d.CSS).group(1)
check("hover does not borrow the active border", "border-color" not in hover, hover)
check("hover still gives feedback", "background" in hover or "color" in hover)

# The two rules used to have EQUAL specificity, so which won was decided by source order alone.
on = re.search(r"nav a\.on,nav a\.on:hover\{([^}]*)\}", d.CSS)
check("active is pinned against hover, not left to source order", bool(on),
      "expected 'nav a.on,nav a.on:hover' so the active pill cannot be un-styled by hovering it")
check("active owns the accent border", bool(on) and "border-color:var(--accent)" in on.group(1))

print(f"\n{'FAILED: ' + ', '.join(fails) if fails else 'nav is identical on every page'}")
sys.exit(1 if fails else 0)
