# Fork roadmap

Parked work for the `DXT2000/garmin_mcp` fork. Issues are disabled on this fork, so
this file is the tracker.

## Redesign direction

Written 2026-10-26 after a review of the latest MCP spec (2026-07-28), the Python
SDK (2.3.0), garminconnect (0.3.20), Garmin's new Personal API, and Anthropic's
tool-design guidance. Nothing here is started. It is the order to work in, not a
rewrite plan: each step is its own change, and the old tools keep working until a
step replaces them.

### What the server is for

This fork has two audiences, and the design has to serve both:

- **General users**, such as a colleague installing the fork: the server is
  their only path to their Garmin data, so the read tools matter.
- **The owner's coach**, which reads training history from a separate, private
  sync job into a database, not from this server. For that setup, this server's
  job is writes (workouts, schedule, gear, races, zones, strength sets) plus the
  live reads that sync does not store: the calendar, a workout before editing it,
  gear before changing it, and the extras the coach skill names.

So the read tools stay, but a client that already has a history store should be
able to hide them. A tool profile is the existing mechanism for that
(`tool_profiles.py`); a `coach-actions` profile with the writes and the live
reads is cheaper than a second server.

### Garmin Personal API (opened 2026-10-21)

Garmin now gives individuals a read-only API: create a key under Garmin Connect
settings, exchange it for a 24-hour bearer token at
`personal.connectapi.garmin.com`, then call GET endpoints. There are no
passwords, no MFA, and no SSO login to get blocked. Sources: Garmin's Personal API
FAQ and its official client, `github.com/garmin/connect-personal-api-python`.

- What its client wraps (24 endpoints, read from its README): daily summaries,
  sleep, HRV, Body Battery, stress, steps, heart rate, SpO2, heart-rate zones,
  fitness age, weight, food logs, menstrual cycle, activity search, activity
  detail, and the original activity file.
- What it does not list: training readiness, training status, training load,
  VO2 max, workouts, calendar and gear. The client makes only GET requests.
  Upstream PR #481 says writes (Training and Courses) stay on Garmin's business
  tier; Garmin's FAQ does not say so in as many words.
- Unverified: upstream PR #481 claims the gateway serves the same
  `/<service>-service/...` paths as the web app, so more would answer than the
  client wraps. No key has been tried against it yet.

Direction: a hybrid backend. Reads go through the Personal API where it answers,
and through the SSO session only for what it does not cover and for every write.
That takes most traffic off the fragile login. The same applies to the private
sync job, which would benefit more than this server does, because it runs
unattended and cannot answer an MFA prompt. Evaluate #481 for this, with a live
key, before copying it.

### Steps, in order

1. **Port to mcp 2.x.** See the next section; the spike and its open decision are
   there. Version 1.x gets only critical fixes from here on, so this step stops
   being optional over time.
2. **Real errors and structured results.** 198 places in `src/garmin_mcp/` return
   failures as text (`return f"Error..."`), and none raises `ToolError`, so a
   failure reaches the client as a normal result. The registration wrapper also
   sets `structured_output=False` for every tool, so results are JSON inside a
   text block. Change to: anticipated failures raise `ToolError`, so they arrive
   as `isError: true` with the message intact (this also settles the 2.x open
   decision). Curated tools declare an output schema. Field names carry units
   (`power_w`, `pace_s_per_km`), because the model does arithmetic on them.
3. **Hybrid backend.** As described above.
4. **Use the library instead of direct HTTP.** garminconnect 0.3.19 added gear v2,
   calendar notes, `update_power_zones`, swim lengths and more, and 0.3.20 can
   resume an MFA login in another process. Where a fork tool makes its own HTTP
   call that now has a library method, switch to it, so Garmin-side changes get
   fixed upstream once. Route every call through one place that handles errors,
   timeouts, the profile-timezone "today" and caching. Cache data that rarely
   changes (zones, gear lists, the exercise catalog) and data that never changes
   (a finished activity).
5. **Timeouts that end the call.** `_GarminProxy` runs each call on a daemon
   thread and stops waiting after the timeout, but the thread keeps going. For a
   write, Garmin may still apply it after the tool reported a timeout.
   `write_errors.py` tells the model the outcome is unknown, but the cause stays.
   Use a timeout on the HTTP request itself, and read back after every write.
   Unverified: whether garminconnect exposes a per-request timeout.
6. **Confirmation by the user, not the model.** On 2026-07-28 a tool can ask the
   user directly (elicitation, verified in Claude Code, see the next section).
   Deletes and `set_power_zones` should ask the person instead of trusting
   `confirm=true`, which the model can set by itself. Keep `confirm` as the
   fallback for clients that do not support elicitation.
7. **Task-shaped tools, measured with evals.** Write about 30 realistic coach
   questions with checkable answers. Measure tool choice, call count, tokens and
   correctness. Then add tools shaped like the questions, for example a training
   snapshot for a date (readiness, HRV, sleep, load and status in one call) or an
   activity review (splits, zones, laps, swim lengths and PRs in one call), and
   retire the overlapping narrow tools. Today's tests prove the code is correct;
   nothing measures whether a model uses the tools well. Clients that load tools
   on demand make 193 definitions cheap. Overlapping tools still cost accuracy,
   so the count is not the target; the evals are.
8. **Ship the house rules with the server.** Put the stable workout-building rules
   (target type ids, the `power.between` refusal, strength matching) in the
   server's `instructions`. Today they live only in a client-side skill and
   drift. Later, the Skills-over-MCP extension can serve the whole skill
   (finalised spec; client support still being built).
9. **Smaller items.**
   - Declare annotations on the tool itself instead of in the separate
     `tool_annotations.py` registry, so there is one source of truth.
   - Once step 7 has shrunk the overlap, see whether the profiles and
     `GARMIN_DISABLED_TOOLS` can collapse into one read-only switch.
   - Later: an MCP Apps view (week calendar, load chart) for clients that render
     it.

### Not planned

- A rewrite from scratch. Auth, patch-style updates, preview-then-confirm, the
  strength matcher and the deliberate-break tests are sound; a rewrite would
  reach today's features late and with new bugs.
- Running this server against the history database. The coach already queries
  that database through its own connector.
- Tracking upstream closely. Upstream merges in batches (the latest on
  2026-10-20) and leaves many PRs open for a long time. Sync when a batch lands,
  and keep the fork decisions recorded in this file.

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

- **#476 `update_workout`** — done differently: the fork wraps garminconnect's own
  `update_workout` (fork PR #14), so #476 itself is no longer needed.
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

## garminconnect endpoints deliberately not wrapped

Fork PRs #14-#18 wrapped most of garminconnect 0.3.20's unused endpoints. These
were looked at and left out; check the reason before adding one.

- **Already covered by an existing tool**: `get_scheduled_workouts_by_period`
  (adds only Garmin Coach adaptive plans), `get_next_scheduled_workout`,
  `get_primary_event`, `get_upcoming_events`, `get_calendar_event`,
  `move_calendar_item`, `get_heart_rate_zones_v2`, `update_heart_rate_zones`,
  `get_current_biometrics`, `get_daily_training_status`,
  `get_weekly_training_status`, `get_activity_laps`, `update_activity`,
  `get_last_activity`, `get_hydration_all_data`, `get_daily_sleep` (same payload
  as `get_sleep_data`), `get_activity_gear_v2`, `set_gear_default`.
- **Returned no data when checked**: `get_activity_step_details`
  (detailsAvailable false; `get_activity_splits` carries wktStepIndex per lap
  instead), `get_pace_bands`.
- **Not needed for current use**: the jet-lag adviser (`get_jetlag_*`,
  `add/update/delete_jetlag_trip`); ECG waveforms (`get_ecg` detail, ~50k chars)
  and Health Snapshot epochs/FIT downloads, since `get_health_recordings` lists
  the summaries.
- **Out of scope**: Garmin Coach training plans, golf, badges, privacy settings,
  PDF reports, most menstrual-cycle writes, device/user settings writes
  (`update_device_settings`, `update_user_settings`).
