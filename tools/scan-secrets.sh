#!/usr/bin/env bash
# scan-secrets.sh — refuse to commit anything credential-shaped.
#
#   tools/scan-secrets.sh              scan what is STAGED (what the pre-commit hook runs)
#   tools/scan-secrets.sh --all        scan every tracked file
#   tools/scan-secrets.sh --selftest   prove it still catches things
#
# WHY A HOOK AND NOT A RULE. Commandment 2 says secrets never live in the repo, and a .gitignore only
# protects files nobody force-adds. This is the layer that actually holds: it reads the staged
# content, and a match stops the commit. Prose is the weakest layer — see the handoff gate for the
# same lesson learned the same way.
#
# THE FALSE-POSITIVE PROBLEM IS REAL, and getting it wrong is how a guard gets disabled. This repo
# legitimately contains credential PATTERNS (the redactors in diagnose.py and transcript_index.py)
# and obvious test fixtures ("xapp-test", "sk-ant-api03-AAAABBBBCCCC"). A scanner that fires on
# those gets bypassed within a week, and a bypassed scanner protects nothing. So the patterns below
# require enough real entropy to be an actual token, and there is an explicit allowlist for the two
# files whose job is to describe secrets.
#
# Install it (bootstrap.sh does this for you):
#   ln -sf ../../tools/scan-secrets.sh .git/hooks/pre-commit

set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo .)" || exit 0

MODE="${1:-staged}"

# Real tokens, not the shapes of tokens. Length requirements are what separate a live credential
# from a redaction rule or a fixture.
PATTERNS='xox[baprs]-[0-9]{8,}-[0-9]{8,}-[A-Za-z0-9]{16,}
xapp-[0-9]-[A-Z0-9]{8,}-[0-9]{10,}-[a-f0-9]{32,}
sk-ant-api[0-9]{2}-[A-Za-z0-9_-]{40,}
sk_live_[A-Za-z0-9]{24,}
rk_live_[A-Za-z0-9]{24,}
whsec_[A-Za-z0-9]{24,}
re_[A-Za-z0-9]{8}_[A-Za-z0-9]{16,}
ghp_[A-Za-z0-9]{36,}
github_pat_[A-Za-z0-9_]{40,}
-----BEGIN (RSA|OPENSSH|EC|PGP) PRIVATE KEY-----
postgres(ql)?://[^:[:space:]]+:[^@[:space:]]{8,}@'

# Files whose PURPOSE is to describe credential shapes. Named explicitly, never by glob — an
# allowlist you cannot read is an allowlist nobody audits.
ALLOW='agent/diagnose.py
mcp/transcript_index.py
agent/moses-inventory
tools/scan-secrets.sh'

allowed() { printf '%s\n' "$ALLOW" | grep -qxF "$1"; }

# $1 = label, $2 = the content itself. NOT stdin.
#
# The first version took content on stdin and then looped the patterns with `<<< "$PATTERNS"` — which
# REPLACES stdin for the whole loop, so every grep read the pattern list instead of the file. It
# reported clean on a file containing a live-shaped Slack token, and the pre-commit hook let that
# commit through. Caught only by deliberately trying to commit a leak; a guard nobody has watched
# fail is decoration.
scan_content() {
    local label="$1" content="$2" hit=0 line
    while IFS= read -r pat; do
        [ -n "$pat" ] || continue
        if line=$(printf '%s\n' "$content" | grep -nE "$pat" 2>/dev/null); then
            printf '  %s\n' "$label"
            printf '%s\n' "$line" | head -3 | cut -c1-160 | sed 's/^/      /'
            hit=1
        fi
    done <<< "$PATTERNS"
    return $hit
}

FOUND=0
if [ "$MODE" = "--selftest" ]; then
    echo "── scan-secrets self-test ──────────────────────────────"
    ok=0
    # Things it MUST catch. The two fixtures below are written as two joined halves: the shell
    # sticks them back together, so the value under test is unchanged, but no token-shaped
    # literal sits in this file. GitHub's own push protection reads the SOURCE and refused the
    # first push of this repository over exactly these two lines (2026-09-24) — a scanner whose
    # fixtures trip every other scanner is a scanner nobody can publish.
    for bad in \
      'xox''b-12345678-87654321-AbCdEfGhIjKlMnOpQrStUvWx' \
      'sk-ant-api03-AAAAAAAAAABBBBBBBBBBCCCCCCCCCCDDDDDDDDDDEEEE' \
      'sk_''live_AAAAAAAAAABBBBBBBBBBCCCC' \
      'ghp_AAAAAAAAAABBBBBBBBBBCCCCCCCCCCDDDDDDDD' \
      'postgresql://user:supersecretpw@db.example.com/x' \
      '-----BEGIN OPENSSH PRIVATE KEY-----'; do
        if printf '%s\n' "$bad" | grep -qE "$(printf '%s' "$PATTERNS" | tr '\n' '|' | sed 's/|$//')"; then
            echo "  caught   ${bad:0:34}…"
        else
            echo "  MISSED   ${bad:0:34}…"; ok=1
        fi
    done
    # Things it must NOT catch — the redaction patterns and fixtures that live here legitimately.
    for good in \
      'SECRET = re.compile(r"(sk-ant-[A-Za-z0-9_\-]+|whsec_[A-Za-z0-9]+")' \
      '"SLACK_BOT_TOKEN": "xoxb-test"' \
      'for secret in ["sk-ant-api03-AAAABBBBCCCC", "xoxb-123456-abcdef"]' \
      'grep -qE "sk_live_|whsec_"'; do
        if printf '%s\n' "$good" | grep -qE "$(printf '%s' "$PATTERNS" | tr '\n' '|' | sed 's/|$//')"; then
            echo "  FALSE +  ${good:0:44}…"; ok=1
        else
            echo "  ignored  ${good:0:44}…"
        fi
    done
    echo
    [ "$ok" = 0 ] && echo "  self-test OK — catches real tokens, ignores the patterns that describe them" \
                  || echo "  SELF-TEST FAILED" >&2
    exit "$ok"
fi

if [ "$MODE" = "--all" ]; then
    while IFS= read -r f; do
        allowed "$f" && continue
        [ -f "$f" ] || continue
        scan_content "$f" "$(cat "$f")" || FOUND=1
    done < <(git ls-files)
else
    while IFS= read -r f; do
        allowed "$f" && continue
        scan_content "$f" "$(git show ":$f" 2>/dev/null)" || FOUND=1
    done < <(git diff --cached --name-only --diff-filter=ACM)
fi

if [ "$FOUND" -ne 0 ]; then
    cat >&2 <<'MSG'

  COMMIT REFUSED — that looks like a real credential.

  Secrets never live in the repo (Commandment 2). Move it to an env file outside the tree, or to
  a *.env.example with the value stripped.

  If this is genuinely a false positive, add the file to the ALLOW list in tools/scan-secrets.sh
  with a comment saying why — do NOT pass --no-verify. A guard you routinely bypass is one you
  have already removed.
MSG
    exit 1
fi
exit 0
