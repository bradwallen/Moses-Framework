# The rules, and the incidents that produced them

Every rule here is here because its absence cost a day. They are written as claims you can check
rather than advice you can nod at, and each one names the failure that taught it.

---

## 1. Prose is the weakest layer

A rule a model is *asked* to follow is a request. A rule enforced by a hook, a permission gate, or a
file it cannot read is a mechanism.

**The incident.** Ten operating rules were injected in full into every single prompt — and the same
mistake kept happening anyway. The fix was not an eleventh rule. It was a `Stop` hook that reads the
drafted response and refuses to send it.

**How it shows up in this repo.** The handoff gate. The secret scanner installed as a pre-commit
hook. The proposal gate, where a human's confirmation is matched by code and the model never decides
it heard a yes. The permission allowlist that names every tool explicitly.

**The corollary that costs more.** *A guard you have never seen go red is decoration.* Every guard
here ships with a self-test that deliberately breaks it and asserts the failure. Run
`tools/scan-secrets.sh --selftest` and watch it catch six real token shapes and ignore four
legitimate ones.

---

## 2. A check that could not look must never say "broken"

"I found nothing wrong" and "I never looked" are the same sentence unless the probe says which.

**The incident.** A health check reported *"cannot reach the push remote"* at 08:00. The remote was
fine — the check ran as `root` while the repository, the SSH identity and the push all belonged to
another user. It reported its own blindness as a fact about the world.

Fixing the identity was not the fix. As the peer agent that sharpened this put it: *"the day the key
rotates or HOME moves, it lies again the same way, because a null result still renders as a negative
one."*

**The durable version.** Every probe emits its coverage — `ran X as <user> · actually looked:
yes/no (why not)` — and scope is tracked separately from result. A second instance was found the same
day: a reachability summary printed `UNREACHABLE` while the site returned HTTP 200, because the
config it needed was unreadable by the user running it.

**Generalize it.** Any tool that reports a verdict must be able to say what it examined. If it
cannot, its verdicts are unfalsifiable.

---

## 3. Verify; never assert what you have not observed

Do not name a cause you have not seen — get the error, the log, the status, or build the instrument
that shows it.

**The incidents.** A referrer allowlist and a browser extension were both blamed for a bug that
turned out to be a race condition. A cost report was declared wrong when a single API page had been
summed as if it were the total.

**And the sharper half — test the seam, not the logic.** Five times in two days, logic verified in
isolation was correct while the code calling it was broken:

- A function that set a global, called inside `$( )` — the subshell ate the result, and a correct
  configuration was reported as six missing permissions.
- `grep -q` inside a pipeline under `set -o pipefail` — the match killed the upstream process, and
  the pipeline reported the *match* as a failure. It passed in a scratch shell with no `pipefail`.
- A deploy manifest that omitted a file: every test green, the service dead at boot.
- A test fixture inventing a payload shape the vendor never produces — two features silently broken
  while their tests passed.
- A test that redirected state by patching a module attribute the target had already bound as a
  default argument. It wrote to the real data.

**Build fixtures from a captured real payload, never from what it ought to look like.** And when two
independent things break at once, suspect the shared assumption rather than debugging each.

---

## 4. A limiter that reaches a human is a bug

Caps, rate limits and budgets exist to bound machines talking to machines. The instant one makes the
agent ignore *you*, it is a fault wearing a safety feature's clothes.

**The incident.** A minimum gap between replies was set to 45 seconds. The other agent answered in
1–9 seconds, so every reply was refused and the agent looked dead. Worse, the gap was measured from
*any* post — so answering a human locked the agent out of the conversation that answer had started.

**The lesson was not "tune it lower."** A delay never made the loop shorter; only the reply cap does
that. It was removed, and the tests now assert that no such constant exists, so re-adding one reads
as a deliberate act rather than a safety improvement.

**What actually bounds it:** the agent may reply at most N times in a row without a human speaking.
A human speaking resets it — self-limiting, no timer, no state to go stale.

---

## 5. Bounding beats forbidding

**The incident.** The first loop guard was absolute: *never answer another bot*. The reasoning was
that two agents answering each other have no natural stopping point. The risk was real. The
assumption underneath it — that there was no upside — was not checked, and was wrong. Once the
exchange was bounded rather than banned, the other agent twice contributed a lesson worth keeping.

**Generalize it.** When you catch yourself refusing a capability outright, check whether what you
actually need is a bound.

---

## 6. Enhancements degrade; they never crash

A model failure makes the agent quieter, not absent. The deterministic half keeps answering
commands. A decorative feature must never take down the content around it — core functionality fails
loudly instead.

**The incident.** A transient model error was surfaced by dumping the raw JSON payload into a shared
channel, and the cost of the failed run was recorded as `$0.00` because the code raised on the exit
status and discarded a perfectly good response body. Now: parse first, retry once, charge both
attempts, show a sentence, and log the payload where it can be read later.

---

## 7. Deliver finished work; do the homework before any handover

One finished artifact and one command. Phases go inside the script, not across conversation turns.

**Never design around a limitation and call it judgement.** "I don't have permission" is usually a
consequence of where a file was put. A service that binds no port and touches only your files belongs
in your home directory under a systemd *user* unit — where deploying it needs no privilege at all.
Six root round-trips in one evening were the cost of learning that; the move took twenty minutes.

**And when a handover is genuinely unavoidable**, do every check you can first and write down what
you checked and what you could not. A task handed over that you could have finished is the failure.

---

## 8. Silence is a report

A job that says nothing is indistinguishable from a job that did not run. Every persona reports even
when there is nothing to say — including *"nothing came in today"* — so an absence is a failure with
a name on it rather than an unnoticed gap.

The inverse matters too: a clean morning stays quiet, because a daily wall of green trains you to
skim, and then you miss the one that matters.
