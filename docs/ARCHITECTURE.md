# Moses — architecture

Commandment #5: every project carries text diagrams that render anywhere. Moses is a project too.
Last verified against running state: 2026-08-06.

Moses is the project-of-projects layer over Brad's portfolio: it holds the commandments, the state of
every project, and the roster of who is accountable for what. It spans three hosts and is reached
three ways.

---

## 1. System overview — where everything runs

```
  BRAD                                RESERVE (devbox, 192.168.1.10 / tailscale 100.64.0.1)
  ────                                ──────────────────────────────────────────────────────
                                      ┌──────────────────────────────────────────────────┐
  Claude app  ──── MCP ─────┐         │  moses-mcp.service        (user unit, brad)       │
  (iOS/desktop)             │         │    100.64.0.1:8765   profile=full   26 tools    │
   pays for the thinking    │         │                                                   │
                            │         │  moses-mcp-public.service (user unit, brad)       │
  Slack ──── Socket Mode ───┼─────────┼─▶  127.0.0.1:8766      profile=full               │
   @Moses, /birdeye         │         │                                                   │
                            │         │  moses.service        (USER unit, brad)           │
                            │         │    the ONLY listener. Ran as root until 2026-09-02;│
                            │         │    now runs the SOURCE directly — no /opt copy,    │
                            │         │    deploy is `systemctl --user restart moses`.     │
  Terminal ──── ssh ────────┼─────────┼─▶  Slack listener — claude CLI where allowed      │
   moses CLI                │         │                                                   │
                            │         │  birdeye / birdeye-report (root)                  │
                            │         │    ops reporting + long-job handoff               │
                            │         │                                                   │
                            │         │  moses-dashboard.service  (user unit, brad)       │
  Browser ──── HTTPS ───────┼─────────┼─▶  127.0.0.1:8791   read-only web dashboard      │
   projects.example.com  │         │    /home/brad/Projects/moses/mcp/dashboard_server.py       │
                            │         │                                                   │
                            │         │  3dcarstuff-dashboard.service (user unit, brad)   │
  Browser ──── HTTPS ───────┼─────────┼─▶  100.64.0.1:8001  business dashboard          │
   dashboard.example.com       │         │    expenses, revenue, Schedule C categories       │
                            │         │                                                   │
                            │         │  code-server              (user)                  │
  Browser ──── HTTPS ───────┼─────────┼─▶  100.64.0.1:8080                              │
   code.example.com      │         │                                                   │
                            │         │                                                   │
                            │         │  burtbot.service   (user unit — INSTALLED, NOT    │
                            │         │    STARTED: its Discord token must be rotated,    │
                            │         │    it was hardcoded in the source until 09-03)    │
                            │         │                                                   │
                            │         │  cloudflared.service      (system)                │
                            │         │    tunnel "Reserve", the only inbound path        │
                            │         │                                                   │
                            │         │  /home/brad/.claude/memory   ← the corpus         │
                            │         │  ~/.config/moses/roster.json      ← the roster         │
                            │         │  /var/lib/moses/             ← standup state      │
                            │         └──────────────────────────────────────────────────┘
                            │
                            │         CLOUDFLARE                    VIATICA (Railway, "Customs")
                            │         ──────────                    ───────────────────────────
                            └────────▶ moses.example.com/mcp     /api/cron/cfo     (Big Pipe)
                                        │  MCP portal + Access      /api/cron/support (Tagilla)
                                        ▼                             ▲
                                       moses-origin.example.com    │
                                        │  tunnel + service token     │
                                        ▼                             │
                                       127.0.0.1:8766 ────────────────┘
                                                        moses standup calls these
```

**No component on Reserve calls the Anthropic API.** Brad's own Claude app is the model (his
subscription), so the reasoning is free and the box only answers questions. That is why the Slack
listener is deterministic and why the MCP server exists at all.

### 1a. Two trees, one copy of everything

Moses is two source directories on one repository. Which tree a file lives in says who runs it:

| Tree | What runs from it | Deployed to |
|---|---|---|
| the agent tree | the Slack listener, the `moses` CLI, the drift check, the tests | nowhere — the user unit runs the SOURCE in place, since the migration off root on 2026-09-02 |
| the MCP tree | `mcp_server.py`, `dashboard_server.py`, `arch_check.py`, `transcript_index.py` | nowhere — the user units read it **in place** |

**A module appears in exactly one of them.** Four appeared in both. Three were byte-identical;
`mcp_server.py` had drifted 393 lines apart, and the agent tree's copy — the one you land on when you
grep for a tool name — declared 16 tools while the process served 34. That is how Moses was twice
described in this house as lacking tools he had had for weeks, and the second time the person doing
the describing had the running server one command away.

`project_status.py` was the awkward case: the `moses` CLI runs it as a script from the agent tree and
that CLI is root-owned, so repointing it needs a privileged install. The MCP server adds the agent
tree to `sys.path` instead. One file, both callers, no sudo.

Two installers that would have copied the stale server over the live one now **refuse to run** and
say what replaced them. `moses-drift` fails on any duplicate `.py` across the two trees — including
identical ones, because identical today is the trap rather than the reassurance.

### 1a-ii. The systemd units are in the repository

Eighteen units run Moses. The repo used to track two of them and **both were wrong** — pre-migration
copies still pointing at `/opt/moses/mcp_server.py`, from before the MCP server became a user
service. The rest were tracked nowhere.

They now live under the agent tree as a **record, not a deployment**: systemd still reads the
installed files. `moses-drift` compares every tracked copy against its installed counterpart and
reports both directions — a copy that no longer matches what runs, and a running Moses unit with no
copy at all. Symlinking was rejected: it welds systemd to a repository path, and this repository is
about to move into `~/Projects`.

---

---

## 1b. The dashboard — projects.example.com

Read-only web view of the same state Moses answers from. **A THIRD CODEBASE**, and that surprised
everyone on 2026-08-22: Knight was asked to fix a nav bug on it, cloned the Viatica repo, and
correctly reported that `/projects` does not exist there. It lives beside the MCP server, not in
either product repo.

```
  Browser ──▶ projects.example.com ──▶ cloudflared ──▶ 127.0.0.1:8791
                                                            │
                                          moses-dashboard.service (user unit, brad)
                                          /home/brad/Projects/moses/mcp/dashboard_server.py
                                                            │
        ┌────────────────┬──────────────────┬───────────────┼──────────────────┬─────────────────────┐
        ▼                ▼                  ▼               ▼                  ▼                     ▼
   / home_page()   /projects page()   /hardware       /architecture   /changelog/<id>
                   (board, in-place   hardware_page() architecture_   changelog_page() ── changelog.py
                    refresh)                          page()            reads the project's git history
        └──────────────── every route is framed by shell(): one nav, enforced by dashboard_nav_test.py
```

**Every page goes through `shell()`.** It did not always: `/projects` once built its own document with
no nav, so you could click into it and not back out. `dashboard_nav_test.py` now renders every route
and fails if any page's pills differ from the others.

**Search (2026-09-11).** Projects and every changelog carry a search box. It sits OUTSIDE `#live`,
because the in-place refresh replaces `#live` wholesale; it re-filters on the `live:updated` event the
refresh fires after each swap; it is rendered hidden and revealed by its script, so with script blocked
there is no dead box. The refresh itself now compares fetched content with what the SERVER last sent,
not with the live DOM — a search (or just opening a project) changed the live DOM, so every tick looked
like a change and the page was redrawn for nothing. Guarded in real headless Chromium by
`dashboard_search_test.py`, which speeds the refresh to 300ms and changes the registry mid-search.

**The changelog (2026-09-11).** Each project header links `/changelog/<id>`. `changelog.py` builds it
from the project's own repository on every request (cached by HEAD), so it cannot drift: every commit
that changed package.json's "version" starts a release, and each change is listed under the version
the code carried when it was committed — which reaches back past Viatica's 1.0 to 0.1.0, grouped by
week. Releases deliberately do NOT come from tags: Viatica's patch tags lapsed for ten releases. A
repository with no version file is grouped by day; a project with no repository says so. Guarded by
`changelog_test.py`, which builds throwaway repositories with a known history.

It renders from the files that own the data — `projects.json`, the roster, the corpus — and writes
nothing. There is no auth in front of it beyond the tunnel.

**Why this section exists at all:** the service was built and never written down anywhere. Every
check we had validated claims the docs MADE, so a component in no doc was invisible to all of them —
`arch_check.py` says so in its own output. `moses-drift` now enumerates running services and flags
any that appear in no architecture document, which is how both this and `cloudflared` were found.

---

### Who said it — resolved from the record, never from the conversation

The transcript handed to the model used to label humans by raw Slack id, on the reasoning that Moses
had no `users:read` scope and that inventing a name would be a fabrication where it is least
excusable. The second half still holds; the first had quietly stopped being true. On **2026-09-17**
the model did the only thing an unlabeled id leaves it — inferred "Jon" from context, addressed Brad
by it, and told him his own confirmation was waiting on somebody else's approval. Measured that day,
the listener's own token resolves that id to "Brad Allen" on the first call.

`agent/people.py` resolves it now: bots name themselves on the message (no scope needed), humans are
looked up once and cached, and **anything unresolved stays an id** rather than becoming a guess. Who
may approve is compared **by id** (`people.is_owner`), never by the name shown in a transcript —
Atlas named the general fault in that same thread: a check that reads the rendered conversation
instead of the record.

## 1c. Why there is exactly ONE Slack listener

A **user** unit named `moses.service` ran alongside the system one from 2026-08-16 to 2026-08-22.
Both held a Socket Mode connection; Slack delivered every event to both. Deleted 2026-08-22.

The deleted unit, recorded here because the file is gone:

```ini
[Unit]
Description=Moses — project-of-projects agent (Slack Socket Mode), running as brad
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=%h/.config/moses/slack.env
# Runs the SOURCE directly. No copy, so no deploy manifest to forget a file from.
ExecStart=%h/moses-venv/bin/python %h/Projects/moses/agent/listener.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
```

**Its intent was reasonable and its effect was not.** Running the source directly avoids a deploy
manifest that can omit a file — a real fault this box has had. But systemd loads the code once, at
start: that unit booted on 2026-08-16 and was still answering Brad with **six-day-old code** on
2026-08-22, while every deploy went to `/opt`. Two Moseses gave different answers to the same
question and neither was obviously wrong.

**Why the SYSTEM unit is the one that survives**, though it needs root to deploy:

| | user unit (deleted) | system unit (kept) |
|---|---|---|
| code | `~/Projects/moses/agent` — whatever is on disk, loaded at boot | the same tree; there is no deployed copy any more (see 1c) |
| gate | none | installer runs the suite, verifies the import, rolls back |
| hardening | none | `ProtectHome=read-only`, `NoNewPrivileges`, `ReadWritePaths=/home/brad/.claude` |

Nothing referenced it: no `.wants`, no `.requires`, no script called `systemctl --user … moses`.
Verified before deleting, along with the system unit being `active`/`enabled`.

**How this is prevented from recurring:** `moses-drift` fails when the same unit name is running in
both the system and user scope. It found this one — but only after it had been live for six days,
because until 2026-08-22 nothing looked. Note the trap that hid it: `systemctl --user` returns
**nothing at all** without `XDG_RUNTIME_DIR=/run/user/1000`, and "no user units" is indistinguishable
from "no duplicates".

## 2. How a question reaches an answer (MCP path)

```
  Claude app                Cloudflare                     Reserve
  ──────────                ──────────                     ───────
  "who owns the backups?"
        │
        ├─ OAuth ─────────▶ Access: is this the owner's address?
        │                        │ no  ──▶ 401, stop
        │                        │ yes
        │                        ▼
        │                   MCP portal  ── CF-Access-Client-Id/Secret ──▶ Access on origin
        │                                                                      │
        │                                                                      ▼
        │                                                            moses-mcp-public :8766
        │                                                                      │
        │                                                    tool: get_roster ─┤
        │                                                                      ▼
        │                                                            /usr/local/bin/moses roster
        │                                                              reads ~/.config/moses/roster.json
        │◀─────────────────────── tool result ─────────────────────────────────┘
        ▼
  the app reasons over the result and answers Brad
```

## 2b. Two domains, on purpose — the example.com migration

**Written 2026-09-03, the day the estate stopped living on the product's domain.**

Moses, the dashboards and code-server had all been published under `example.org`. That is the
domain customers reach the product on, and none of these are the product. Brad: *"get example.com
behind Cloudflare so that we can use that domain for the BCM Dashboard as well as all the
Moses/Project stuff."*

```
                     ONE TUNNEL, NAMED "Reserve"
                     00000000-0000-0000-0000-000000000000
                     connector: devbox
                                  │
       ┌──────────────────────────┼──────────────────────────┐
       │                          │                          │
   example.com                                       (catch-all)
   ── the estate ──                                      http_status:404
       │
   bcm ──────────▶ 100.64.0.1:8001
   projects ─────▶ localhost:8791
   code ─────────▶ 100.64.0.1:8080
   moses-origin ─▶ 127.0.0.1:8766
   example.com, www ▶ 127.0.0.1:8793
   moses.example.com = MCP PORTAL, no ingress rule
```

**ADDITIVE, NEVER A CUT-OVER.** Both sets answer simultaneously. Nothing on `example.org` is
retired until its replacement is proven working, so there is no window in which a service is neither
in one place nor the other. The `example.org` rows come out only when `moses` moves.

**All example.org names were retired 2026-10-10** (`code.`, `projects.`, `moses-origin.`: route,
Access app and DNS record each). Retiring `moses-origin.example.org` took Moses MCP down for about
half an hour, because the MCP **server** record still pointed at it — the portal had moved on 9/23,
the server had not. The server's URL is fixed at creation, so it was deleted and re-created against
`moses-origin.example.com`. Lesson: a Cloudflare-side object can be a caller too.

**The Moses pair is two objects, not one**, and that is the whole reason it was left until last:

| object | what it is |
|---|---|
| `moses.example.com` | an **MCP Portal** Access application. **No tunnel ingress rule at all.** |
| `moses-origin.example.com` | the self-hosted origin behind the tunnel, on a **service token** |

The portal is what the Claude app connects to. Moving it means creating both, repointing the
connector URL in the app, and updating every script that hardcodes the old name — so it takes Moses
off Brad's phone if it is done carelessly.

**Access lists were copied from the live policies, not from memory:** `bcm` is Brad only (it renders
the business's expenses, revenue and Schedule C figures), `projects` is Brad and Jon, `code` is Brad
only. Session duration standardised at 730h; `projects.example.org` alone had been 24h with
nothing indicating that was deliberate.

**Two verification traps live here, both hit on 2026-09-03** — see
[[gotcha-dns-port53-intercepted-on-reserve]]:

1. Verifying a new hostname through the LAN resolver **poisons the eero's negative cache for 30
   minutes**, and it then serves that NXDOMAIN to Brad's browser. `publish-hostname.sh` therefore
   resolves only over DNS-over-HTTPS and passes `--doh-url` to curl.
2. An Access application takes seconds to apply at the edge, and until it does **the tunnel passes
   traffic straight to the origin**. The check retries for ~60s before accusing, but never passes on
   a timeout: unprotected and not-yet-protected are the same risk while they last.

---

**LABS IS GONE.** The Raspberry Pi was retired and its card wiped on 2026-09-03. BurtBot and the
3DCarStuff dashboard were moved to Reserve first; the Gmail ingest that fed that database garbage was
deliberately left behind. Nothing in this estate should reach for `192.168.1.20` or `100.64.0.2`
again — see [[project_labs_decommission_audit]].

**Three Access applications, three different questions.** Confusing them cost an afternoon:

| Application | Type | Policy | Answers |
|---|---|---|---|
| `Moses` → `moses` | MCP | `Moses-MCP` (Emails) | who may USE the server's tools |
| `Moses` → `moses.example.com` | MCP Portal | `Moses-MCP` (Emails) | who may reach the portal |
| `moses-origin (example.com)` → `moses-origin.example.com` | Self-hosted | `Moses MCP origin` (**Service Auth** + service token) | who may reach the tunnel origin |

A service token needs Action `Service Auth`, never `Allow` — an Emails rule can never match a machine.

---

## 2c. The delivery chain — who holds what, and where it stops

**Brad, 2026-09-04, on why this exists:** *"I'm the Product/Project Architect... I wanted to build a
software development team with all the necessary parts to PROPERLY build and ship software."* He is
not going to read the diff, so every question a reviewer would ask has to be asked by something in
this chain.

```
  Brad ──▶ Moses                    what he wants, in his own words
             │
             ├──▶ task ─────────▶ Knight        how to build it
             └──▶ acceptance ───▶ Zryachiy      what must be TRUE when it is done
                                                (written BEFORE the code exists)
                     │
                 Knight builds, in a clone with no remote and no push rights
                     │
                 GATE      vitest + tsc, run by the RUNNER, never claimed by Knight
                     │
                 rebase onto current master, then RE-GATE
                     │
                 ZRYACHIY  reads the diff. Two questions, not one:
                             is it correct?           CONFIRMED blocks
                             is it what was asked?    MISSED blocks
                             suspected but unproven?  PLAUSIBLE rides on the commit
                     │
                 PUSH      (direct targets only — branch targets stop here for a human)
                     │
                 DEPLOY VERIFICATION
                    1. Railway's record   — did OUR commit build and go live?   (declaration)
                    2. the live site      — does it answer, are deps green?     (behavior)
                     │
                 ┌───┴────────────────────────────┐
                live                          broken / failed
                 │                                │
            report success                   REVERT and push again
                                             (never reset, never force)
                     │
                 Moses ──▶ Brad
```

**Why acceptance criteria go to the reviewer and not the builder.** Withholding the *builder's*
framing from Zryachiy is deliberate — handing over the author's own account of what they attempted
is how a second opinion becomes agreement. The requester's statement of done is a different object:
written first, by the side that wanted the change, so it can be judged against. Criteria derived
from a finished diff only describe whatever happened.

**Why the deploy is verified twice.** Railway can report SUCCESS for a container that crash-loops on
its first request, and a site can answer 200 while still serving the previous build. Neither source
alone is evidence. And the gate cannot help here at all — it runs inside a clone and never leaves
the process, which Brad proved by sabotage when a completely broken PDF import passed all 711 tests.

**Why "unverified" does NOT roll back.** Not knowing whether a deploy landed is not evidence that it
broke anything. Reverting on ignorance would let a Railway API blip undo good work — the false
positive that gets a mechanism switched off. It is reported as loudly as a failure and left for a
human.

**The last seat is Moses's, filled 2026-09-04.** Zryachiy judges the criteria against the DIFF,
before anything ships. After the deploy verifies itself, MOSES judges them against the RUNNING
PRODUCT — he gave the order, so he confirms it was carried out. Brad's phrase: trust but verify.

A ledger records every criterion, who can check it, and whether anyone has:

| criterion | who | why |
|---|---|---|
| public page, or an API Moses holds the secret for | **Moses** | he can fetch it and look |
| marked `[admin]` | **Brad** | an admin page answers Moses with a 307 to `/login` |

Giving Moses an admin session was considered and REFUSED. The line in this estate is that the agent
never holds the credential — Knight has no push rights, nothing here reads the Railway token — and a
verification endpoint that reads admin content for a secret-holder would be a read path built around
the auth gate, which is the exact shape of the public-link leak that started this.

**A DEPLOY THAT LANDED IS NOT A REQUEST THAT IS DONE.** A job with any unverified criterion reports
`awaiting-verification`, not `live`; the Slack headline says how many are open and how many need
Brad; and the standup carries them every morning until they close. Brad, 2026-09-04: *"a request
isn't marked as closed until everything is verified, even if I'm one of the holdups."*

Moses cannot close a row marked `[admin]` — the ledger refuses it by name, because the one thing
worse than an open row is a closed one nobody actually checked.

**NO STEP IN THIS CHAIN MAY DEPEND ON BRAD DOING SOMETHING TO THE REPOSITORY.** He does not merge,
pull, or read diffs — 2026-09-04: *"I'm not going to work on it locally, or anywhere for that
matter."* He is the architect; the agents hold the other seats. A design that needs him to merge a
branch or refresh a checkout has put a person back in a seat this pipeline exists to fill, and it
will simply not happen. See [[feedback-brad-never-touches-the-repo]].

---

### 2c-i. Zryachiy explores — "Zryachiy, test X" (Viatica E2E phase 2, 2026-09-11)

Zryachiy's second job. He already reviews Knight's diffs; now he also USES a feature, the way its
customers would, in a disposable Viatica on Reserve.

```
  Brad in Slack ── "Zryachiy, test group trips" ──▶ Moses
                                                      │  zryachiy_explore(feature, criteria)
                                                      │  ACT tool: a human-directed turn only
                                                      ▼
  knight/bin/zryachiy   one at a time · 4 a day · records in zryachiy/jobs/ (not Knight's history)
        │  launches, detached: knight/bin/zryachiy-run
        ▼
  Viatica: npm run explore   (the walls live THERE, not here: e2e/explore.ts)
        │  sandbox up · egress proxy · one browser server per person, over HTTP
        │  boundary check through those servers; a failed check means no run
        │  agent/claude-run --app zryachiy-explorer (the one runner), on the Pro subscription:
        │  tools = the browsers only, read back from the init event
        ▼
  exit code 0 clean · 1 findings · 2 could not finish   (never re-judged here)
        │
        ├──▶ #viatica-dev, as Zryachiy
        └──▶ the conversation that asked (notify.json, written by the listener; agent/knight_notify.py)
```

The verdict is the runner's exit code, and "could not finish" is said as NOT a pass. Starting one is an
acting tool, like knight_start, because it spends about half an hour of Brad's allowance; reading one
(zryachiy_status) is free. Tests: knight/test-zryachiy.sh (fake runner, posts nowhere) and
conversation_test.py (the gate).

## 3. Accountability — the standup

```
  08:00 America/Chicago
  moses-morning.timer ──▶ moses-morning ──▶ moses standup
                                                │
                    ┌───────────────────────────┼───────────────────────────┐
                    ▼                           ▼                           ▼
            birdeye-report morning     GET /api/cron/cfo           GET /api/cron/support
            (Reserve: backup,          (Viatica: Big Pipe)         (Viatica: Tagilla)
             disks, quota, jobs)              │                           │
                    │                         │  502 + error on a         │
                    │                         │  failed Slack post        │
                    ▼                         ▼                           ▼
                 #Ops                     #finance                    #support
                    │
                    └──▶ record outcome ──▶ /var/lib/moses/personas/<id>.json
                                              │
                    overdue = now − last_success > cadence × 1.5
                                              │
                    ▼                         ▼
              exception-only Slack post from Moses, or silence
```

**The roster — not any script — defines who owes a report.** `moses-morning` used to hold a hardcoded
list of two URLs, which made the *caller* the definition of duty: drop a persona from that list and it
silently stopped being anyone's responsibility. Now duty is declared in `~/.config/moses/roster.json` and
measured against, so **silence is a failure with a name on it** rather than an absence of one.

Moses stamps its own heartbeat too (`/var/lib/moses/last-standup`), so a morning that never ran is
reported by the next one instead of vanishing.

---

## 4. Data flow — where state lives and how it survives

```
  /home/brad/.claude/memory        the corpus: commandments, project state, todo, ideas, research
        │                          MASTER. Written by Claude sessions and by MCP capture tools.
        │
        ├──▶ 02:30 reserve-nightly-backup ──▶ /home/moses/Backups/*.tar.gz
        │    source: agent/reserve-nightly-backup, installed by agent/install-nightly-backup.sh
        │    archives the corpus, transcripts, ~/Projects, ~/scripts, ~/retired, code-server config
        │                                          │
        │                                          └──▶ 03:00 iDrive ──▶ off-site
        │
        └──▶ read in place by the listener and the MCP server (no copy since 2026-09-02);
             the nightly job only alarms if the corpus is missing or empty

  ~/.config/moses/roster.json     duties + charters      ← read at runtime by standup, status, MCP

THE ROSTER IS THE OPERATOR'S FILE, and root still executes what it names. Once Moses moved off root
(2026-09-02) the roster moved with it, so editing who is accountable needs no sudo. But two root-run
scripts execute a persona's declared `check.argv` — the standup's `moses` CLI and the
`moses-remediate` sudo wrapper — and an operator-writable roster without a guard would have turned
"declare a persona" into "run anything as root".

So the trust sits on the TARGET, not the file: `lib/roster-exec-guard.sh` resolves symlinks first,
then requires a root-owned, non-group/other-writable executable inside a root-owned,
non-writable directory. Relative paths are refused outright — root must never search PATH. Both
callers refuse EVERYTHING if the guard file itself is missing, because an absent check is the one
failure that would restore arbitrary root execution.

This is stricter than the arrangement it replaced: the old root-owned roster was trusted absolutely,
and nothing checked what it pointed at.
  /var/lib/moses/personas/   last_success per id    ← the accountability ledger
  /var/lib/birdeye/jobs/     handed-off job state   ← survives SSH drop; orphans detected
  ~/.claude/memory/projects.json   declared project state ← cross-checked against derived facts
```

### Transcripts — two indexes over one database

```
  ~/.claude/projects/*.jsonl          live sessions on Reserve
  ~/.claude/transcripts-archive/      history recovered from the laptop tarballs (backfill)
        │
        │  transcript_index.py — parse, REDACT, store    (hourly timer)
        ▼
  moses-mcp/transcripts.db
        ├── messages + msg_fts   FTS5/porter   → search_transcripts   exact wording
        └── chunks + vec BLOB    bge-small-en  → recall               meaning
                                 fastembed/onnxruntime, local, no API spend
```

Two indexes because they fail in opposite directions. Exact search is right for an error string, a
port or a session id, where a near-miss is a wrong answer. Semantic recall is right for a
half-remembered idea — it connects "make backups safer" to "guard against unmounted drives", which
scores zero under FTS5. `recall` fuses both rankings; `search_transcripts` stays purely exact.

They deliberately cover **different material** in one respect: tool invocations (59% of all
messages) are indexed for exact search but excluded from embeddings, because a bash line is mostly
paths and punctuation and crowds out real discussion.

Redaction happens once, at parse time, before anything is stored — so neither index can leak a
credential, and no path re-reads the raw .jsonl.

### Discovery — the half that runs unprompted

```
  moses-discovery.timer  Mon 08:45 ──▶ discovery.py sweep
                                            │
        chunks + vec ──▶ genericness filter ──▶ cross-origin pairs ──▶ seen-ledger
                                            │                          (announce once)
                                            ▼
                              ~/.claude/memory/discoveries.md   ← inherits the nightly backup

  find_connections   pairs that converge across sessions/projects/months — nobody asked
  link_idea          arbitrary text vs the whole corpus; add_idea calls this automatically
  themes             k-means clusters, excerpts only — the MODEL names them, so no API spend
```

Retrieval waits to be queried, and a question Brad never thinks to ask never gets answered. That
gap is the entire reason this exists: *"there'll be no way to catch something like that unless I
build that system now."*

Three filters carry the signal, because naive nearest-neighbour returns garbage: pairs must come
from different sessions or ≥14 days apart (adjacent chunks are restatements); the top 40% of
chunks by *mean similarity to everything* are dropped as boilerplate; and each chunk may appear in
only one reported pair, so a hub chunk cannot eat the report. Harness-injected text — skill
preambles, `<system-reminder>`, session-continuation notices — is excluded from embeddings
entirely; it repeats verbatim across sessions and paired at 0.92 before it was cut.

Every report states corpus composition and warns when one session dominates. Manufacturing insight
from a thin corpus would be the same failure as a monitor reporting success while checking
nothing.

Capture lives **inside** the corpus on purpose: it inherits the nightly backup and the off-site leg,
and Moses can read it back with no extra wiring. No second store to keep in sync.

---

## 4b. Personas on demand — read-only, posting untouched

```
  Brad's Claude app
        │
        ▼
  Moses MCP ──┬── birdeye_status    live: df + handed-off jobs        (runs as brad)
              ├── birdeye_report    reads /var/lib/birdeye/last-report.txt, with its AGE
              ├── bigpipe_pnl   ─┐
              └── tagilla_queue ─┴─▶ HTTPS + Bearer ──▶ Customs /api/agents/report
                                                              │
                                        shared libs: cfo.ts, support/digest.ts
                                                              │
  UNCHANGED, on their own timers ──▶ /api/cron/{cfo,support} ─┘ ──▶ Slack #finance / #support
```

The scheduled Slack reports are untouched: same timers, same channels, same 502-on-failed-post
contract. The on-demand path posts nothing and **cannot** — no Slack module is reachable from
`/api/agents/report`, asserted by a test that fails if the import is ever added. If a question
posted to the channel, a message appearing there would stop meaning "the scheduled report ran",
which is the only reason those reports are worth reading.

Both doors compute from the same modules, so the on-demand answer and the morning report cannot
disagree — the support digest was extracted out of the cron route specifically to remove the second
implementation before it could drift.

**Why `birdeye_report` reads a cache rather than running the report.** Run from the MCP server it
would post; run as brad it cannot read the iDrive profile and reports the backup missing — a false
alarm about the one thing Birdeye watches. Root's own run now caches its rendered text, so the tool
returns exactly what #ops received, and states the age. Stale past ~26h is called out as a MISSED
scheduled run rather than presented as current.

### 4b-i. A screenshot pasted into Slack

Brad pasted a picture of a live bug and both Moses and Atlas said they could not see it. Slack was
delivering the attachment the whole time; nothing in the listener had ever looked at `event["files"]`,
and the bot could not have downloaded one anyway until `files:read` was granted (2026-08-23).

```
  Slack message with files[]
        │  listener (holds the bot token) downloads url_private
        ▼
  attachments.fetch  ──▶ images only · ≤12MB · ≤4 · content-type CHECKED, not the status code
        │                 (Slack answers 200 with an HTML login page when the scope is missing)
        ▼
  conversation._invoke  --input-format stream-json  ──▶ [image block, image block, text]
                        every native tool still denied · nothing written to disk
```

**No tool is granted, and that is the design.** The first build fetched the image to a scratch
directory and handed Moses the `Read` tool, scoped by running the CLI with that directory as its
working directory — on the belief that Claude Code confines file access to the working tree.
**Measured before shipping, and it does not:** the turn read `~/.claude/memory/MEMORY.md`, well
outside that directory, with no refusal recorded. An explicit `--allowedTools Read` is a permission
grant and does not carry the confinement the sandbox has when `Read` is never allowed — which is why
Knight's guarantee holds (§4c) and that one would not have.

Sending the picture as message content removes the question entirely: no grant, no directory to
scope, no cleanup, no boundary to get wrong. Strictly less surface than the version that was
approved, arrived at because the guard was tested rather than assumed.

**Failures are told to the model, not swallowed.** A file that was skipped — wrong type, too big,
download refused — is named in the prompt so Moses says he could not see it. Silence would rebuild
the original bug one layer down.

---

### 4b-ii. The incident queue — the one place Moses WRITES to the product

```
  Brad ──▶ Moses ──┬── viatica_incidents   GET  /api/incidents/agent   (read, always available)
                   └── resolve_incident    POST /api/incidents/agent   (write, human-directed only)
                                                    │  Bearer CRON_SECRET
  Knight ── runner, after a GREEN gate AND a real push ──┘
```

Incidents are what the product noticed going wrong for real people, as opposed to what somebody wrote
in about — that is Tagilla's queue, and they are two halves of one desk.

**Reading is free; closing is not.** `viatica_incidents` sits in the read set and is always available,
because the queue is state and state is read live rather than recalled. `resolve_incident` is in the
act set, so it unlocks only on a turn a human directed — "we looked at that" is a claim somebody will
rely on later, and an agent must not be able to make it because a persona's own alarm text said so.

**A signature is required and refused before the lookup.** The app will not close an incident without
a name, so the queue says MOSES or KNIGHT rather than implying a person looked.

**Knight never holds the credential.** He has no network and no way to reach Viatica — that sandbox is
why he is safe to run unattended, and trading it for the convenience of tidying a queue would be a bad
bargain. A job can carry an incident id; the id travels to the RUNNER, which closes it only if the
gate went green **and** the work was actually pushed. Green with nothing pushed means nothing shipped,
and an incident closed against work nobody received is a lie the queue then repeats. Same shape as
push rights: the privileged step belongs to the thing that verified the work, never to the model that
did it. Pinned by a test that fails if a red job closes its incident.

**Asking whether a report landed.** The intake answers 204 whether it filed something or dropped it as
noise, so from outside those were the same event. `POST /api/incidents?probe=1` runs the same decision
without the write and says which rule would drop it. Drops are counted, because a filter that starts
swallowing real failures looks exactly like a quiet week.

---

## The one runner (M20) — every agent's `claude -p` command line

```
  Moses chat ──────┐  moses-chat / -directed / -reading   conversation.py, chosen per turn
  alarm diagnosis ─┤  moses-diagnosis                     diagnose.py
  liveness probe ──┤  moses-liveness                      moses-liveness, every 30 minutes (joined 2026-09-15)
  knight-run ──────┤  knight-builder · zryachiy-reviewer  via agent/claude-run; lists from guards.env
  Viatica explore ─┘  zryachiy-explorer                   via agent/claude-run; browser approvals
                   │                                      must fit inside its ceiling
                   ▼
      agent/claude_runner.py      profile → command line
        always  --restricted · --strict-mcp-config · --tools <exactly what the profile offers>
        --tools and --allowedTools built from ONE declaration, so they cannot disagree
        a caller may narrow (approve less, refuse more), never widen
        a profile that grants anything needs a written reason of 20+ characters, or nothing imports
        a BUILD profile (builder, reviewer, explorer, scout, diagnosis) gets the ten commandments
          via --append-system-prompt, from the framework's hooks/inject-commandments.sh
          ($MOSES_FRAMEWORK, else ../moses-framework); can't load them → the job refuses to start;
          MOSES_BUILD_JOB leaves out the "spawn sub-agents" line (no sub-agent tool here)
                   │
                   ▼
      claude -p … ──▶ result ──▶ classify():  ok · DEGRADED · failed
                                  degraded = exit 0 with a tool refused. It is SAID (a line on Moses's
                                  answer, in Knight's report, in Zryachiy's report) and never retried:
                                  the same turn would be refused the same way.
```

Guards: `claude_runner_test.py` fails if any other file in this repo carries a tool-grant flag, or
starts the CLI in print mode at all (the liveness probe did, with no flags, and went unseen until
2026-09-15 because the first version only looked for flags) (a named
allowlist of exceptions, each with its reason, that fails when an entry stops being true), and proves a
missing reason stops the module importing. `tool-wall-probe` launches every profile against the real CLI
up to its opening event, which is free, and fails any profile whose live tool list differs from its
declaration in either direction; it runs in the 07:30 moses-selftest sweep. It also scans outside this
repo (~/scripts, /usr/local/bin, the installers, Viatica's e2e and scripts), where nothing may start
the CLI. Three hand-run scripts did (the /opt installer and two one-offs from 2026-08-25) until they
were retired on 2026-09-15; the drift checks that read the installer's file list went with it. Jon's side built the same
chokepoint the same week; the probe is ours, the chokepoint test and the reason line are his.

---

## 4c. Knight — the one persona that writes, and deploys

**The scout, at both doors (2026-10-01).** Moses could not read code — his chat profile offers no file tool —
so every idea he proposed, most of them from Atlas, was judged in prose against his memory. Proposal
6848b820 asked to extend a coverage assertion that exists in Atlas's system and not in ours, and reached
Knight before anyone looked. `agent/scout.py` asks one narrow question — does what the task ASSUMES
already exist? — at the two moments it matters:

```
  PROPOSING   listener posts the proposal ──▶ background thread ──▶ scout vs the target's checkout
                                              PREMISE_FALSE/ALREADY_DONE → withdrawn, reason in thread
                                              GROUNDED → "checked: touches <files>" in thread
  BUILDING    knight-run, before the builder ──▶ scout vs the clone at the fetched tip (catches drift
                                              from work done directly in Claude Code) — see the diagram
```
A check that could not run never blocks and never passes: it is said, and Knight is told to verify the
premise himself. The verdict is the first line or it is UNCLEAR (scout_test.py proves prose cannot pass).

### A build asked for in Slack: a confirmed spec, then one status card (2026-10-17)

Brad: "When you said 'civil and water' earlier I didn't think you meant civil engineer" and "there's no
real way for me to know the status of what's being built." So in Slack Moses cannot start Knight at
all (knight_start is not among his tools); he proposes, Brad confirms by example, code starts it, and
the run reports on ONE message that is edited in place.

```
  Brad ── "make the listings page GIS analysts only" ──▶ Moses (model turn)
                                                        │  knight_propose(task, acceptance,
                                                        │      will_include, will_exclude, target)
                                                        ▼  agent/build_spec.py → STATE/build-specs.json
  listener, after the turn: claim the spec for THIS conversation — only one recorded by the Slack
        │  turn's own MCP server (:8765, never the iOS app's), and only if no other turn overlapped
        │  then POSTS THE SPEC ITSELF (✅ will include / ❌ will not), word for word as recorded
        ▼
  Brad: "yes" · ✅ on the spec · "start <id>"     anything else he says there retires the spec
        │  owner only · same channel AND thread · 24h · no other agent spoke since (else: asked)
        │  an idea proposal also waiting here? asked which, nothing started
        ▼
  build_spec.start (code, not the model) ── knight start --acceptance "<criteria> + CONFIRMED EXAMPLES"
        │  takes the spec under a lock first: two yeses start one build
        ▼
  knight/lib/card.py — THE RUN'S CARD: posted once, then chat.update at every step
        started → building → checking → reviewing → following-up → landing → landed/live → done
        │  moves once into the asking conversation (new copy posted first, old deleted after)
        │  a conversation that refuses it → Knight's channel; failures appended to card-error.txt
        ├── the report: one reply under the card
        └── a ping (a reply; edits notify nobody): only when the run is finished or needs Brad
        knight-next's follow-ups and next pieces carry run-of and edit the same card
```

What the confirmation does NOT cover: the next pieces knight-next briefs after a landing (they build
the tracker scope the project was started with), and a new project's first job (gated by Brad's
one-time code, with the scope from the tracker). From the iOS app, knight_start itself requires the
examples, but nothing waits for a separate yes there — the request in the app is the confirmation.
Tests: agent/build_spec_test.py, knight/lib/card_test.py, knight/test-notify.sh.

**After the spec (2026-10-18): examples are tests, notes are notes, and "done" means something.**

```
  examples.txt (lib/examples.sh, from the acceptance) ──▶ builder's brief: one test per example
        │                                                  (the example's text, verbatim)
        ▼
  knight-run, after KT_GATE: every example named in a test file at the branch tip (case, quotes,
        │  escapes, spacing ignored; test paths, test-*.sh, Rust #[cfg(test)])? no ──▶ gate RED
        │  the reviewer's brief: check each test asserts its example the right way round
        │                                                  → knight-next follows it up (missed requirement)
        ▼
  review: CONFIRMED / MISSED ──▶ held → follow-up (bounded)   PLAUSIBLE ──▶ knight/notes/<target>.md
        │                                                    + a card line; the job lands on its merits;
        │                                                    the next piece is handed the open notes
        ▼
  scope covered (SCOPE COMPLETE) ──▶ knight-next: `knight provision` green on main? live URL answers?
        │  (provision waits for a job holding the clone; a start waits for provision; 30 min cap)
        │  either no ──▶ card "needs you", NOT done
        ▼  both yes
  card "waiting for you: have a look" · tracker next = "Waiting for Brad's look" · glance.py records it
        ▼
  Brad: "looks good"/"lgtm" as a REPLY IN THE CARD'S THREAD, "mark <project> done" in its channel,
        or ✅ on the card — owner only, a week, no other agent since; a spec or idea also waiting there
        gets a question instead ──▶ listener code: stage = shipped (exact id) → card says done →
        knight-next builds nothing more
```

One log per run: card.py appends every step of every job in the run to run.log in the run's first
job (written before Slack is tried); `knight status <job>` prints it, and the Projects page shows each
project's newest card (stage, last line, what is next) from the same card.json the Slack card uses.
Tests: knight/test-examples.sh, knight/test-next.sh, agent/glance_test.py, knight/lib/card_test.py,
mcp/dashboard_board_test.py.

```
  knight_start(task, target, will_include, will_exclude) ──▶ knight      (the iOS app; from Slack,
                                                                          build_spec.start above)
                                          preflight: kill switch · 1 concurrent · 8/day
                            │
                            ├── targets/<name>.env ── unknown name? REFUSED, and the
                            │      KT_SOURCE / KT_PREP        refusal lists the real ones
                            │      KT_GATE / KT_PUSH_MODE
                            ▼
                        knight-run          (separate script: permission lists are ARRAYS)
                            │
                    ┌── THE PRE-CHECK (2026-10-01) ─────────────────────┐
                    │  agent/scout.py --app moses-scout: Read/Grep/Glob │
                    │  only, against THIS clone as freshly fetched —    │
                    │  "does what the task assumes exist here?"         │
                    └───────────────┬───────────────────────────────────┘
       PREMISE_FALSE / ALREADY_DONE ┴ GROUNDED / UNCLEAR / COULD_NOT_CHECK
                 │                                │
       NO BUILD. status refused-precheck,  findings (or "NOT checked, and why")
       Slack: "🔎 not built — what the     prepended to the builder's brief
       code actually holds"                       │
                                                  ▼
              agent/claude-run --app knight-builder   (the one runner; lists from guards.env)
                → claude -p --model opus --effort high --restricted --permission-mode acceptEdits
                  --tools and --allowedTools built from one declaration
                            │   cwd = knight/repo/<target>  ← NO REMOTE while running
                            ▼
                    ┌── THE GATE (runner, plain bash) ──┐
                    │  every command in KT_GATE, in     │
                    │  order, first red wins            │
                    │  fetch + rebase onto KT_BRANCH    │
                    │  re-run the whole gate after it   │
                    └───────────────┬───────────────────┘
                        red ────────┴──────── green
                         │                      │
                  no push, Slack     ┌── THE REVIEW (runner, a SECOND agent) ──┐
                  says which step    │  zryachiy-reviewer: no Edit/Write,      │
                  failed             │  no Task/Agent, no network              │
                                     │  input = the diff, NOT the builder's    │
                                     │          brief                          │
                                     └──────────────┬──────────────────────────┘
                                CONFIRMED ──────────┴────────── CLEAN / PLAUSIBLE
                                 or unreadable                       │
                                 or crashed                   PLAUSIBLE amended
                                        │                     onto the commit
                                  NO PUSH. Slack                     │
                                  names the finding      add remote → push → remove remote
                                                                     │
                                                              ┌──────┴───────┐
                                                      KT_PUSH_MODE=direct   KT_PUSH_MODE=branch
                                                      → KT_BRANCH, deploys  → knight/<slug>, waits
                                                                                      │
                                                                       Brad: "merge it" / "land it"
                                                                                      │
                                                                        Moses → knight_land
                                                                                      │
                                    ┌─────────────────────────────────────────────────┴──────────┐
                                    │ knight-land — refuses unless ALL of:                        │
                                    │   review CLEAN (the same lib/review-verdict.sh the push     │
                                    │     gate reads — one copy, so they cannot disagree)          │
                                    │   no unanswered PLAUSIBLE note   ·   tree clean              │
                                    │   branch not already in          ·   target really is branch │
                                    └─────────────────────────────────────────────────┬──────────┘
                                                                                      │
                                                                     merge --no-ff into KT_BRANCH
                                                                                      │
                                                            knight-land-live (DETACHED, see below)
                                                              KT_LIVE_CMD → restart · KT_LIVE_CHECK
                                                              → verify it came back → say so in Slack
```

### Landing a branch — "a human merges" without Brad typing git

**Brad, 2026-09-17:** *"Just need Moses to be able to finish up the job on command."* Branch mode was
never about who types the merge; it was about a person deciding, on a reviewed branch. What it had
become in practice was Moses handing Brad two git commands, which is the one thing he does not do —
so green, reviewed work sat unmerged. `knight_land` closes that: Brad says merge it, and the decision
he already made is carried out. **The gate did not move.** The lander re-reads the verdict, and every
refusal above is watched failing in `knight/test-land.sh` against a real repository.

**Why the restart is detached.** Every unit that runs Moses starts from this source tree — the Slack
listener and both MCP servers — so whichever one is handling "merge it" is a process the restart is
about to kill. `knight-land` merges and returns; `knight-land-live` restarts a moment later and
reports the outcome to the conversation that asked. Same lesson as `agent/deploy-and-report.sh`: a
Slack-triggered deploy that kills its own caller answers nobody.

**What "live" means is recorded per target, not inferred.** `KT_LIVE_CMD` and `KT_LIVE_CHECK` sit in
`knight/targets/<target>.env` beside the gate. For `moses` that is restarting `moses`, `moses-mcp`
and `moses-mcp-public` and then confirming all three are active; for `moses-framework` there is
nothing to restart and the lander says the merge was the whole job. **`/opt/moses` does not exist** —
that path lived in Knight's briefing for months and both Knight and Moses repeated it to Brad as a
deploy step, which is exactly why the answer is now a registry entry that can be checked rather than
a sentence somebody remembers.

### The review pass — and why it is spawned HERE

Adopted 2026-09-02 from Atlas. Three properties, and the placement is the load-bearing one:

1. **A different agent from the author, spawned by the RUNNER.** Not a step in the builder's brief.
   Atlas's own estate has the reviewer separated but the *decision to spawn* one sitting with the
   author, which he named as self-review moved up a level. His sharper form is worth keeping:
   **a gate the checked thing invokes is still self-review, even if it is compiled.**
2. **CONFIRMED blocks; PLAUSIBLE records and proceeds.** A reproduced defect stops the push exactly
   like a red test. A suspicion is amended onto the commit, so it can neither quietly stop the build
   nor quietly vanish. CONFIRMED demands the concrete input or state that breaks — a guess must not
   carry a blocking verdict.
3. **A review that could not run BLOCKS.** Crashed, killed, or an answer in a shape nothing can
   parse are all "could not look", and that has never meant "fine" anywhere else here.

The reviewer has **no edit tools by design**: a reviewer that edits is an author, and then nobody has
reviewed the edit. It runs on a cheaper tier than the build (`KNIGHT_REVIEW_MODEL`, Sonnet 5 at
medium) because reading a bounded diff is not the same job as writing the feature, and the review
doubles the model calls against a shared daily allowance of eight.

`Task` and `Agent` are denied to Knight by name — Atlas's third rule. One clone and one worker
bounded *jobs*, never what a single job spawns from inside itself.

Because the review only runs after a green gate, real commits and a clean rebase, the fake-agent
seam cannot reach it end to end. The verdict is therefore extracted as `review_verdict()` and driven
with fixtures by `knight/test-review.sh` — 17 checks, five sabotages proven red.

### The target registry

Knight works on repositories that are written down, and only those: one file per repo in
`knight/targets/`, and a name that is not there is refused before any work starts. `knight targets`
prints them; `knight doctor` REACHES each source and push target rather than printing a URL.

| Target | Source | Gate | Green work lands |
|---|---|---|---|
| `viatica` | `Projects/brads-travel-project` (master) | `npx vitest run`, `npx tsc --noEmit` | **master — and Customs deploys it** |
| `moses` | the Moses repo (main) | every `*_test.py`, in a venv prep builds | a branch — a human merges |
| `moses-framework` | `moses-framework` (main) | `tools/scan-secrets.sh --all`, then the agent tests | a branch — a human merges |

**`KT_PUSH_MODE` is the line of demarcation**, and it is recorded per repository rather than decided
per job by whoever is typing. Viatica has a real suite, a type checker and a deploy that refuses a
red build, so Knight lands there himself. Moses and the framework are the machinery that *runs*
Knight: a change there can break the gate that would have caught it, so green work waits on a branch.
Nothing about the gate is weaker — only the landing.

`knight provision <target>` clones, runs prep, and runs the gate on an untouched mainline without
starting an agent. It exists because the moment a target is added is the moment its gate is most
likely to be wrong, and because of the failure below.

**A stale generated artifact made every Viatica job red for a week.** `.next/types/validator.ts`
imports every API route by path; it is gitignored, so `git clean -fd` left it, and the copy in
Knight's clone was written 2026-08-08. Six admin routes were deleted on 2026-08-15, and from then on
`tsc --noEmit` failed with six TS2307s about files that no longer existed. Knight's nav-pill job on
2026-08-22 committed 163 lines with a clean diff and was refused for it, and his report — accurately
— said the work had not landed. Prep now deletes it, and a red gate says in plain words to run
`knight provision` to find out whether the mainline was already broken.

**The agent never gets push rights.** `Bash(git push:*)` stays denied, the clone has no remote for
the whole window it runs, and the gate is run by the runner — never "the agent said tests pass",
which is exactly what makes an unattended deploy dangerous. Red means nothing ships.

**Verified against a throwaway bare repo**: a deliberately failing test produced
`gate=red · pushed=no` with the target unchanged; a valid change produced `gate=green · pushed=yes`.
The demarcation is verified the same way — `test-runner.sh` builds a scratch repo, runs the real
runner on it in branch mode, and asserts the work arrived as a branch **and that the mainline did not
move**. Flipping the refspec to land directly turns it red.

**The report is the deliverable** — Brad reads it instead of the diff, then looks at the result.
Four mandated sections: *What shipped* (product terms), *Decisions*, *Stoppers*, *Notes*. The generic
half of the brief lives in `bin/knight`; what each codebase IS lives in `targets/<name>.brief`, so
adding a project is one directory and no edits to the script.

Agent-side guarantees: Claude Code sandboxes file access to the working directory across Write, Edit
and Bash, reads included — so `~/.claude`, Brad's checkout and `/etc` are unreachable. `--add-dir`
would open that, so it is never passed. **This is also why a target must be a registered repository
and not a path handed in per job**: Knight can only work on what he has a clone of, and the registry
is what decides that. The permission lists are bash arrays; as plain strings, word-splitting shredded
every multi-word pattern (`Bash(git push:*)` → `Bash(git` + `push:*)`).

---

## 5. Safety model — which layers are real

Ordered by what actually holds. Only the first two are boundaries; the rest are controls.

```
  1. OS / network      sudoers allowlist · loopback binds · Tailscale-only binds
                       Cloudflare Access · SandboxNetworkConfig.deniedDomains
                       ── what is not permitted is IMPOSSIBLE
  2. Typed tools       destructive ops are dedicated tools with inspectable arguments,
                       never a bash string the harness cannot read
  3. Harness gate      guard.py — deny beats allow, unmatched ASKS (53 tests, run before install)
  4. Policy            commandment #9 — shapes intent, guarantees nothing
```

The MCP server's blast radius **is its tool list**: no bash, no arbitrary paths, no destructive
operations. That is a smaller and more auditable surface than a general agent behind a permission
gate, which is why the MCP direction is better than the agent design it replaced rather than a
fallback from it. Layers 2–4 are built and tested but **parked** — see
`/home/brad/Projects/moses/agent/README-parked.md` — because agents need a model and API spend is off.

### 5a. The Moses plugin — what every Claude Code session, and every sub-agent, is handed

Since 2026-10-14 the hooks ship as the Claude Code plugin `moses@moses-framework`, from the framework
checkout (`~/Projects/moses-framework`: `.claude-plugin/`, `hooks/`, `skills/`, `agents/`), installed at
user scope from a local marketplace. They are tracked there, and **nothing is registered in
`~/.claude/settings.json` any more** (plugin and settings hooks are not de-duplicated, so re-adding them
would run each twice). The installed copy is a cache pinned to plugin.json's `version`: bump it and
run `claude plugin update moses@moses-framework`. These govern interactive sessions. Moses's own
`claude -p` jobs run `--setting-sources ""`, so they load no plugin; the build pipeline gets the
commandments from the runner instead (§ the runner, above).

```
  SessionStart       load-moses.sh              roster, drift, ledger, memory-index pointer
   (and compaction)  inject-commandments.sh     the ten, derived from project_moses.md, plus the
                                                delegation trigger (independent subtasks → spawn)
  SubagentStart      inject-now.sh              a sub-agent sees none of the above, so it gets
                     inject-commandments.sh     the clock and the ten itself; NOT the trigger
                                                (agents spawning agents multiply cost)
  UserPromptSubmit   inject-now.sh              the real clock (sessions stay open for days)
   (every prompt)    inject-commandments.sh     one line: the ten are in context, and where
                     inject-relevant-notes.sh   memory notes + this repo's docs/ sections whose
                                                words match the prompt (relevant-notes.py, BM25,
                                                high bar; replies like "yes please" get nothing)
  Stop               gate-handoff.sh            blocks handing Brad work without stated diligence;
                                                judges the revision too, at most 2 blocks a turn

  Skills             ship-and-report · architecture-diagrams · diligence · memory-hygiene ·
                     watch-it-fail · delegate     (Knight's and Zryachiy's briefs quote sections)
  Agents             moses:explore · moses:builder · moses:reviewer

  Proof              framework hooks/gate-handoff-selftest.sh and agent/relevant-notes-selftest.sh —
                     weekly, Monday 07:40, moses-gate-selftest.timer; quiet on success
  Counts             ~/.local/state/moses/handoff-blocks.log (cap:<code> = a revision let through
                     at the cap), relevant-notes.log; the self-tests write to /dev/null
```

**What no hook sees.** When the editor resumes a turn that a dropped connection interrupted, the app
writes the prompt "Continue from where you left off." and a placeholder reply, "No response
requested.", itself — no model call, no UserPromptSubmit, no Stop. The turn is not re-run unless
the undocumented `CLAUDE_CODE_RESUME_INTERRUPTED_TURN` is set, and the editor does not set it. The work
waits for Brad. See the memory note `feedback-continue-means-continue`.

---

## 5b. A new build needs a code (#14, 2026-10-16)

**Why.** Brad wants to say "build this" in Slack and have it happen, even when "this" is a new
repository. Knight builds only registered targets (`knight/targets/<name>.env`), so a new build
starts by adding a target, and a target is what gives Knight access to a repository. Until now the
only thing stopping Moses from adding one was that he has no file tools. That is a capability he
lacks, not a control. Giving him the capability without a control would let anyone who can post as
Brad in Slack, or any agent text that can talk Moses into it, open a new repository to an
unattended builder.

**The decision (Brad, 2026-10-16):** the green light is Brad telling Moses to build a project that
is already in the tracker ("Moses, build #13"). Moses then asks for a one-time authenticator code
(TOTP), and a verified code unlocks the whole start: a new directory for the project, version
control, its Knight target, and Knight's first job, briefed from the project's scope in the
tracker. *Creating* a target is what the code gates. Editing an existing target, including its push mode, works as it does today. Ordinary
Knight jobs on existing targets need no code. A code works once and only while its challenge is
open, the seed never lives in a repository, and every attempt leaves an audit row.

### Who can create a target today

```
  path                                         today                      after #14
  ───────────────────────────────────────────  ─────────────────────────  ──────────────────────────
  Moses, from Slack                            cannot — no file tools     only through the code flow
  Knight, in a job on the `moses` target       a branch can ADD           knight_land refuses an added
                                               knight/targets/x.env, and  target file with no
                                               knight_land merges it      authorization for that name
                                               ← skips the gate entirely
  A Claude Code session at Brad's keyboard     can write the file         unchanged — Brad is present
  Any process running as the operator          can write the file         unchanged; see "the limit"
```

### The flow

```
  Brad (Slack): "Moses, build #13"       ← the green light; #13 must already be in the tracker
        │                                    with a scope, and must not already have a target
        ▼
  Moses proposes it (a declared action, not prose)
        │
        ▼
  listener (code) ── sudo moses-mfa challenge new-build X ───▶  root: one open challenge,
        │                                                       purpose stored ROOT-SIDE,
        │   ONE request open at a time: a different project is refused until this
        │   one is answered or expires, so nothing can swap X out from under Brad
        │   posts "Authorize a new build for `X`?                    expires in 5 minutes
        │          Reply with your 6-digit code."
        ▼             ▲ purpose text comes from the helper's output, never from the model
  Brad replies 123456
        │   a 6-digit reply from the owner, matched to the open challenge BEFORE the model is
        │   called. The code is never put in a prompt, an argv, or a log.
        ▼
  listener ── code on STDIN ──▶ sudo moses-mfa verify <challenge>
                                    │  seed: /etc/moses/mfa/totp.seed (root, 0600)
                                    │  window ±1 step · a time step is accepted once
                                    │  3 wrong codes kill the challenge
                                    │  5 failures in 24 hours lock verify for 24 hours
                                    ▼
                               authorization row (root-owned ledger)
                                    │
        ┌───────────────────────────┴───────────────────────────┐
        ▼                                                       ▼
  moses-new-target X (A4)                               knight_land (A5)
  consumes the authorization; makes the                 a branch that ADDS a target file
  project's directory, git init, target .env            lands only if an authorization
  with KT_PUSH_MODE=branch, .brief from the             for that name exists
  scope, knight provision
        │
        ▼
  Knight's first job (A4): the project's scope from the tracker is the brief.
  Green work lands on a branch, as for every branch-mode target.
```

**Why root holds the state, not the listener.** The listener runs as the operator, and so does
every agent. If the challenge's purpose lived in an operator-writable file, a process could swap
`X` for something else between Brad seeing the prompt and typing the code. So the helper keeps the
purpose, the seed, the replay counter and the audit ledger under root, and runs as a single
stdlib-only Python file installed root-owned in `/usr/local/bin` (section 7b: root runs nothing the
operator can write). Its path overrides (`MOSES_MFA_*`) are honored only when it is NOT running as
root, so the tests can run the real script against a scratch directory while a sudo caller cannot
point it at a seed of their own.

**Why the code goes on stdin.** sudo logs the command line to the auth log, and argv is visible in
`ps` while the helper runs. Stdin is neither.

**The brute-force arithmetic.** With a ±1 step window, three codes are valid at any moment, so one
guess succeeds with probability 3 in 1,000,000. Three tries per challenge alone is not enough,
because a process running as the operator can open challenges in a loop. The lockout caps it
at 5 guesses a day: about 1,800 a year, or a 0.55% chance in a year of nonstop trying, and every
failure is an audit row that Birdeye can report on.

### The limit — say what this does NOT cover

- **The operator account.** Anything running as the operator can still write
  `knight/targets/<x>.env` directly, because the registry is the operator's own directory. The code
  gates the paths an *unattended* request takes (Slack → Moses, and Knight's branches). It does not
  replace the sandbox that keeps Knight's own process inside his clone (section 4c), and it does not
  guard a session Brad is sitting at.
- **Brad's phone.** Whoever holds the authenticator holds the gate. The seed is shown once at
  enrollment and never again.
- **The clock.** TOTP depends on Reserve's clock being right; a clock more than 30 seconds off makes
  correct codes fail. That fails closed (it says why), and Brad can see it in the audit ledger.
- **The lockout is a lever.** Five wrong codes in a day lock verify for a day, and any process
  running as the operator can spend them. That trades a day of Brad's new builds for the brute-force
  bound above; the audit ledger shows who spent them.
- **Repointing an existing target is starting a new project** (Brad, 2026-10-17: a code "only when
  Moses/Knight are starting work on a NEW project"). So a branch that changes an existing target's
  `KT_SOURCE` or `KT_PUSH_REMOTE`, or adds an `eval` to it, is refused by knight-land outright, and no
  code unlocks it: the one door into a new folder is the tracker's new build. Every other edit stays
  code-free. The check reads text and never sources the branch, so a changed target file must also
  hold PLAIN SETTINGS only (knight/lib/target-shape.py): `declare "KT_AUTO_""LAND=1"`, `printf -v`,
  `$(…)` and a second statement after `;` are refused for their shape, so what the text says is what
  sourcing the file does.
- **The gate's own files.** A branch that weakens knight-land, new_build.py or the listener lands
  through review like any Moses change, with no code. The code gates new targets, not changes to the
  machinery that checks for them; Zryachiy's review and branch landing are what stand there.
- **Anything outside this machine.** A GitHub repository, a Railway service and DNS are still
  ask-first under commandment 9. A code does not authorize them.

### After the code: building away (A8–A11, 2026-10-17)

Brad's aim for #14, in his words: replace the roadblocks with one code, after which Moses and Knight
"are free to build the project in the ~/Projects folder, put it under version control and build away."
So a target created by moses-new-target carries two settings, and `knight/bin/knight-next` acts on them:

```
  job ends `pushed` ──▶ knight-run starts knight-next <job> (detached: one job at a time)
                          │ KT_AUTO_LAND=1, and ALL of:
                          │   project is active, at `building`, and passes the tracker's build rule
                          │   project is `internal` in the tracker (read now)   ── else "merge it"
                          │   main's gate.sh is real, not the placeholder      ── else "merge it" (first job)
                          │   the branch leaves alone what decides its tests:  ── else "merge it"
                          │     gate.sh, prep.sh, files gate.sh names, test-runner configs
                          │     (conftest, pytest/tox/setup.cfg, tsconfig, jest/vitest…),
                          │     package.json "scripts", pyproject [tool.*]
                          ▼
                        knight-land <job>   (every refusal there still applies)
                          │ merged, KT_CONTINUE=1
                          ▼
                        knight-next --after-land <job>
                          │ stops on: SCOPE COMPLETE on the report's first line · the project paused,
                          │ parked or past `building` in the tracker · KT_CONTINUE_MAX (6) jobs on it
                          │ today · Knight disabled or the day's budget spent. Another job in flight
                          │ is waited for (every 2 min, up to an hour), not a reason to stop.
                          ▼
                        knight start --target <project>  "build the next piece of the scope"
```

Every hold and every stop is said in the conversation the work came from and recorded in
lands.jsonl; a Slack post that fails is recorded as `slack-not-posted`. A job that finds the scope
covered changes nothing and ends `done-not-pushed`, and knight-run hands that to knight-next too, so
the run ends with "scope covered" rather than looking like a failed build. A branch cannot switch these on for another target: knight-land treats KT_AUTO_LAND and
KT_CONTINUE like KT_SOURCE, refused outright. From the Claude app, the `new_build` MCP tool runs the
same code check as Slack, two calls: one opens the request, the next carries the code (Brad accepted
the app's model seeing a code: they last 30 seconds and work once). That tool never raises, because a
raised call's arguments are written to tool-failures.jsonl.

### Where it lives

```
  agent/moses-mfa            the root helper: challenge, verify (code on stdin), authorized, consume,
                             status, enroll. Installed root-owned at /usr/local/bin/moses-mfa.
                             Seed /etc/moses/mfa/totp.seed (0600) · state + audit.jsonl /var/lib/moses/mfa
  agent/new_build.py         the listener's half: `DO: new-build <project>` opens a challenge; a bare
                             6-digit reply from the owner is answered here before anything else reads
                             it; redact() strips codes from every history the model is given
  agent/moses-new-target     runs after a verified code, as the operator: the directory, git, the
                             branch-landing target (committed alone), knight provision, the first job
  knight/bin/knight-land     landable() refuses a branch that ADDS a target with no code waiting,
                             and the land spends the code before merging
  agent/install-mfa.sh       the one install, by the operator, with a password (never in sudoers)
  agent/mfa-suite            every check above in one command, each guard watched failing
  /etc/sudoers.d/moses-mfa   challenge, verify, authorized, consume, status — NOT enroll
```

**Defense in depth, found by the tests.** Twice, removing one guard on purpose left its check
green, because a second barrier stood behind it: moses-new-target's `authorized` check is backed by
`consume` refusing a non-authorization, and knight-land's `landable()` check is backed by spending
the code before the merge. The mutants now remove both, so each pair is watched failing as a pair.

**The gap the suite closes.** Knight's `moses` gate runs every `*_test.py`, so three of the four
suites run on every Moses job. `knight/test-land.sh` is not a `*_test.py` and is not in that gate;
`agent/mfa-suite` runs it, and the installer runs the suite before it changes anything.

---

## 6b. Deliberately not running

**A service that is off on purpose and a service that fell over look identical to a checker.** Brad,
2026-09-04, after the standup reported two faults that were both intended states: *"Moses knows 2
things have drifted but he doesn't know WHY — you know why burtbot isn't active, he does not."*

That was a real disconnect. The reason existed in a commit message and in Claude's head; the standup
reads this document. So intent gets declared HERE, in a table the checker parses, and the reason
travels with it into the report.

<!-- arch-check: deliberately-inactive -->

| unit | why it is off | what would turn it back on |
|---|---|---|
| `burtbot.service` | Its Discord token was hardcoded in `bot.py` and was echoed into a transcript on 2026-09-03. The token now lives in `~/.config/burtbot/burtbot.env` and the source refuses to start without it, but the credential itself is still the leaked one. | Brad rotates the token in the Discord developer portal; then `systemctl --user enable --now burtbot`. |

**The declaration cuts both ways.** A unit listed here that is INACTIVE is correct and reported as
such. A unit listed here that is RUNNING is drift — because something started it without the reason
above being resolved, and nobody noticed. Silence in either direction would be worse than the false
alarm this replaces.

---

## 6. Personas — faces, not workers

`kind` in the roster distinguishes what a thing actually is:

| id | kind | Runs on | Owns |
|---|---|---|---|
| moses | service | Reserve | commandments, roster, standup, capture — deterministic, no model |
| birdeye | job | Reserve | backup result, disk headroom, iDrive quota, handed-off jobs |
| bigpipe | job | Viatica | P&L, Schedule C draft, quarterly tax set-aside |
| tagilla | job | Viatica | support digest, backlog, triage outcomes |
| therapist | job | Reserve | Customs reachability, TLS certs, domain expiry, the dependency canary; also reports Knight's readiness |
| knight | agent | Reserve | writes the code, scaffolds new projects — **active**; has pushed to master since 2026-08-08 |
| zryachiy | agent | Reserve | **reviews Knight's diff before it is pushed** — the last check between a green gate and production |


**Zryachiy is the one to know about, because he is the last thing before customers.** He runs after
the gate, after the rebase onto the current mainline, after the re-gate — and immediately before the
push. Spawned **by the runner, never by Knight**: an agent reading its own diff is a green that was
never able to go red, and a gate the checked thing invokes is still self-review even when it is
compiled.

Three properties, all deliberate:

- `CONFIRMED` **blocks the push**, exactly like a red test.
- `PLAUSIBLE` is **recorded into the commit message and the push proceeds**, so "there might be a
  problem here" can neither quietly stop a build nor quietly disappear.
- **A review that could not run BLOCKS.** Timeout, crash, or unparseable output means nothing ships
  and the report says why. Not "could not look, therefore fine".

He is read-only by construction — no `Edit`, `Write`, `Task` or `Agent` — and `knight/test-review.sh`
proves it (17 checks).

**Where his verdict actually lands depends on the target**, and this is the part worth internalizing:

| target | push mode | what happens after Zryachiy passes |
|---|---|---|
| `viatica` | `direct` | lands on master, **Railway deploys it** — no human in the chain |
| `moses` | `branch` | lands on a branch; a human merges |
| `moses-framework` | `branch` | lands on a branch; a human merges |

So for Viatica, Zryachiy is the only non-Knight judgment between a task and live customers.

"Persona" is only the Slack face (name + emoji) any of these wears. Big Pipe and Tagilla also have
interactive slash-command halves living inside the Viatica app.

## 7. One tree, two audiences — the operator's settings and the public framework (2026-09-21)

The public framework (`github.com/bradwallen/Moses`) used to be a hand-kept copy in a different layout.
Every sync was a per-file translation plus a merge against scrubbing done on the other side, so it was
skipped: 57 commits behind in 18 days, with nothing saying so. Now the framework IS this tree, filtered.

```
  this tree (runs Reserve)                              ~/.config/moses/ (never in any repo)
  ────────────────────────                              ────────────────────────────────────
  agent/ knight/ mcp/  ──── reads ────────────────────▶ moses.env      owner id, channels, MCP URL,
    agent/lib/moses_env.py  (Python)                                   product paths — NOT secrets
    agent/lib/moses-env.sh  (shell, sourced)                           mcp.env / mcp-public.env
                                                                       estate.md  (inventory prose)
  framework.map  every tracked file: ship | private <reason>          slack.env, knight.env … secrets
        │
        ▼  agent/moses-framework-sync [--check]
  ~/Projects/moses-framework   agent/ knight/ mcp/  = byte-for-byte copies of what ships
                               README, LICENSE, docs/, config/, tools/, hooks/, install/, personas/
                                                    = the framework's own, never touched by a sync
        │
        ▼  git push (after the framework's own secret scanner, in its pre-commit hook)
  github.com/bradwallen/Moses
```

- **Nothing personal is written in the code.** Paths come from the operator's home (`moses_env.HOME`),
  Moses's own location from the file's position, and everything else from `moses.env`. Precedence:
  the process environment, then `moses.env`, then a neutral default. Read by the code itself rather
  than handed in per unit, because Moses starts from a dozen places and a setting only some receive is
  drift.
- **Root never sources the operator's files through this.** `moses`, `moses-remediate` and the deploy
  scripts run as root; they resolve the operator inline (`MOSES_OPERATOR_USER`, the sudo caller,
  `/etc/moses/operator`, then account 1000 — what the code hard-coded before — printing that it assumed
  so) and read single values as data. A sourced file
  is code, and the operator's home is writable by every agent that runs as the operator.
- **The sync refuses three things** before writing: a file the map does not classify, a framework
  checkout with uncommitted work, and anything the framework's own secret scanner rejects. It never
  commits. `framework_sync_test.py` watches each refusal fail.
- **Units use `%h`**, systemd's own "this user's home", so the tracked copies are the installed files.

## 7b. Root runs nothing the operator can write (2026-09-21)

Every agent runs as the operator, so anything the operator can write, every agent can write. Four
root-run scripts executed such files, and each was a path from "an agent" to root:

```
  root, 08:00 timer ─▶ /usr/local/bin/moses standup ─┬─ sourced ~/.config/moses/slack.env
                                                    └─ ran agent/moses-drift, project_status.py,
                                                       moses-project from the checkout
  root ─▶ birdeye-customs, therapist ─────────────── sourced knight.env / customs.env
  sudo, NO password ─▶ install-persona-tools.sh ──── sourced knight.env        ← immediate
```

Now: the operator's settings are read as **data** (`op_value FILE KEY` — one line, nothing evaluated)
and the operator's code runs **as the operator** (`as_op`, `runuser -u <operator> -- env HOME=…`).
Root-owned files under `/etc` are still sourced; only root can change them. The tracked sources of the
three persona scripts live in `agent/root/` (private to this machine); `agent/install-root-hardening.sh`
installs them with sudo, once, testing before and after and restoring on failure — and is never to be
granted in sudoers, since a password-free copy of operator files into /usr/local is this same hole.
`agent/root_scripts_test.sh` plants commands in settings files and runs the real scripts; against the
copies installed before the fix it failed 10 of 12, including the standup and birdeye-customs running
the planted commands.
