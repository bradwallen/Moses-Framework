"""dashboard_server — the project dashboard as a web page.

WHY A SERVER AND NOT A GENERATED FILE: a written-out HTML page is a snapshot, and a snapshot of
project state is wrong within a day. This renders from the registry on every request, so the page and
the thing Moses edits can never disagree.

Runs as a systemd USER unit on localhost — no privilege to deploy, and nothing but the tunnel on the
same machine can reach it. Public access is Cloudflare's job: a tunnel route plus an Access policy,
exactly like code.viatica.travel.

Stdlib only, on purpose. This is a page about six projects; a dependency here would be a thing to
keep patched forever in exchange for nothing.
"""

from __future__ import annotations

import sys as _sys, pathlib as _pl  # noqa: E401
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent / "agent" / "lib"))
import moses_env as _env  # noqa: E402 — whose home, where Moses lives, the operator's settings
import html
import re
import json
import os
import sys
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
import projects
import changelog  # noqa: E402
import live_state  # noqa: E402
import moses_pages as mp  # noqa: E402

HOST, PORT = "127.0.0.1", 8791

CSS = """
:root{--bg:#0b0f19;--panel:#121828;--line:#222c44;--ink:#e8eefc;--dim:#8fa1c4;--mute:#5d6f95;
--go:#34d399;--warn:#fbbf24;--bad:#f87171;--accent:#60a5fa}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
padding:28px 18px 60px}
.wrap{max-width:60rem;margin:0 auto}
.wide .wrap,.wrap.wide{max-width:min(112rem,95vw)}
.pblock.targeted{outline:2px solid var(--acc,#4c8dff);outline-offset:3px;border-radius:10px}
/* A 2K widescreen was showing a 1312px column in a field of empty space (Brad, 2026-09-23).
   The wide pages — Projects, the changelog — now use the room; the narrow ones stay at 60rem
   because those are prose, and a 1700px line is miserable to read. */
h1{font-size:1.15rem;letter-spacing:.14em;text-transform:uppercase;margin:0 0 4px}
.sub{color:var(--mute);font-size:.82rem;margin:0 0 22px}
.p{background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:15px 17px;margin-bottom:12px}
.p.active{border-left:3px solid var(--go)}
.p.parked{border-left:3px solid var(--mute)}
.hd{display:flex;flex-wrap:wrap;gap:9px;align-items:baseline}
.rank{color:var(--mute);font-variant-numeric:tabular-nums;font-weight:700}
.nm{font-weight:700;font-size:1.02rem}
.tag{font-size:.7rem;letter-spacing:.08em;text-transform:uppercase;padding:2px 8px;
border-radius:999px;border:1px solid var(--line);color:var(--dim)}
.tag.active{color:var(--go);border-color:var(--go)}
.scope{color:var(--dim);margin:9px 0 0;font-size:.9rem}
.scope.none{color:var(--mute);font-style:italic}
.meta{margin-top:9px;font-size:.85rem;color:var(--dim)}
.meta b{color:var(--ink);font-weight:600}
.bar{display:flex;align-items:center;gap:10px;margin-top:10px}
.track{flex:1;height:7px;border-radius:99px;background:#1b2440;overflow:hidden;max-width:280px}
.fill{height:100%;background:var(--go)}
.count{font-size:.8rem;color:var(--dim);font-variant-numeric:tabular-nums}
.live{display:inline-block;margin-left:8px;padding:2px 9px;border-radius:999px;font-size:11px;letter-spacing:.02em;background:#0b2a1d;color:#4ade80;border:1px solid #14532d}
.due{font-size:.8rem;padding:2px 8px;border-radius:999px;border:1px solid var(--line);color:var(--dim)}
.due.soon{color:var(--warn);border-color:var(--warn)}
.due.over{color:var(--bad);border-color:var(--bad)}
.ms{margin:10px 0 0;padding:0;list-style:none;font-size:.87rem}
.ms li{color:var(--dim);padding:1px 0}
.ms li.done{color:var(--mute);text-decoration:line-through}
/* Ideas sit below the milestones and read quieter than them — smaller, dimmer, no tick marks,
   because a tick implies a thing that can be finished and these have not been started. */
.ideas-h{margin:12px 0 2px;font-size:.72rem;letter-spacing:.04em;text-transform:uppercase;color:var(--mute)}
.ideas{margin:0;padding:0 0 0 14px;list-style:'~  ';font-size:.82rem}
.ideas li{color:var(--mute);padding:1px 0}
.idea-note{color:var(--mute);opacity:.75;font-style:italic}
.blocked{margin-top:8px;font-size:.85rem;color:var(--warn)}
/* ── The board ──────────────────────────────────────────────────────────────
   Per project, and collapsible with the browser's own disclosure — no script is involved in opening
   or closing anything, and none should be. Active projects start open; parked start closed and say
   on their summary line where their work sits, so collapsing loses nothing. */
.pblock{background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:4px 15px 12px;margin-bottom:11px}
.pblock.active{border-left:3px solid var(--go)}
.pblock.parked{border-left:3px solid var(--mute)}
.pblock.inbox{border-left:3px solid var(--warn)}
.pblock>summary{display:flex;flex-wrap:wrap;gap:9px;align-items:center;cursor:pointer;
padding:9px 0;list-style:none}
.pblock>summary::-webkit-details-marker{display:none}
.pblock>summary::before{content:"▸";color:var(--mute);font-size:.8rem;transition:transform .12s}
.pblock[open]>summary::before{content:"▾"}
.pblock>summary:hover{color:var(--ink)}
.chip{font-size:.68rem;letter-spacing:.04em;padding:2px 8px;border-radius:999px;
border:1px solid var(--line);color:var(--dim);background:#0e1424}
.chip.building{color:var(--accent);border-color:#2b3f68}
.chip.ea{color:var(--warn);border-color:#5c4a12}
.chip.idea{color:var(--mute)}
.board{display:grid;grid-template-columns:repeat(5,minmax(150px,1fr));gap:10px;
margin:4px 0 2px;align-items:start}
.board.one{grid-template-columns:1fr}
.lane{background:#0e1424;border:1px solid var(--line);border-radius:10px;padding:9px 9px 11px;
min-width:0}
.lane>h2{font-size:.66rem;letter-spacing:.11em;text-transform:uppercase;color:var(--mute);
margin:0 0 8px;display:flex;justify-content:space-between;gap:6px;align-items:center}
.lane>h2 .n{font-variant-numeric:tabular-nums;color:var(--dim);background:#1b2440;
border-radius:999px;padding:1px 7px}
.lane.building{border-top:2px solid var(--accent)}
.lane.ea{border-top:2px solid var(--warn)}
.lane.done{border-top:2px solid var(--go)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:7px 9px;
margin-bottom:6px;font-size:.8rem;line-height:1.38;color:var(--dim)}
.lane.building .card{color:var(--ink);border-color:#2b3f68}
.lane.done .card{color:var(--mute)}
.card .ref{font:600 .68rem ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--mute);
letter-spacing:.02em;margin-right:3px}
.lane .empty{color:var(--mute);font-size:.75rem;font-style:italic;opacity:.6}
.more{color:var(--mute);font-size:.73rem;padding-top:2px}
@media(max-width:900px){.board{grid-template-columns:1fr;}}
@media(min-width:1600px){.board{gap:14px}
 .grid{grid-template-columns:repeat(auto-fill,minmax(260px,1fr))}
 .card{font-size:.9rem}}
.arch{margin-top:26px;color:var(--mute);font-size:.85rem}
.arch h2{font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;color:var(--mute);margin:0 0 7px}
.err{border-left:3px solid var(--bad);color:var(--bad)}
footer{margin-top:26px;color:var(--mute);font-size:.76rem}
nav{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 20px}
/* inline-block + a pinned line-height so every pill is the same height whatever the label: an
   inline <a> ignores vertical padding for line-box purposes, so the pills used to size themselves
   slightly differently from each other. */
nav a{display:inline-block;padding:6px 13px;border-radius:999px;border:1px solid var(--line);
background:var(--panel);color:var(--dim);text-decoration:none;font-size:.83rem;line-height:1.25}
/* HOVER MUST NOT IMPERSONATE ACTIVE. Hover used to set border-color to the accent — the same
   border that means "you are here" — so while the pointer was down the bar showed two current
   pages. Hover now lifts the background only; the accent border belongs to the active pill alone.
   The two rules also had EQUAL specificity, so which one won was decided by source order; .on is
   now pinned against :hover explicitly rather than by being written second. */
nav a:hover{color:var(--ink);background:#1b2440}
nav a.on,nav a.on:hover{color:var(--accent);border-color:var(--accent);background:#152239}
.tablet{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px;
margin-bottom:20px;overflow-x:auto}
.tablet pre{margin:0;color:var(--accent);font-size:.72rem;line-height:1.15;white-space:pre}
.cmd{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--accent);
border-radius:10px;padding:12px 15px;margin-bottom:9px}
.cmd .n{color:var(--accent);font-weight:800;font-variant-numeric:tabular-nums;margin-right:8px}
.cmd .t{font-weight:700}
.cmd .b{color:var(--dim);font-size:.88rem;margin-top:5px}
ul.b{margin:6px 0 0;padding-left:18px}
ul.b li{margin:3px 0}
.ref{color:var(--mute);font-style:italic}
code{background:#1b2440;padding:1px 5px;border-radius:4px;font-size:.85em}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:9px;margin-bottom:20px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:11px 13px}
.card .k{color:var(--mute);font-size:.7rem;letter-spacing:.1em;text-transform:uppercase}
.card .v{font-size:1.15rem;font-weight:700;margin-top:3px}
.who{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:11px 14px;margin-bottom:8px}
.who .nm{font-weight:700}
.who .kd{color:var(--mute);font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;margin-left:7px}
.who .ao{color:var(--dim);font-size:.86rem;margin-top:3px}
.diag{background:#080c16;border:1px solid var(--line);border-radius:10px;padding:13px;
overflow-x:auto;margin:9px 0 16px}
.diag pre{margin:0;font-size:.74rem;line-height:1.3;color:var(--dim);white-space:pre}
h2{font-size:.95rem;margin:22px 0 9px;color:var(--ink)}
.tbl{width:100%;border-collapse:collapse;margin:9px 0 14px;font-size:.85rem}
.tbl th{text-align:left;color:var(--mute);font-size:.7rem;letter-spacing:.08em;text-transform:uppercase;padding:6px 10px;border-bottom:1px solid var(--line)}
.tbl td{padding:6px 10px;border-bottom:1px solid var(--line);color:var(--dim);vertical-align:top}
.lead{color:var(--dim);max-width:46rem}

.card{border:1px solid var(--line,#2a2f3a);border-radius:10px;padding:14px 16px;margin:0 0 14px}
.card h2{margin:0 0 2px;font-size:1.05rem}
.card .up{margin-bottom:10px}
.sub{margin:-6px 0 16px}
table.hw{width:100%;border-collapse:collapse}
table.hw th{text-align:left;font-weight:600;padding:5px 10px 5px 0;white-space:nowrap;width:1%}
table.hw td{padding:5px 10px 5px 0;vertical-align:middle}
table.hw td:last-child{width:46%;white-space:nowrap}
.bar{display:inline-block;width:calc(100% - 52px);height:9px;border-radius:5px;
     background:rgba(127,127,127,.22);overflow:hidden;vertical-align:middle}
.bar span{display:block;height:100%}
.bar .ok{background:#3fa45b}.bar .warn{background:#c9922b}.bar .crit{background:#c0392b}
.spec{margin:2px 0 12px}
.spec summary{cursor:pointer;font-size:.86rem;opacity:.75;padding:2px 0}
.spec summary:hover{opacity:1}
.spec table.hw{margin-top:6px}
.live{color:#3fa45b;font-weight:600}
.warn-txt{color:#c9922b;font-weight:600}
.crit-txt{color:#c0392b;font-weight:600}
.pkgs{font-size:.8rem;line-height:1.6;opacity:.7;word-break:break-word;margin-top:4px}
.health{margin:2px 0 10px;font-size:.86rem;line-height:1.7}
.pct{display:inline-block;width:44px;text-align:right;font-variant-numeric:tabular-nums}

.clog{font-size:.72rem;color:var(--accent);text-decoration:none;border:1px solid var(--line);border-radius:999px;padding:2px 9px;margin-left:6px}
.clog:hover{background:var(--panel)}
.toc{font-size:.8rem;line-height:1.9;margin:0 0 18px}.toc a{color:var(--accent);margin-right:10px;text-decoration:none}
.rel{margin:0 0 22px}.rel h2{font-size:1.05rem;margin:20px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
.rel h2 .dim,.cdate{color:var(--mute);font-size:.75rem;font-weight:400}
.wk{font-size:.74rem;color:var(--dim);margin:14px 0 4px;text-transform:uppercase;letter-spacing:.06em}
.chg{padding:5px 0;font-size:.88rem;border-bottom:1px dashed var(--line)}.chg summary{cursor:pointer}
.cbody{white-space:pre-wrap;color:var(--dim);font-size:.82rem;margin:6px 0 8px 14px}
[hidden]{display:none!important}
.search{margin:0 0 14px;display:flex;gap:10px;align-items:center}
.search input{flex:1;max-width:440px;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--ink);font:inherit}
.search .dim{color:var(--mute);font-size:.8rem}
"""


def _esc(v) -> str:
    return html.escape(str(v or ""))


def _days(target: str):
    try:
        return (datetime.strptime(target, "%Y-%m-%d").date() - datetime.now().date()).days
    except (ValueError, TypeError):
        return None


TABLET = r'''
         .-~~~~~-.              M O S E S
        /         \
       |  I    VI  |            the standards every project follows
       |  II   VII |
       |  III  VIII|            ten of them, put into every prompt before
       |  IV   IX  |            the model writes a token — because asking
       |  V    X   |            whether it read the rules only ever proves
        \         /             that it can say yes
         '-.....-'
'''


def shell(title: str, current: str, body: str, refresh: int | None = None,
          wide: bool = False, search: str | None = None) -> str:
    """One frame for every page. The nav is written once so a new page cannot be orphaned.

    That was the intent from the start, but only two of the three pages went through here: Projects
    built its own document and rendered no nav at all, so you could navigate to it and not away from
    it, and the error frames were the same dead end. `refresh` exists so Projects can keep its
    auto-reload without that being a reason to hand-roll the document again.
    """
    tabs = [("/", "Moses"), ("/projects", "Projects"), ("/hardware", "Hardware"),
            ("/architecture", "Architecture")]
    nav = "".join(
        f'<a href="{h}" class="{"on" if h == current else ""}"'
        + (' aria-current="page"' if h == current else "")
        + f">{t}</a>"
        for h, t in tabs
    )
    # A META REFRESH IS A NAVIGATION, and that is the whole problem with it. Brad, 2026-09-09:
    # "when I'm looking at the content and the time hits it refreshes the whole page, puts me back at
    # the top and collapses what I was looking at." All three are unavoidable consequences of
    # reloading the document — scroll position and every open <details> belong to the page being
    # thrown away.
    #
    # So the page no longer navigates. It fetches itself, compares, and swaps the contents of #live
    # only when something actually changed, re-applying which projects were open. Nothing moves
    # under the reader, and an unchanged registry costs one request and zero repaints.
    #
    # AN ENHANCEMENT, NOT A DEPENDENCY (commandment 6). The document is complete and every
    # disclosure works before this runs; with script blocked you get a page that does not
    # auto-update, which is the old behavior minus the interruption. Everything is inside a
    # try/catch and a failed fetch is ignored — a dashboard must not break because a poll timed out.
    live_js = "" if not refresh else """<script>
(function(){
 try{
  var el=document.getElementById("live");
  if(!el||!window.fetch||!window.DOMParser) return;
  // Compare against what the SERVER last sent, not the page as the reader has changed it. Comparing
  // with the live DOM meant a search (which hides things) or simply opening a project made every tick
  // look like a change, so the whole page was swapped every interval for nothing.
  var last=el.innerHTML;
  // THE CLOCK IS NOT A CHANGE (2026-09-15). The page prints the time to the minute inside #live, so
  // every new minute looked like new content and the whole page was redrawn while nothing had changed,
  // cutting across a search in progress. The 07:30 test run caught it when a minute rolled over inside
  // its idle window. The time is compared out, and moved in place.
  var CLOCK=new RegExp('<span data-clock="">[^<]*</span>','g');
  var bare=function(h){return h.replace(CLOCK,"");};
  var openMap=function(root){var o={};var ds=root.querySelectorAll("details[id]");
    for(var i=0;i<ds.length;i++){o[ds[i].id]=ds[i].open;} return o;};
  setInterval(function(){
    fetch(location.href,{cache:"no-store",credentials:"same-origin"})
      .then(function(r){return r.ok?r.text():null})
      .then(function(html){
        if(!html) return;
        var next=new DOMParser().parseFromString(html,"text/html").getElementById("live");
        if(!next) return;
        if(bare(next.innerHTML)===bare(last)){       // nothing changed on the server: do not redraw
          var now=next.querySelector("[data-clock]"), shown=el.querySelector("[data-clock]");
          if(now&&shown&&shown.textContent!==now.textContent) shown.textContent=now.textContent;
          last=next.innerHTML;
          return;
        }
        last=next.innerHTML;
        var was=openMap(el);
        el.innerHTML=next.innerHTML;
        var ds=el.querySelectorAll("details[id]");
        for(var i=0;i<ds.length;i++){ if(ds[i].id in was){ ds[i].open=was[ds[i].id]; } }
        document.dispatchEvent(new Event("live:updated"));   // so a search in progress re-applies
      }).catch(function(){});
  }, REFRESH_MS);
 }catch(e){}
})();
</script>""".replace("REFRESH_MS", str(int(refresh) * 1000))
    # A LINK TO A PROJECT SHOULD OPEN THAT PROJECT (Brad, 2026-09-24). The workshop page links to
    # …/projects#p-<id>, and the browser jumped to the block and left it shut: a parked project renders
    # closed, so the reader landed on a collapsed summary with no sign anything had happened. This opens
    # the targeted block and brings it into view, on load and on any later hash change, and again after
    # the in-place refresh swaps #live (which rebuilds the element the first run opened).
    #
    # An enhancement, not a dependency (commandment 6): with script blocked the link still lands on the
    # right block — it is simply still collapsed, which is exactly today's behavior.
    target_js = """<script>
(function(){
 try{
  var reveal=function(){
    var id=(location.hash||"").replace(/^#/,"");
    if(!id) return;
    var el=document.getElementById(id);
    if(!el) return;
    if(el.tagName==="DETAILS") el.open=true;
    else { var d=el.closest&&el.closest("details"); if(d) d.open=true; }
    el.scrollIntoView({block:"start"});
    el.classList.add("targeted");
  };
  if(document.readyState!=="loading") reveal(); else document.addEventListener("DOMContentLoaded",reveal);
  window.addEventListener("hashchange",reveal);
  document.addEventListener("live:updated",reveal);
 }catch(e){}
})();
</script>"""

    # SEARCH (Brad, 2026-09-11). The box sits OUTSIDE #live on purpose: the in-place refresh replaces
    # #live wholesale, and a box inside it would lose what was typed every two minutes. It re-filters on
    # "live:updated", which the refresh fires after each swap. It is rendered `hidden` and shown by the
    # script, so with script blocked there is no box that looks like it works and does nothing
    # (commandment 6). Pages mark what is searchable: [data-sgroup] (a project, a release), its
    # [data-shead] (the heading), and [data-sitem] (a card, a change). Guarded in a real browser by
    # dashboard_search_test.py.
    search_html = "" if not search else (
        f'<div class="search"><input type="search" id="q" hidden autocomplete="off" '
        f'placeholder="{mp.esc(search)}" aria-label="{mp.esc(search)}"><span id="qn" class="dim"></span></div>')
    search_js = "" if not search else """<script>
(function(){
 try{
  var q=document.getElementById("q"), n=document.getElementById("qn");
  if(!q||!document.querySelectorAll) return;
  q.hidden=false;
  var saved=null;
  var live=function(){return document.getElementById("live");};
  var has=function(t,term){return (t||"").toLowerCase().indexOf(term)>=0;};
  // AN ITEM'S CODE MATCHES EXACTLY. As plain text, "v1" also finds V10 to V19 and every "v1.10", and
  // someone typing a code means that one item. Only where cards carry codes (the Projects board).
  var refOf=function(el){var r=el.querySelector&&el.querySelector(".ref");return r?r.textContent.toLowerCase():"";};
  function apply(){
    var root=live(); if(!root) return;
    var term=q.value.toLowerCase().trim(), hits=0, i, j;
    var code=/^[a-z]{1,3}-?\\d{1,5}$/.test(term)&&root.querySelector(".card .ref")?term.replace("-",""):null;
    if(term&&saved===null){ saved={}; var d0=root.querySelectorAll("details[id]");
      for(i=0;i<d0.length;i++){ saved[d0[i].id]=d0[i].open; } }
    // A project block is recognized as what it already is, so its markup stays exactly as the board
    // test pins it; the changelog marks its own releases and changes.
    var groups=root.querySelectorAll("[data-sgroup], details.pblock");
    for(i=0;i<groups.length;i++){
      var g=groups[i], head=g.querySelector("[data-shead]")||(g.tagName==="DETAILS"?g.querySelector("summary"):null);
      var items=g.querySelectorAll("[data-sitem], .card"), itemHits=[];
      var headHit=!code&&!!term&&!!head&&has(head.textContent,term);
      for(j=0;j<items.length;j++){ if(term&&(code?refOf(items[j])===code:has(items[j].textContent,term))) itemHits.push(items[j]); }
      var groupHit=!term||headHit||itemHits.length>0||(!code&&has(g.textContent,term));
      var narrow=!!term&&!headHit&&itemHits.length>0;   // only some cards match: show just those
      g.hidden=!groupHit;
      for(j=0;j<items.length;j++){
        var it=items[j]; it.hidden=narrow&&itemHits.indexOf(it)<0;
        if(narrow&&!it.hidden&&it.tagName==="DETAILS"&&!it.open){
          var sm=it.querySelector("summary");
          if(!sm||!has(sm.textContent,term)){ it.open=true; it.setAttribute("data-sopened",""); }  // matched in the description
        }
      }
      if(term&&groupHit&&g.tagName==="DETAILS") g.open=true;
      if(term&&groupHit) hits+=narrow?itemHits.length:(items.length||1);
    }
    if(!term){
      var so=root.querySelectorAll("[data-sopened]");
      for(i=0;i<so.length;i++){ so[i].open=false; so[i].removeAttribute("data-sopened"); }
      if(saved){ var d1=root.querySelectorAll("details[id]");
        for(i=0;i<d1.length;i++){ if(d1[i].id in saved){ d1[i].open=saved[d1[i].id]; } } }
      saved=null;
    }
    var wks=root.querySelectorAll(".wk");                  // a week heading with nothing left under it goes
    for(i=0;i<wks.length;i++){ var el=wks[i].nextElementSibling, vis=false;
      while(el&&!(el.classList&&el.classList.contains("wk"))){ if(el.hasAttribute&&el.hasAttribute("data-sitem")&&!el.hidden){vis=true;break;} el=el.nextElementSibling; }
      wks[i].hidden=!vis; }
    n.textContent=term?(hits+(hits===1?" match":" matches")):"";
    // KEEP THE FRAGMENT. This ran on load to put the search term in the address, and rewrote the URL
    // to the bare path — silently dropping #p-<id>, so a link from the workshop straight to a project
    // lost its target before anything could act on it (2026-09-24).
    try{ history.replaceState(null,"",(term?location.pathname+"?q="+encodeURIComponent(q.value):location.pathname)+location.hash); }catch(e){}
  }
  q.addEventListener("input",apply);
  document.addEventListener("live:updated",apply);
  try{ var init=new URLSearchParams(location.search).get("q"); if(init){ q.value=init; } }catch(e){}
  apply();
 }catch(e){}
})();
</script>"""
    meta_refresh = ""
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">{meta_refresh}
<title>{mp.esc(title)}</title><style>{CSS}</style>
<div class="wrap{" wide" if wide else ""}"><nav>{nav}</nav>{search_html}<main id="live">{body}</main>{live_js}{search_js}{target_js}
<footer>Read-only. Ask Moses to change anything — everything here is rendered from the files that own it.</footer>
</div></html>"""


def home_page() -> str:
    cmds = mp.commandments()
    cmd_html = "".join(
        f'<div class="cmd"><span class="n">{c["n"]}</span><span class="t">{mp.inline(c["title"])}</span>'
        + (f'<div class="b">{mp.inline(c["body"])}</div>' if c["body"] else "")
        + ("<ul class='b'>" + "".join(f"<li>{mp.inline(b)}</li>" for b in c["bullets"]) + "</ul>"
           if c["bullets"] else "")
        + "</div>"
        for c in cmds
    ) or '<div class="cmd">Could not read the commandments.</div>'

    vit = "".join(f'<div class="card"><div class="k">{mp.esc(k)}</div><div class="v">{mp.esc(v)}</div></div>'
                  for k, v in mp.vitals())

    who = "".join(
        f'<div class="who"><span class="nm">{mp.esc(r["name"])}</span>'
        f'<span class="kd">{mp.esc(r["kind"])}</span>'
        f'<div class="ao">{mp.esc(r["title"] or r["aoe"] or "")}</div></div>'
        for r in mp.roster()
    )

    return shell("Moses", "/", f"""
<h1>Moses</h1>
<p class="sub">The project of projects. <span data-clock>{datetime.now().strftime('%a %d %b %Y, %H:%M')}</span></p>
<div class="tablet"><pre>{mp.esc(TABLET)}</pre></div>
<p class="lead">Moses keeps the standards, the memory, and the list of what is being built. He is not
a chatbot with opinions — he is the thing that makes a rule survive the session it was agreed in.</p>
<div class="grid">{vit}</div>
<h2>The Commandments</h2>
{cmd_html}
<h2>Who is accountable for what</h2>
{who}
""")


def architecture_page() -> str:
    secs = mp.architecture_sections()
    out = []
    for sec in secs:
        tag = "h1" if sec["level"] == 1 else "h2"
        out.append(f'<{tag}>{mp.esc(sec["title"])}</{tag}>')
        if sec["prose"]:
            out.append(f'<p class="lead">{mp.inline(" ".join(sec["prose"][:3]))}</p>')
        rows = sec.get("tables") or []
        if rows:
            head, body = rows[0], rows[1:]
            th = "".join(f"<th>{mp.inline(c)}</th>" for c in head)
            tb = "".join("<tr>" + "".join(f"<td>{mp.inline(c)}</td>" for c in r) + "</tr>" for r in body)
            out.append(f'<table class="tbl"><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table>')
        for b in sec["blocks"]:
            out.append(f'<div class="diag"><pre>{mp.esc(b)}</pre></div>')
    body = "".join(out) or "<p>Could not read the architecture document.</p>"
    return shell("Architecture · Moses", "/architecture", f"""
<p class="sub">Rendered from the same document the weekly architecture check validates against the
running system — so a diagram here cannot quietly stop being true.</p>{body}""")


# ── The board ────────────────────────────────────────────────────────────────
# Brad, 2026-09-09: "each Project contains its own cards and the Project is collapsible."
#
# So the board is PER PROJECT rather than one pool with project labels on the cards. The pooled
# version answered "what is being built" across the estate but buried the thing he actually reads —
# where ONE project stands — under nine others' cards.
#
# COLLAPSING IS <details>, AND NO SCRIPT OPENS OR CLOSES ANYTHING. The browser already does
# disclosure: it works with the keyboard, it prints, and if anything here were ever to break, a
# <details> falls open rather than hiding the contents. Active projects start open, parked ones
# start closed — the summary line carries the lane counts so a closed project still says where its
# work sits.
#
# The page DOES carry one script, added later the same day: the auto-refresh used to be a meta
# refresh, which is a navigation, and so threw away scroll position and every open panel every two
# minutes. That script updates #live in place and puts back what was open. It is an enhancement —
# strip it and the page is complete, which the board test proves by doing exactly that.
LANES = [("idea", "Ideas"), ("next", "Next"), ("building", "Building"),
         ("ea", "Early access"), ("done", "Done")]
# Done grows forever and nobody scrolls it. Enough to see momentum, then a count.
DONE_SHOWN = 6


def _card(title: str, note: str = "", ref: str = "") -> str:
    full = f"{title} — {note}" if note else title
    if ref:
        full = f"{ref} · {full}"
    body = title if len(title) <= 150 else title[:149] + "…"
    # The item's code (V14), first and quiet: there to quote in a conversation instead of the title,
    # and inside the card so the page's search finds it.
    code = f'<span class="ref">{_esc(ref)}</span> ' if ref else ""
    return f'<div class="card" title="{_esc(full)}">{code}{_esc(body)}</div>'


def _lanes_for(p: dict) -> dict:
    cols = {k: [] for k, _ in LANES}
    for m in p.get("milestones") or []:
        cols[projects.milestone_lane(m)].append(m)
    cols["idea"] = list(p.get("ideas") or [])
    return cols


def _board_html(cols: dict) -> str:
    out = ['<div class="board">']
    for key, label in LANES:
        items = cols[key]
        shown, extra = items, ""
        if key == "done" and len(items) > DONE_SHOWN:
            shown = items[-DONE_SHOWN:]          # newest last in the registry
            extra = f'<div class="more">+{len(items) - DONE_SHOWN} more finished</div>'
        cards = "".join(_card(i.get("title", ""), i.get("note", ""), i.get("ref", "")) for i in shown)
        out.append(f'<div class="lane {key}"><h2>{_esc(label)}<span class="n">{len(items)}</span></h2>')
        out.append(cards if cards else '<div class="empty">nothing here</div>')
        out.append(extra + "</div>")
    out.append("</div>")
    return "".join(out)


def _summary_html(p: dict, cols: dict) -> str:
    """The always-visible line. Must say enough that collapsing it loses nothing important."""
    ms = p.get("milestones") or []
    done = sum(1 for m in ms if m.get("done"))
    pct = round(100 * done / len(ms)) if ms else 0
    status = p.get("status", "?")
    bits = ["<summary>"]
    rank = p.get("rank", 99)
    if rank < projects.ARCHIVE_RANK:
        bits.append(f'<span class="rank">#{rank}</span>')
    bits.append(f'<span class="nm">{_esc(p.get("name"))}</span>')
    bits.append(f'<span class="tag{" active" if status == "active" else ""}">{_esc(status)}</span>')
    bits.append(f'<span class="tag">{_esc(p.get("stage"))}</span>')
    # Where the work sits, on the line itself — a collapsed project still answers the question the
    # board exists to answer.
    for key, label in LANES:
        n = len(cols[key])
        if n and key != "done":
            bits.append(f'<span class="chip {key}">{n} {_esc(label.lower())}</span>')
    if p.get("id") == "viatica":
        live = live_state.summary()
        if live:
            bits.append(f'<span class="live">{_esc(live)}</span>')
    left = _days(p.get("target", ""))
    if left is not None:
        cls = "over" if left < 0 else "soon" if left <= 7 else ""
        when = (f"{left} days" if left > 0 else "today" if left == 0 else f"{abs(left)} days over")
        bits.append(f'<span class="due {cls}">{_esc(p["target"])} · {when}</span>')
    if ms:
        bits.append(f'<div class="bar"><div class="track"><div class="fill" style="width:{pct}%">'
                    f'</div></div><span class="count">{done}/{len(ms)}</span></div>')
    bits.append(f'<a class="clog" href="/changelog/{_esc(p.get("id", ""))}">changelog</a>')
    bits.append("</summary>")
    return "".join(bits)


def _project_block(p: dict) -> str:
    cols = _lanes_for(p)
    is_open = " open" if p.get("status") == "active" else ""
    inner = _board_html(cols)
    for b in p.get("blockers") or []:
        inner += f'<p class="blocked">⚠ Blocked: {_esc(b)}</p>'
    if p.get("next"):
        inner += f'<p class="meta"><b>Next:</b> {_esc(p["next"])}</p>'
    # The id is what lets an in-place update put back what was open — see shell().
    return (f'<details id="p-{_esc(p.get("id", ""))}" '
            f'class="pblock {_esc(p.get("status", ""))}"{is_open}>'
            f'{_summary_html(p, cols)}{inner}</details>')


def _inbox_html(items: list) -> str:
    """Captured with no project named. First on the page, because an idea with no home is the one
    thing here that needs a decision from Brad rather than from Moses."""
    if not items:
        return ""
    cards = "".join(_card(i.get("title", ""), i.get("note", ""), i.get("ref", "")) for i in items)
    return (f'<details id="p-inbox" class="pblock inbox" open><summary><span class="nm">Unfiled</span>'
            f'<span class="tag">inbox</span>'
            f'<span class="chip idea">{len(items)} waiting for a project</span></summary>'
            f'<div class="board one"><div class="lane idea">{cards}</div></div></details>')


def page() -> str:
    try:
        d = projects.load()
        ps = sorted(d["projects"], key=lambda x: (x.get("rank", 99), x.get("name", "")))
    except Exception as e:
        # The page still renders and says what is wrong, rather than returning a blank 500 that
        # looks identical to the tunnel being down — and it keeps the nav, because a broken
        # registry is exactly when you want to click somewhere else.
        return shell("Projects · Moses", "/projects",
                     f'<h1>Projects</h1>'
                     f'<div class="p err">Could not read the registry: {_esc(e)}</div>')

    live = [p for p in ps if p.get("rank", 99) < projects.ARCHIVE_RANK]
    arch = [p for p in ps if p.get("rank", 99) >= projects.ARCHIVE_RANK]

    # NO "WORKING ON X — N PARKED" BANNER. It earned its place when this page was a list, where
    # nothing else said which projects were live. The board says it structurally: the active ones are
    # the blocks that are open, the parked ones are the collapsed ones under them. Brad, 2026-09-09:
    # "this feels redundant now." A line that restates what the reader can already see is a line they
    # learn to skip, and then they skip the one above it too.
    body = _inbox_html(d.get("inbox") or []) + "".join(_project_block(p) for p in live)
    archive = ""
    if arch:
        rows = "".join(f'<div data-sgroup>{_esc(p.get("name"))} — {_esc(p.get("status"))} '
                       f'<a class="clog" href="/changelog/{_esc(p.get("id", ""))}">changelog</a></div>' for p in arch)
        archive = f'<div class="arch"><h2>Not in the running order</h2>{rows}</div>'

    # Re-rendered from the registry on every request, so this cannot drift from what Moses knows.
    return shell("Projects · Moses", "/projects", f"""
<h1>Projects</h1>
<p class="sub">Moses keeps this list. <span data-clock>{datetime.now().strftime('%a %d %b %Y, %H:%M')}</span></p>
{body}{archive}""", refresh=120, wide=True, search="Search projects, cards and notes")


# ── Changelog ────────────────────────────────────────────────────────────────────────────────────
# Brad, 2026-09-11: a changelog link on every project, back past 1.0. Built by lib changelog.py from
# each project's own git history on every request (cached by HEAD), so it cannot drift from what shipped.

def _change_html(c: dict) -> str:
    meta = f'<span class="cdate">{_esc(c["date"])} · {_esc(c["short"])}</span>'
    if c.get("body"):
        return (f'<details class="chg" data-sitem><summary>{_esc(c["subject"])} {meta}</summary>'
                f'<div class="cbody">{_esc(c["body"])}</div></details>')
    return f'<div class="chg" data-sitem>{_esc(c["subject"])} {meta}</div>'


def _changes_html(changes: list) -> str:
    """A long stretch at one version — Viatica's two months at 0.1.0 — is grouped by week, or nobody
    could find anything in it."""
    if not changes:
        return '<div class="chg">no changes recorded</div>'
    day = lambda c: datetime.strptime(c["date"], "%Y-%m-%d").date()
    if (day(changes[0]) - day(changes[-1])).days <= 14:
        return "".join(_change_html(c) for c in changes)
    out, week = [], None
    for c in changes:
        monday = (day(c) - timedelta(days=day(c).weekday())).isoformat()
        if monday != week:
            out.append(f'<h3 class="wk">Week of {monday}</h3>')
            week = monday
        out.append(_change_html(c))
    return "".join(out)


def changelog_page(pid: str) -> str | None:
    try:
        d = projects.load()
    except Exception as e:
        return shell("Changelog · Moses", "/projects",
                     f'<h1>Changelog</h1><div class="p err">Could not read the registry: {_esc(e)}</div>')
    p = next((x for x in d.get("projects", []) if x.get("id") == pid), None)
    if p is None:
        return None
    name = p.get("name") or pid
    head = f'<p class="sub"><a href="/projects">← Projects</a></p><h1>{_esc(name)} — changelog</h1>'
    log = changelog.build(p.get("repo"))
    plural = lambda n: f"{n} change{'' if n == 1 else 's'}"

    if log["kind"] == "none":
        done = [m for m in p.get("milestones") or [] if m.get("done")]
        rows = "".join(f'<div class="chg">{_esc(m.get("ref", ""))} {_esc(m.get("title"))}</div>' for m in done)
        body = head + f'<div class="p">{_esc(log["why"])}</div>'
        if rows:
            body += (f'<section class="rel" data-sgroup><h2 data-shead>Finished milestones <span class="dim">recorded without dates'
                     f'</span></h2>{rows}</section>')
    elif log["kind"] == "days":
        groups = log["groups"]
        body = head + (f'<p class="sub">{plural(sum(len(g["changes"]) for g in groups))}, read from the git '
                       f'history on Reserve. This project has no version numbers, so changes are grouped by day.</p>')
        body += "".join(f'<section class="rel" data-sgroup><h2 data-shead id="d{_esc(g["date"])}">{_esc(g["date"])} '
                        f'<span class="dim">{plural(len(g["changes"]))}</span></h2>'
                        f'{"".join(_change_html(c) for c in g["changes"])}</section>' for g in groups)
    else:
        rels = log["releases"]
        anchor = lambda x: f'v{x["version"]}' if x["version"] else "v-early"
        label = lambda x: x["version"] or "before a version"
        toc = "".join(f'<a href="#{_esc(anchor(x))}">{_esc(label(x))}</a>' for x in rels)
        body = head + (f'<p class="sub">{plural(sum(len(x["changes"]) for x in rels))} across {len(rels)} versions, '
                       f'read from the git history on Reserve. Each change is listed under the version the code '
                       f'carried when it was committed; a long stretch at one version is grouped by week.</p>'
                       f'<div class="toc">{toc}</div>')
        for x in rels:
            note = ""   # no "tagged" label: tags are not used (Brad, 2026-09-11) — the version is the record
            if x["version"] and x["version"].startswith("0."):
                note += " · before 1.0 — built, not yet released"
            body += (f'<section class="rel" data-sgroup><h2 data-shead id="{_esc(anchor(x))}">{_esc(label(x))} <span class="dim">'
                     f'{_esc(x["date"])} · {plural(len(x["changes"]))}{note}</span></h2>'
                     f'{_changes_html(x["changes"])}</section>')
    return shell(f"{name} · Changelog · Moses", "/projects", body, wide=True,
                 search="Search changes, versions and descriptions")


HARDWARE_FILE = os.environ.get("MOSES_STATE", str(_env.STATE)) + "/hardware.json"


def _bar(pct: int, warn: int = 75, crit: int = 90) -> str:
    """A usage bar. Color is a hint, the number is the fact — both are shown."""
    cls = "crit" if pct >= crit else ("warn" if pct >= warn else "ok")
    return (f'<div class="bar"><span class="{cls}" style="width:{min(100, max(0, pct))}%"></span></div>'
            f'<span class="pct">{pct}%</span>')


def _gb(kb: int) -> str:
    return f"{kb / 1024 / 1024:.1f} GB" if kb >= 1024 * 1024 else f"{kb / 1024:.0f} MB"


def _updates_block(u: dict) -> str:
    """Pending updates: the number, then the two facts that make the number mean something.

    Brad: "just show the number but make it expandable... it's a lot of noise, especially at 53."
    So the count is the summary and the package list is behind a disclosure — but the count alone is
    genuinely noise, so the line beside it says how many are SECURITY and whether the machine
    installs those by itself. 53 pending with zero security and a working unattended-upgrades is
    reassurance; 254 pending with 70 security and no unattended-upgrades is a job.

    Staleness is shown because `apt list --upgradable` reads a local cache. A count from a three-week
    old cache is a floor, not a total, and presenting it as current would be the same lie as any
    other stale reading on this page.
    """
    if not u or not u.get("count"):
        return ""
    n, sec = u["count"], u.get("security", 0)
    cls = "crit-txt" if sec else "dim"
    head = f'{n} update{"" if n == 1 else "s"} pending'
    if sec:
        head += f' · <span class="{cls}">{sec} security</span>'
    else:
        head += ' · <span class="dim">none security</span>'

    notes = []
    auto = u.get("auto", "")
    if auto == "installs security automatically":
        when = f' <span class="dim">(last ran {mp.esc(u["auto_last"])})</span>' if u.get("auto_last") else ""
        notes.append(f'<span class="live">✓ security updates install automatically</span>{when}')
    elif auto:
        # No automation is the finding, not the count. 70 security updates that nothing will ever
        # apply is a different situation from 70 that get applied tonight.
        notes.append(f'<span class="warn-txt">⚠ unattended-upgrades {mp.esc(auto)}</span> '
                     f'<span class="dim">— nothing installs these on its own</span>')
    age = u.get("lists_age_days")
    if isinstance(age, int) and age >= 7:
        notes.append(f'<span class="warn-txt">⚠ package lists last refreshed {age} days ago</span> '
                     f'<span class="dim">— the real count is likely higher</span>')

    pkgs = ", ".join(u.get("packages") or [])
    return (f'<details class="spec"><summary>{head}</summary>'
            + (f'<div class="health">{"<br>".join(notes)}</div>' if notes else "")
            + f'<div class="pkgs">{mp.esc(pkgs)}</div></details>')


def _health_strip(health: dict) -> str:
    """Only what is wrong or pending. Renders nothing at all when there is nothing to say.

    NO GREEN TICKS. A panel that lists twenty things being fine trains you to stop reading it, and
    then the one red line is missed — which is the failure this whole estate keeps rediscovering.
    Silence here means healthy; that is the contract.
    """
    out = []
    failed = health.get("failed_units") or []
    if failed:
        out.append(f'<span class="crit-txt">✗ {len(failed)} failed unit'
                   f'{"" if len(failed) == 1 else "s"}</span> '
                   f'<span class="dim">{mp.esc(", ".join(failed[:3]))}</span>')
    if health.get("unchecked"):
        # Could not look is not the same as nothing failed — a silent strip would claim the second.
        out.append(f'<span class="warn-txt">? not checked</span> '
                   f'<span class="dim">{mp.esc(", ".join(health["unchecked"]))}</span>')
    if health.get("readonly_mounts"):
        # A filesystem that flipped read-only is a disk problem wearing a mount option.
        out.append(f'<span class="crit-txt">✗ mounted read-only</span> '
                   f'<span class="dim">{mp.esc(", ".join(health["readonly_mounts"]))}</span>')
    if health.get("reboot_required"):
        why = ", ".join(health.get("reboot_for") or [])
        out.append(f'<span class="warn-txt">⟳ reboot pending</span>'
                   + (f' <span class="dim">{mp.esc(why)}</span>' if why else ""))
    return f'<div class="health">{"<br>".join(out)}</div>' if out else ""


def _pci_desc_for(spec: dict, short_addr: str) -> str:
    """The controller description for a PCI address, from the lspci lines already collected."""
    lines = (spec.get("pci") or {}).get("net") or []
    for entry in lines:
        # Entries are {addr, desc}. Matching on the address is the whole point: on a three-NIC board
        # guessing by position would attach the wrong part name to the live link.
        if isinstance(entry, dict) and short_addr and entry.get("addr") == short_addr:
            return entry.get("desc", "")
    if len(lines) == 1 and isinstance(lines[0], dict):
        return lines[0].get("desc", "")
    return ""


def _spec_block(spec: dict) -> str:
    """What the machine IS, above what it is doing.

    Every value comes from the host's own reporting — /proc, /sys, lscpu, lspci, lsblk. The graphics
    line is where the Intel microarchitecture comes from ("CoffeeLake-S GT2"), because the PCI ID
    database names it; there is deliberately no CPUID-to-codename table here, since a table I
    maintain is one that goes stale and starts lying about hardware that cannot answer back.

    RAM type and speed are absent on purpose: they live behind `dmidecode`, which needs root. Blank
    beats a guess.
    """
    if not spec:
        return ""
    cpu, board, pci = spec.get("cpu") or {}, spec.get("board") or {}, spec.get("pci") or {}
    bits = []

    if cpu.get("model"):
        cores = cpu.get("cores_per_socket")
        threads = cpu.get("threads")
        # Cores and threads separately: 8c/8t and 4c/8t behave very differently under the same load.
        shape = f"{cores}c / {threads}t" if cores and threads else (f"{threads} threads" if threads else "")
        speed = ""
        if cpu.get("max_mhz"):
            speed = f"up to {float(cpu['max_mhz']) / 1000:.2f} GHz"
            if cpu.get("now_mhz"):
                speed += f", now {cpu['now_mhz'] / 1000:.2f} GHz"
        extra = " · ".join(x for x in (shape, speed, cpu.get("l3", "") and f"L3 {cpu['l3']}") if x)
        bits.append(("Processor", f"{mp.esc(cpu['model'])}"
                                  + (f'<br><span class="dim">{mp.esc(extra)}</span>' if extra else "")))
        # Family/model/stepping is what you actually need to look a part up. Shown raw, not decoded.
        ids = " ".join(f"{lbl} {cpu[k]}" for k, lbl in
                       (("family", "family"), ("model_id", "model"), ("stepping", "stepping"))
                       if cpu.get(k))
        if ids:
            bits.append(("CPU id", f'<span class="dim">{mp.esc(cpu.get("vendor", ""))} {mp.esc(ids)} '
                                   f'· {mp.esc(cpu.get("arch", ""))}</span>'))
    for line in (pci.get("gpu") or [])[:2]:
        bits.append(("Graphics", mp.esc(line)))

    # NETWORK: WHICH ONE IS ACTUALLY PLUGGED IN. Listing three controllers off the PCI bus says
    # nothing about which is carrying traffic — the answer here is one of three, and that is the only
    # part anyone needs at a glance. Each interface is matched back to its controller by PCI address,
    # so the row names the part AND its link.
    nics = spec.get("net") or []
    if nics:
        rows_net = []
        for i, n in enumerate(nics):
            desc = ""
            if n.get("pci"):
                # lspci prints "01:00.0"; sysfs gives "0000:01:00.0". Match on the short form.
                # sysfs gives "0000:01:00.0"; lspci prints "01:00.0".
                short = n["pci"].split(":", 1)[1] if n["pci"].count(":") > 1 else n["pci"]
                desc = _pci_desc_for(spec, short)
            up = n.get("state") == "up"
            if up and n.get("mbps"):
                speed = f'{n["mbps"] // 1000} Gb/s' if n["mbps"] >= 1000 else f'{n["mbps"]} Mb/s'
                link = (f'<span class="live">● connected · {mp.esc(speed)}'
                        f'{" " + mp.esc(n.get("duplex", "")) if n.get("duplex") else ""}</span>')
            elif n.get("virtual"):
                link = '<span class="dim">virtual</span>'
            else:
                link = '<span class="dim">not connected</span>'
            rows_net.append(f'<b>{mp.esc(n["name"])}</b> — {link}'
                            + (f'<br><span class="dim">{mp.esc(desc)}</span>' if desc else ""))
        bits.append((f"Network ({sum(1 for n in nics if n.get('state') == 'up' and n.get('mbps'))} live)",
                     "<br>".join(rows_net)))

    if board:
        made = " ".join(x for x in (board.get("board_vendor"), board.get("board_name")) if x)
        bios = board.get("bios_version")
        if bios and board.get("bios_date"):
            bios += f" ({board['bios_date']})"
        bits.append(("Board", mp.esc(made or board.get("product_name", ""))
                              + (f'<br><span class="dim">BIOS {mp.esc(bios)}</span>' if bios else "")))
    if spec.get("os"):
        bits.append(("System", f'{mp.esc(spec["os"])}<br><span class="dim">kernel '
                               f'{mp.esc(spec.get("kernel", ""))}</span>'))
    memory = spec.get("memory") or {}
    if memory.get("slots"):
        # Slots are shown INCLUDING the empty ones, because "how much room is left" is usually the
        # actual question when someone looks up their RAM.
        listed = "<br>".join(
            (f'{mp.esc(m.get("slot", "?"))} — <span class="dim">empty</span>' if m.get("empty") else
             f'{mp.esc(m.get("slot", "?"))} — {mp.esc(m.get("size", ""))} '
             f'{mp.esc(m.get("type", ""))} {mp.esc(m.get("speed", ""))}'
             + (f' <span class="dim">(rated {mp.esc(m.get("rated", ""))})</span>'
                if m.get("rated") and m.get("rated") != m.get("speed") else "")
             + (f' <span class="dim">{mp.esc(m.get("maker", ""))} {mp.esc(m.get("part", ""))}</span>'
                if m.get("maker") or m.get("part") else ""))
            for m in memory["slots"])
        bits.append((f'Memory ({memory.get("filled", 0)} of {memory.get("total_slots", 0)} slots)',
                     listed))

    disks = spec.get("disks") or []
    if disks:
        listed = "<br>".join(
            f'{mp.esc(d["name"])} — {mp.esc(d["model"])} '
            f'<span class="dim">{mp.esc(d.get("size", ""))} {mp.esc(d.get("kind", ""))} '
            f'{mp.esc(d.get("bus", ""))}</span>' for d in disks)
        bits.append((f"Drives ({len(disks)})", listed))

    if not bits:
        return ""
    rows = "".join(f'<tr><th>{k}</th><td colspan="2">{v}</td></tr>' for k, v in bits)
    # <details> so the spec is one click away and the usage numbers stay the thing you see first —
    # this page is for watching resources, not for reading a spec sheet every time.
    return (f'<details class="spec"><summary>Hardware</summary>'
            f'<table class="hw">{rows}</table></details>')


def _host_card(h: dict) -> str:
    name = mp.esc(h.get("name", "?"))
    if not h.get("ok"):
        # UNREACHABLE IS SHOWN, LOUDLY. A host that cannot be read must never render as a host using
        # no resources — that is the same "couldn't look renders as nothing wrong" this estate keeps
        # tripping over.
        return (f'<div class="card"><h2>{name}</h2>'
                f'<div class="p err">Could not read this host — {mp.esc(str(h.get("reason", "no reason recorded")))}</div>'
                f'</div>')

    cores = h.get("cores") or 0
    load = h.get("load") or [0, 0, 0]
    # Load is only meaningful against core count, so it is shown as a percentage of capacity too.
    load_pct = round(load[0] / cores * 100) if cores else 0
    mem, swap = h.get("mem") or {}, h.get("swap") or {}
    mem_pct = round(mem.get("used_kb", 0) / mem["total_kb"] * 100) if mem.get("total_kb") else 0
    sw_pct = round(swap.get("used_kb", 0) / swap["total_kb"] * 100) if swap.get("total_kb") else 0

    rows = [f'<tr><th>CPU load</th><td>{load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f} '
            f'<span class="dim">over {cores} cores</span></td><td>{_bar(load_pct)}</td></tr>',
            f'<tr><th>Memory</th><td>{_gb(mem.get("used_kb", 0))} of {_gb(mem.get("total_kb", 0))}</td>'
            f'<td>{_bar(mem_pct)}</td></tr>']
    if swap.get("total_kb"):
        rows.append(f'<tr><th>Swap</th><td>{_gb(swap.get("used_kb", 0))} of {_gb(swap["total_kb"])}</td>'
                    f'<td>{_bar(sw_pct, 25, 60)}</td></tr>')
    for d in h.get("disks", []):
        rows.append(f'<tr><th>{mp.esc(d["mount"])}</th>'
                    f'<td>{_gb(d["used"] // 1024)} of {_gb(d["bytes"] // 1024)}</td>'
                    f'<td>{_bar(d["pct"])}</td></tr>')
    # Every sensor, named. One number labeled "temperature" is a guess about which one matters.
    for t in h.get("temps", []):
        c = t.get("celsius", 0)
        rows.append(f'<tr><th>{mp.esc(t.get("name", "sensor"))}</th>'
                    f'<td>{c:.1f} &deg;C</td><td>{_bar(round(min(100, c)), 70, 85)}</td></tr>')

    return (f'<div class="card"><h2>{name} <span class="dim">{mp.esc(h.get("model", ""))}</span></h2>'
            f'<div class="dim up">{mp.esc(h.get("uptime", ""))}</div>'
            f'{_health_strip((h.get("spec") or {}).get("health") or {})}'
            f'{_updates_block((h.get("spec") or {}).get("updates") or {})}'
            f'{_spec_block(h.get("spec") or {})}'
            f'<table class="hw">{"".join(rows)}</table></div>')


def hardware_page() -> str:
    """Resource usage for the two machines Brad runs.

    Rendered from a file a timer writes, not collected here. Reserve is local and would be cheap,
    but Labs is a Pi over Tailscale and an SSH round trip inside a page render would make every view
    wait on the network — and hang the page outright when the Pi is asleep. The age of the reading
    is shown, because a stale number presented as current is worse than no number.
    """
    try:
        with open(HARDWARE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:
        return shell("Hardware", "/hardware",
                     f'<h1>Hardware</h1><div class="p err">No readings yet — '
                     f'{html.escape(type(e).__name__)}. The collector runs every 5 minutes; '
                     f'this is what it looks like before the first one lands.</div>')

    age = ""
    try:
        secs = int(time.time() - os.path.getmtime(HARDWARE_FILE))
        age = f"{secs // 60}m {secs % 60}s ago" if secs >= 60 else f"{secs}s ago"
        if secs > 1800:
            age += " — STALE, the collector may not be running"
    except OSError:
        age = "age unknown"

    cards = "".join(_host_card(h) for h in data.get("hosts", []))
    return shell("Hardware", "/hardware",
                 f'<h1>Hardware</h1><div class="dim sub">Read {mp.esc(age)}. '
                 f'Collected on a timer, not when you loaded this page.</div>{cards}',
                 refresh=60)


ROUTES = {
    "/": lambda: home_page(),
    "/projects": lambda: page(),
    "/hardware": lambda: hardware_page(),
    "/architecture": lambda: architecture_page(),
}

# /index.html is the same page as /, and resolving it to / here rather than giving it its own route
# entry means the nav highlights Moses on it — including on the error frame, which is built from the
# requested path and would otherwise light no pill at all.
ALIASES = {"/index.html": "/"}


def render(path: str) -> str | None:
    """The page for a path, or None when there is no such page.

    Lives outside the handler so the nav guard can render every route the same way a real request
    does — including the error frame, which is a page like any other and has to be one you can
    navigate away from.
    """
    path = path.split("?")[0]
    path = ALIASES.get(path, path)
    if path.startswith("/changelog/"):
        # One page per project. The id must be a plain registry id — never a path — or it is a 404.
        pid = path[len("/changelog/"):]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", pid):
            return None
        try:
            return changelog_page(pid)
        except Exception as e:
            return shell("Moses", "/projects",
                         f'<h1>Changelog</h1><div class="p err">This page could not render: '
                         f'{html.escape(type(e).__name__ + ": " + str(e))}</div>')
    route = ROUTES.get(path)
    if route is None:
        return None
    try:
        return route()
    except Exception as e:
        # A page is decoration on top of files that may be mid-edit. It says what broke instead
        # of returning a blank 500, which looks identical to the tunnel being down.
        return shell("Moses", path,
                     f'<h1>Moses</h1><div class="p err">This page could not render: '
                     f'{html.escape(type(e).__name__ + ": " + str(e))}</div>')


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = render(self.path)
        if body is None:
            self.send_error(404, "Nothing here")
            return
        out = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, fmt, *args):
        # One line per request on stdout is enough; the default writes to stderr and journald then
        # files every page view as an error.
        sys.stdout.write("dashboard: %s\n" % (fmt % args))
        sys.stdout.flush()


if __name__ == "__main__":
    print(f"dashboard: serving projects on http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
