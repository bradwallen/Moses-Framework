# The units that actually run Moses

Copies of what is installed in `~/.config/systemd/user/`. **This directory is a record, not the
deployment** — systemd reads the installed files, and these are here so a unit is reviewable, has a
history, and can be restored.

Before this existed, the repo tracked exactly two unit files and **both were wrong**: pre-migration
copies still pointing at `/opt/moses/mcp_server.py`, from before the MCP server moved to a user
service. The sixteen files that were really running were tracked nowhere at all. A stale unit in a
repo is worse than an absent one, because it answers the question confidently.

`moses-drift` compares every file here against its installed counterpart and reports both directions:
a copy that no longer matches what runs, and an installed Moses unit with no copy here. That check is
the reason this directory can be trusted; without it this is just another place to go stale.

`system/` holds the two root-owned units — the Slack listener and the morning run — which live in
`/etc/systemd/system/`. They are readable, they are part of how Moses runs, and no unit here contains
a credential: every one of them reads its secrets from an `EnvironmentFile` outside the repo.

Symlinking the installed units at these files was the obvious alternative and was rejected: it welds
systemd to a repository path, and this repository is about to move.
