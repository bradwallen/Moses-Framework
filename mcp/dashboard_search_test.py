#!/usr/bin/env python3
"""The search gate — in a real browser, because search is behavior, not markup.

    mcp/venv/bin/python mcp/dashboard_search_test.py

Brad, 2026-09-11: "add a search box to both the Projects page and the changelog page." The part that
can silently break is the one a markup test cannot see: the Projects page REPLACES its own content every
two minutes, so a box inside that content would lose what was typed, and a filter applied once would
stop applying to the new content. This runs the dashboard on a spare port with a throwaway registry,
drives it with headless Chromium (Playwright, borrowed from the Viatica checkout), makes the refresh
fire every 300ms instead of every 120s, and changes the registry mid-search.

It needs Playwright and a Chromium build. If it cannot find them it FAILS and says so — a gate that
quietly skips is the portability checker again (commandment 8).
"""
import json, os, subprocess, sys, tempfile, threading
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# A checkout that has Playwright installed — the browser half borrows it rather than installing its
# own. MOSES_PLAYWRIGHT_DIR in the operator's settings; with none, the check says it could not run.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent" / "lib"))
import moses_env as _env  # noqa: E402
VIATICA = os.environ.get("VIATICA_REPO") or _env.setting("MOSES_PLAYWRIGHT_DIR")

fails = []
def check(label, cond, detail=""):
    print(f"  {'✓' if cond else '✗'} {label}" + ("" if cond else f"  — {detail}"))
    if not cond: fails.append(label)


def repo_with(history, root):
    r = Path(tempfile.mkdtemp(dir=root))
    run = lambda *a, env=None: subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True, env=env)
    run("init", "-q", "-b", "main"); run("config", "user.email", "t@example.com"); run("config", "user.name", "T")
    for i, (day, subject, body, version) in enumerate(history):
        if version:
            (r / "package.json").write_text(json.dumps({"version": version}) + "\n"); run("add", "package.json")
        (r / "n.txt").write_text(str(i)); run("add", "n.txt")
        env = {**os.environ, "GIT_AUTHOR_DATE": f"{day}T12:00:00", "GIT_COMMITTER_DATE": f"{day}T12:00:00"}
        run("commit", "-q", "-m", subject + (f"\n\n{body}" if body else ""), env=env)
    return str(r)


NODE = r"""
import { createRequire } from "module";
import fs from "fs";
const require = createRequire(process.env.VIATICA_REPO + "/package.json");
let chromium;
try { ({ chromium } = require("playwright")); } catch (e) { console.log(JSON.stringify({ cannotRun: String(e) })); process.exit(0); }
const [base, regPath] = process.argv.slice(2);
const out = {};
const browser = await chromium.launch();
try {
const page = await browser.newPage();
await page.addInitScript(() => { const si = window.setInterval; window.setInterval = (f) => si(f, 300);
  window.__swaps = 0; document.addEventListener("live:updated", () => window.__swaps++); });
const blocks = () => page.$$eval("details.pblock", es => es.filter(e => e.checkVisibility()).map(e => e.id));
// A card's TITLE, without its code (V14) in front. The code is checked on its own, below.
const cards = (id) => page.$$eval(`#${id} .card`, es => es.filter(e => e.checkVisibility())
  .map(e => { const c = e.cloneNode(true); c.querySelector(".ref")?.remove(); return c.textContent.trim(); }));
const typeQ = async (v) => { await page.fill("#q", v, { timeout: 3000 }); await page.waitForTimeout(50); };

await page.goto(base + "/projects");
out.box = await page.isVisible("#q");
out.all = await blocks();
await typeQ("roster");
out.roster = { blocks: await blocks(), alphaCards: await cards("p-alpha"), alphaOpen: await page.$eval("#p-alpha", e => e.open),
               count: await page.textContent("#qn") };
await typeQ("group");
out.group = { blocks: await blocks(), betaCards: (await cards("p-beta")).length };
await typeQ("alpha");
out.name = { blocks: await blocks(), alphaCards: (await cards("p-alpha")).length };
// An item's code typed into the box finds its card. Codes are how items get named in conversation now.
const rosterRef = await page.evaluate(() => [...document.querySelectorAll(".card")]
  .find(e => e.textContent.includes("Group trips roster"))?.querySelector(".ref")?.textContent || "");
await typeQ(rosterRef);
out.byCode = { ref: rosterRef, blocks: await blocks(), alphaCards: await cards("p-alpha") };

await typeQ("roster");
const before = await page.evaluate(() => window.__swaps);
out.clockBefore = await page.textContent("[data-clock]");
await page.waitForTimeout(1200);                      // four refresh ticks, nothing changed on the server
out.idleSwaps = (await page.evaluate(() => window.__swaps)) - before;
out.clockAfter = await page.textContent("[data-clock]");
const reg = JSON.parse(fs.readFileSync(regPath, "utf8"));
reg.projects.find(p => p.id === "gamma").milestones.push({ title: "Roster import from last year", done: false });
fs.writeFileSync(regPath, JSON.stringify(reg));
await page.waitForFunction(() => document.getElementById("live").textContent.includes("Roster import"), null, { timeout: 5000 })
  .then(() => out.swapped = true, () => out.swapped = false);
await page.waitForTimeout(100);
out.afterRefresh = { value: await page.inputValue("#q"), blocks: await blocks(), gammaCards: await cards("p-gamma") };

await typeQ("");
out.cleared = { blocks: await blocks(), gammaOpen: await page.$eval("#p-gamma", e => e.open), count: await page.textContent("#qn") };

await typeQ("stripe");
out.url = page.url();
await page.reload();
await page.waitForTimeout(100);
out.reloaded = { value: await page.inputValue("#q"), blocks: await blocks() };

const secs = () => page.$$eval("section.rel", es => es.filter(e => e.checkVisibility()).map(e => e.querySelector("h2").id));
const chg = () => page.$$eval(".chg", es => es.filter(e => e.checkVisibility()).map(e => e.querySelector("summary")?.firstChild?.textContent?.trim() || e.firstChild.textContent.trim()));
const weeks = () => page.$$eval(".wk", es => es.filter(e => e.checkVisibility()).length);
await page.goto(base + "/changelog/viatica");
out.clAll = { secs: await secs(), weeks: await weeks() };
await typeQ("shift");
out.clShift = { secs: await secs(), changes: await chg(), weeks: await weeks() };
await typeQ("1.0.0");
out.clVersion = { secs: await secs(), changes: (await chg()).length };
await typeQ("real money");
out.clBody = { changes: await chg(), open: await page.$eval("details.chg", e => e.open) };
await typeQ("");
out.clCleared = { secs: await secs(), anyOpen: await page.$$eval("details.chg", es => es.some(e => e.open)) };

// A LINK STRAIGHT TO ONE PROJECT opens that project — and the search box must not eat the fragment
// on the way. It did: replaceState rewrote the address to the bare path on load, so #p-<id> was gone
// before anything could act on it, and the reader landed on a collapsed block (2026-09-24).
const deep = await browser.newContext();                 // cold: no history, nothing cached
const p4 = await deep.newPage();
await p4.goto(base + "/projects#p-gamma");   // parked, so it renders CLOSED — the link has work to do
await p4.waitForTimeout(500);
// The same page WITHOUT the fragment, so "it opened" means the link did it rather than the block
// having been open all along.
const p5 = await deep.newPage();
await p5.goto(base + "/projects");
await p5.waitForTimeout(200);
out.deepLink = { open: await p4.$eval("#p-gamma", e => e.open), hash: await p4.evaluate(() => location.hash),
                 closedByDefault: await p5.$eval("#p-gamma", e => !e.open) };

const nojs = await browser.newContext({ javaScriptEnabled: false });
const p2 = await nojs.newPage();
await p2.goto(base + "/projects");
out.noJsBox = await p2.isVisible("#q");
} catch (e) {
  // A step failed mid-run — the page broke, not the machine. Reported as that, never as "could not run".
  out.crashed = String(e).split("\n")[0];
}
await browser.close();
console.log(JSON.stringify(out));
"""

with tempfile.TemporaryDirectory() as root:
    repo = repo_with([
        ("2026-06-01", "feat: initial scaffold", "", "0.1.0"),
        ("2026-06-10", "feat: the itinerary builder", "", None),
        ("2026-07-01", "fix: dates shift west of Greenwich", "", None),
        ("2026-08-20", "Viatica 1.0", "The day it charged real money.", "1.0.0"),
        ("2026-08-21", "Stop telling Google Viatica is free", "", None),
        ("2026-09-10", "Never show a flight time nobody stated", "", "1.0.1"),
    ], root)
    reg = Path(root) / "projects.json"
    reg.write_text(json.dumps({"projects": [
        {"id": "alpha", "name": "Alpha", "rank": 1, "status": "active", "stage": "building", "repo": "",
         "milestones": [{"title": "Group trips roster", "done": False}, {"title": "Travel-day mode", "done": False}],
         "ideas": [{"title": "Passport renewal reminder"}]},
        {"id": "beta", "name": "Beta", "rank": 2, "status": "active", "stage": "building", "repo": "",
         "milestones": [{"title": "Stripe webhook events", "done": False}, {"title": "Cancel in app", "done": False}],
         "next": "renew the group booking"},
        {"id": "gamma", "name": "Gamma", "rank": 3, "status": "parked", "stage": "idea", "repo": "",
         "milestones": [{"title": "Nothing relevant", "done": False}]},
        {"id": "viatica", "name": "Viatica", "rank": 4, "status": "parked", "stage": "shipped", "repo": repo, "milestones": []},
    ], "inbox": []}))
    os.environ["MOSES_REGISTRY"] = str(reg)
    import dashboard_server as d
    from http.server import ThreadingHTTPServer
    d.projects.REGISTRY = reg
    d.live_state.summary = lambda: None
    # A CLOCK THAT MOVES ON EVERY RENDER. The page prints the time to the minute inside the refreshed
    # area, and on 2026-09-15 a minute rolled over inside the idle window below and failed the 07:30
    # run. Real time made that about a 1-in-50 event; this makes every refresh tick a new minute, so the
    # idle check is certain rather than lucky, in both directions.
    import datetime as _dtm

    class _Tick(_dtm.datetime):
        n = 0

        @classmethod
        def now(cls, tz=None):
            cls.n += 1
            return _dtm.datetime(2026, 9, 15, 7, 0) + _dtm.timedelta(minutes=cls.n)
    d.datetime = _Tick

    print("without script, there is no box pretending to work — and it lives outside the refreshed area")
    raw = d.render("/projects") or ""
    check("the box is rendered hidden", 'id="q" hidden' in raw)
    check("the box is outside #live, so a refresh cannot wipe it", raw.find('id="q"') < raw.find('<main id="live">'))

    srv = ThreadingHTTPServer(("127.0.0.1", 0), d.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    script = Path(root) / "drive.mjs"; script.write_text(NODE)
    r = subprocess.run(["node", str(script), f"http://127.0.0.1:{srv.server_address[1]}", str(reg)],
                       capture_output=True, text=True, timeout=120, env={**os.environ, "VIATICA_REPO": VIATICA})
    srv.shutdown()
    try:
        o = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        o = {"cannotRun": (r.stderr or r.stdout)[-600:]}
    if "cannotRun" in o:
        check("the browser test could run", False, f"COULD NOT RUN — {o['cannotRun']}")
    elif "crashed" in o:
        if "box" in o:
            check("with script, the box appears", o["box"])
        check("the page could be driven all the way through", False, f"a step failed: {o['crashed']}")
    else:
        print("\nthe Projects page")
        check("with script, the box appears", o["box"])
        check("everything shows before a search", o["all"] == ["p-alpha", "p-beta", "p-gamma", "p-viatica"], o["all"])
        check("a card match narrows to that project and that card", o["roster"]["blocks"] == ["p-alpha"]
              and o["roster"]["alphaCards"] == ["Group trips roster"], o["roster"])
        check("and opens it", o["roster"]["alphaOpen"])
        check("the count says how many", o["roster"]["count"] == "1 match", o["roster"]["count"])
        check("a match only in a project's notes shows the whole project",
              o["group"]["blocks"] == ["p-alpha", "p-beta"] and o["group"]["betaCards"] == 2, o["group"])
        check("searching a project's name shows all its cards", o["name"]["blocks"] == ["p-alpha"] and o["name"]["alphaCards"] == 3, o["name"])
        check("an item's code finds its card", bool(o["byCode"]["ref"]) and o["byCode"]["blocks"] == ["p-alpha"]
              and o["byCode"]["alphaCards"] == ["Group trips roster"], o["byCode"])
        print("\nthe refresh, which is where this could quietly break")
        check("while nothing changes, a search does not make the refresh redraw the page", o["idleSwaps"] == 0,
              f'{o["idleSwaps"]} swaps in four idle ticks')
        # The server's clock moved a minute on every one of those ticks (see _Tick), so this proves the
        # check above held while the time really was changing, and that the time on the page kept up.
        check("and the time on the page still moves, without a redraw",
              bool(o.get("clockBefore")) and o.get("clockBefore") != o.get("clockAfter"),
              f'{o.get("clockBefore")!r} -> {o.get("clockAfter")!r}')
        check("the refresh really swapped in new content", o["swapped"])
        check("what was typed survives it", o["afterRefresh"]["value"] == "roster", o["afterRefresh"])
        check("and the filter applies to the new content",
              o["afterRefresh"]["blocks"] == ["p-alpha", "p-gamma"] and o["afterRefresh"]["gammaCards"] == ["Roster import from last year"],
              o["afterRefresh"])
        check("clearing brings everything back", o["cleared"]["blocks"] == ["p-alpha", "p-beta", "p-gamma", "p-viatica"], o["cleared"])
        check("and puts a parked project back the way it was — closed", o["cleared"]["gammaOpen"] is False)
        check("the search is in the address", "q=stripe" in o["url"], o["url"])
        check("so a reload keeps it", o["reloaded"]["value"] == "stripe" and o["reloaded"]["blocks"] == ["p-beta"], o["reloaded"])
        print("\nthe changelog")
        check("every release shows before a search", o["clAll"]["secs"] == ["v1.0.1", "v1.0.0", "v0.1.0"], o["clAll"])
        check("a word finds the one change, in its release", o["clShift"]["secs"] == ["v0.1.0"]
              and o["clShift"]["changes"] == ["fix: dates shift west of Greenwich"], o["clShift"])
        check("weeks with nothing left in them are hidden", o["clShift"]["weeks"] == 1 and o["clAll"]["weeks"] > 1,
              (o["clAll"]["weeks"], o["clShift"]["weeks"]))
        check("a version number shows that whole release", o["clVersion"]["secs"] == ["v1.0.0"] and o["clVersion"]["changes"] == 2, o["clVersion"])
        check("a match in a description opens it", o["clBody"]["changes"] == ["Viatica 1.0"] and o["clBody"]["open"], o["clBody"])
        check("clearing closes what the search opened", o["clCleared"]["secs"] == ["v1.0.1", "v1.0.0", "v0.1.0"]
              and not o["clCleared"]["anyOpen"], o["clCleared"])
        print("\na link straight to one project")
        check("that project's block is open on arrival", o["deepLink"]["open"], o["deepLink"])
        check("the fragment survives the search box's own URL rewrite", o["deepLink"]["hash"] == "#p-gamma", o["deepLink"])
        check("and it was closed to begin with, so the check means something", o["deepLink"]["closedByDefault"], o["deepLink"])

        print("\nwith script blocked")
        check("no box shows at all", o["noJsBox"] is False)

print(f"\n{'FAILED: ' + ', '.join(fails) if fails else 'search gate passes'}")
sys.exit(1 if fails else 0)
