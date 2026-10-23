# Fork roadmap

Parked work for the `DXT2000/garmin_mcp` fork. Issues are disabled on this fork, so
this file is the tracker.

## Port to mcp 2.x

`pyproject.toml` caps `mcp` below 2: mcp 2.x renamed `mcp.server.fastmcp` to
`mcp.server.mcpserver` (`FastMCP` to `MCPServer`), and the server is not ported yet.
Parked 2026-10-23, when mcp 1.30.0 was the newest 1.x and 2.3.0 the newest 2.x.

Scope: every `FastMCP` import and construction (`git grep FastMCP`), the test
fixtures that build an app, then lift the cap. Upstream (Taxuspt) has the same cap,
so check whether they have ported before doing it here; a port of our own would
conflict with theirs on every sync.

Done when: tests pass on mcp 2.x, and the server starts through the `uvx` command
in `~/.claude.json` and answers a read tool and a refused `power.between` upload.

## Keep the fork's side when syncing upstream

Upstream still documents absolute cycling watts as target id 6 / `power.between`.
The fork refuses that form (`_POWER_BETWEEN_ERROR` in `workouts.py`). A sync that
conflicts on `KNOWN_TARGET_TYPE_IDS`, the `upload_workout` docstring or
`workout_templates.py` target types must keep the fork's version.
