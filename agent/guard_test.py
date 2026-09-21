"""guard_test — every case must pass BEFORE the agent user or its sudoers file exists.

The asymmetry is the whole point, so the two halves of this file are not equally important:
a missing ALLOW costs one confirmation; a DENY that fails to match leaves a destructive command
armed and unattended. When something here fails, fix the rule — never the expectation.

Run:  python3 guard_test.py
"""

import sys

from guard import check

# (tool, input, expected, what this case is really testing)
CASES: list[tuple[str, dict, str, str]] = [
    # ── Irreversible storage. The drive scenario's dangerous half. ───────────
    ("Bash", {"command": "mkfs.ext4 /dev/sdb1"}, "deny", "format"),
    ("Bash", {"command": "sudo /sbin/mkfs.ext4 /dev/sde1"}, "deny", "format via sudo + absolute path"),
    ("Bash", {"command": "parted /dev/sdb mklabel gpt"}, "deny", "partition"),
    ("Bash", {"command": "wipefs -a /dev/sdc"}, "deny", "wipe signatures"),
    ("Bash", {"command": "dd if=/dev/zero of=/dev/sda bs=1M"}, "deny", "dd to block device"),

    # ── Deleting the irreplaceable ──────────────────────────────────────────
    ("Bash", {"command": "rm -rf /mnt/data1"}, "deny", "recursive delete on the photo drive"),
    ("Bash", {"command": "rm -rf /home/brad/.claude/memory"}, "deny", "delete the project brain"),
    ("Bash", {"command": "rsync -a --delete /tmp/empty/ /mnt/bcm-archive-1/"}, "deny", "rsync --delete empties a drive"),

    # ── Segment bypass: the classic way a first-word check gets beaten ───────
    ("Bash", {"command": "ls; mkfs.ext4 /dev/sdb"}, "deny", "semicolon chain"),
    ("Bash", {"command": "ls && rm -rf /mnt/bcm-archive-1"}, "deny", "&& chain"),
    ("Bash", {"command": "echo $(mkfs.ext4 /dev/sdb)"}, "deny", "command substitution"),
    ("Bash", {"command": "cat /etc/hosts | dd of=/dev/sda"}, "deny", "pipe into a destructive tail"),
    ("Bash", {"command": "df -h; systemctl stop moses.service"}, "deny", "safe head, unsafe tail"),

    # ── The Claude API rule (Brad's constraint) ─────────────────────────────
    ("Bash", {"command": "curl -s https://api.anthropic.com/v1/messages"}, "deny", "direct API call"),
    ("Bash", {"command": "claude -p 'summarize this'"}, "deny", "claude CLI spends off-budget"),
    ("Bash", {"command": "ant beta:sessions create"}, "deny", "ant CLI spends off-budget"),
    ("WebFetch", {"url": "https://api.anthropic.com/v1/models"}, "deny", "API via WebFetch"),
    ("WebFetch", {"url": "https://viatica.travel/itinerary/generate"},
     "deny", "Viatica AI route — costs money, disturbs itinerary parsing"),
    ("Bash", {"command": "curl -X POST $APP/api/ingest"}, "deny", "Viatica ingest invokes Claude"),

    # ── Turning monitoring or backups off ──────────────────────────────────
    ("Bash", {"command": "systemctl stop moses.service"}, "deny", "stop a service"),
    ("Bash", {"command": "systemctl disable reserve-backup.timer"}, "deny", "disable the backup timer"),
    ("Bash", {"command": "crontab -r"}, "deny", "wipe the schedule"),

    # ── Git history and safety-check bypasses ──────────────────────────────
    ("Bash", {"command": "git push --force origin master"}, "deny", "force push"),
    ("Bash", {"command": "git reset --hard origin/master"}, "deny", "discard work"),
    ("Bash", {"command": "git commit --amend -m 'oops'"}, "deny", "amend published commit"),
    ("Bash", {"command": "git commit --no-verify -m 'skip hooks'"}, "deny", "bypass hooks"),
    ("Bash", {"command": "npx prisma db push --accept-data-loss"}, "deny", "accept-data-loss ad hoc"),
    ("Bash", {"command": "chmod 777 /mnt/data1"}, "deny", "chmod 777 is never the fix"),

    # ── Credentials ────────────────────────────────────────────────────────
    ("Bash", {"command": "cat /etc/moses/agents.env"}, "deny", "read own credential"),
    ("Bash", {"command": "cat /home/brad/Projects/brads-travel-project/.env"}, "deny", "read Viatica prod key"),
    ("Read", {"file_path": "/etc/birdeye/birdeye.env"}, "deny", "read Slack token via Read tool"),
    ("Read", {"file_path": "/home/brad/.ssh/id_ed25519_github"}, "deny", "read ssh key"),

    # ── Must ALLOW: read-only inspection and the enumerated ops ─────────────
    ("Bash", {"command": "df -h"}, "allow", "disk usage"),
    ("Bash", {"command": "lsblk"}, "allow", "list block devices — the drive job's first step"),
    ("Bash", {"command": "findmnt -rno TARGET"}, "allow", "list mounts"),
    ("Bash", {"command": "systemctl status moses.service"}, "allow", "read service state"),
    ("Bash", {"command": "systemctl restart moses.service"}, "allow", "restart a documented unit (#17)"),
    ("Bash", {"command": "journalctl -u moses-morning -n 50"}, "allow", "read logs"),
    ("Bash", {"command": "git status"}, "allow", "read repo state"),
    ("Bash", {"command": "moses roster"}, "allow", "its own tooling"),
    ("Bash", {"command": "birdeye list"}, "allow", "its own tooling"),
    ("Bash", {"command": "ls -la /mnt/data1"}, "allow", "listing is read-only even on /mnt"),
    ("Bash", {"command": "df -h && lsblk"}, "allow", "chain of two allowed segments"),
    ("Read", {"file_path": "/home/brad/.claude/memory/ideas.md"}, "allow", "read the corpus"),
    ("Grep", {"pattern": "backup"}, "allow", "search"),
    ("WebSearch", {"query": "prisma migrate"}, "allow", "research — the phase 1 capability"),
    ("WebFetch", {"url": "https://www.prisma.io/docs"}, "allow", "ordinary web read"),

    # ── Must ASK: not dangerous enough to ban, not safe enough to assume ────
    ("Bash", {"command": "rsync -a /mnt/data1/ /mnt/data2/"}, "ask",
     "800GB copy deserves one confirmation before it starts"),
    ("Bash", {"command": "systemctl restart nginx"}, "ask", "restart of an unlisted unit"),
    ("Bash", {"command": "apt-get install ncdu"}, "ask", "installing software"),
    ("Bash", {"command": "./some-script.sh"}, "ask", "unknown script"),
    ("Write", {"file_path": "/home/brad/notes.md"}, "ask", "writing a file"),
    ("Bash", {"command": "df -h; ./unknown.sh"}, "ask", "one unknown segment makes the line ask"),
]


def main() -> int:
    failed: list[str] = []
    for tool, payload, expected, why in CASES:
        v = check(tool, payload)
        if v.decision != expected:
            arg = payload.get("command") or payload.get("url") or payload.get("file_path") or payload
            failed.append(
                f"  {tool}: {str(arg)[:64]!r}\n"
                f"    expected {expected}, got {v.decision} (rule {v.rule})  — {why}"
            )

    total = len(CASES)
    denies = sum(1 for c in CASES if c[2] == "deny")
    if failed:
        print(f"FAILED {len(failed)} of {total}\n")
        print("\n".join(failed))
        print("\nFix the rule, never the expectation. A deny that does not match is a live hazard.")
        return 1

    print(f"all {total} cases pass  ({denies} deny, "
          f"{sum(1 for c in CASES if c[2] == 'allow')} allow, "
          f"{sum(1 for c in CASES if c[2] == 'ask')} ask)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
