# Fork roadmap

Parked work for the `DXT2000/garmin_mcp` fork. Issues are disabled on this fork, so
this file is the tracker.

## Port to mcp 2.x

`pyproject.toml` caps `mcp` below 2. Parked by choice, not blocked: everything below
was measured or verified on 2026-10-23, against mcp 1.30.0 (newest 1.x, security
fixes only from here on) and 2.3.0 (newest 2.x).

### What 2.x would give this server

Protocol revision 2026-07-28, in which a tool can ask the user a question mid-call
(it returns an `InputRequiredResult`; the client asks, then repeats the call with
the answer). The delete and confirm tools could ask "are you sure?" instead of the
current call-again-with-`confirm=true` pattern, which stays safe in the meantime.

Verified with Claude Code 2.1.309, through a logging relay around a probe server on
mcp 2.3.0: it opens with `server/discover` at 2026-07-28 (no `initialize`), sends
every request at that revision, and declares form and URL elicitation. In an
interactive session a test question came back `accept` with both a boolean and a
free-text field filled in.

### What porting costs

Measured in a spike that ported fork main `6c22106` to mcp 2.3.0. The spike is the
local branch `spike/mcp2` in one clone, not pushed; its commit message has the detail.

- Source: only `__init__.py`. The transport settings (`host`, `port`,
  `stateless_http`) move from the constructor to `run()`, and stdio takes none.
- Tests, all mechanical: the import rename; `call_tool()` now returns a
  `CallToolResult`, so `result[0][0].text` becomes `result.content[0].text`;
  camelCase attributes become snake_case (`input_schema`, `read_only_hint`, ...);
  the annotations dump needs `by_alias=True` to keep wire keys.
- With those, 1278 of 1279 tests passed. The one left is the decision below.
- Unchanged, checked live: the HTTP bearer-token gate (401 without or with a wrong
  token), and a 2025-era client (mcp 1.30) still connects at 2025-11-25.
- Small: pass `version=` to `MCPServer`, which otherwise reports an empty version.
- Not checked in the spike: the SSE transport, the e2e tests, the DXT generator
  script, and write tools on 2.x.

### The open decision

On 2.x an exception a tool does not catch reaches the model only as
`Error executing tool <name>`; the cause goes to the server log. Hints this fork
carries in exception text ("re-run garmin-mcp-auth", rate-limit waits) would stop
reaching Claude from any tool that lets them escape. Either raise `ToolError` for
anticipated failures (2.x passes its message through) or catch in each tool.
In the spike, 28 of 157 tools had code outside a broad `try/except` (a static count,
so an upper bound); each needs that review. `test_tool_exceptions_still_surface`
encodes the 1.x behaviour and has to change with whichever choice is made.

### When to port

Upstream (Taxuspt) has the same cap. Porting here first means later upstream syncs
conflict in the server setup and in 20+ test files. Port when upstream has ported
(then sync instead), when a 1.x security issue goes unfixed, or when the
confirmation prompts are wanted enough to carry those conflicts.

Done when: tests pass on mcp 2.x, the crash-message decision above is applied, and
the server starts through the `uvx` command in `~/.claude.json`, answers a read
tool, and refuses a `power.between` upload.

## Keep the fork's side when syncing upstream

Upstream still documents absolute cycling watts as target id 6 / `power.between`.
The fork refuses that form (`_POWER_BETWEEN_ERROR` in `workouts.py`). A sync that
conflicts on `KNOWN_TARGET_TYPE_IDS`, the `upload_workout` docstring or
`workout_templates.py` target types must keep the fork's version.

The same applies to strength workouts: the fork defaults `create_strength_workout`'s
`resolve_exercises` to true and uploads no_match exercises as free text. Upstream #238,
if merged as written, defaults to false and blocks on any unresolved exercise.

## Upstream PRs copied unmerged, and what was left

Reviewed 2026-10-26 against upstream's 40 open PRs. Copied into the fork with
their authors: #478, #482, #483, #484, #485, #486, #487, #488, #471, #417, #455
(fork PR #11) and #238 (strength exercise catalog). When upstream merges any of
them, possibly in a revised form, the next sync conflicts in those files; prefer
upstream's merged version unless it drops a fork decision recorded above.

Left for later, each its own change:

- **#476 `update_workout`** — edit a workout in place instead of delete +
  re-upload (which changes its id and drops its calendar entries). Conflicts with
  current main.
- **#481 Garmin Personal API** — Garmin's official read-only API (opened
  2026-10-21) as a second backend beside the SSO login. Large; evaluate before
  copying.
- **#251's `note` field** for strength steps (appended to the description). Its
  time/distance end conditions are already in the fork.
- **#444 `GARMIN_TZ`** — not copied: written against garminconnect 0.3.2. With
  0.3.20's own profile-timezone handling its host-local conversion would shift
  writes when the host zone differs from the Garmin profile zone.

Upstream comments worth posting (not posted; each needs the user's go-ahead):
#194 (same fix as fork #5, verified independently), #345 and #344 (the fork's
measured garminconnect 0.3.20 and mcp 2.x results), #422 (likely fixed by
garminconnect 0.3.20).
