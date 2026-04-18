# KNOWLEDGE — restore_last_changed

## Current status (2026-04-17)

The integration is **working end-to-end** in the Docker dev environment. A full restart-cycle test confirms pre-restart timestamps are restored both in the state machine and via the REST API / frontend.

Verified flow:
1. Toggle `input_boolean.test_one` (on), `input_boolean.test_two` (on), `input_number.test_number` to 55 — `last_changed` becomes toggle time.
2. `docker compose restart homeassistant`.
3. After boot + 5s delay, `last_changed` matches the pre-restart timestamps exactly. `match=True` in logs AND the REST API `/api/states/<entity>` returns the mutated value.

## Bugs fixed this session

### 1. Wrong recorder row picked — `get_last_state_changes(1)` returns the post-startup state

**File:** `__init__.py` `_fetch_last_recorded_state()` / `_fetch_blocking()`

The original code called `history.get_last_state_changes(hass, 1, entity_id)`, which returns the most recent state row for the entity. But HA writes a fresh state row on every startup (e.g. `input_number` resets to `initial: 0` and `input_boolean` rehydrates via `RestoreEntity`). So "the latest state" IS the post-restart state, not what we want.

**Fix:** Fetch a batch of recent rows (`get_last_state_changes(hass, 50, entity_id)`), then walk from newest backward and return the first row with `last_updated < recorder.recorder_runs_manager.recording_start`. `recording_start` is captured inside `RecorderRunsManager.__init__` (at recorder construction, before any entity platform runs) — so it's a rock-solid cutoff between "previous session" and "this session" rows.

**Also tried and abandoned:** `state_changes_during_period(..., descending=True, limit=1)` — the SQL it generates does `ORDER BY last_updated_ts ASC LIMIT 1`, so it returns the OLDEST row in the range, not the newest. The `descending` flag only affects the final Python list ordering. See `_state_changed_during_period_stmt` in `homeassistant/components/recorder/history/__init__.py`.

### 2. Mutation applied in state machine but REST API still returns old timestamps

**File:** `__init__.py` `_apply_mutate()`

After mutating `last_changed` / `last_updated` on the live `State` object, `hass.states.get()` returned the new values, but `/api/states/<entity>` still returned the old ones. The REST serializer (`State.as_dict_json` via `as_dict`) reads `self.last_updated_timestamp`, which is a `__slots__` attribute — *not* a `propcache` property, and *not* stored in `_cache`.

**Fix:** Also `setattr(state_obj, "last_updated_timestamp", recorded.last_updated.timestamp())` and pop `_as_dict` / `_as_read_only_dict` from `_cache` so they rebuild from the new slot values.

### 3. `NotImplementedError: __delete__` on cached properties (pre-existing, fixed earlier)

The original code tried `delattr(state_obj, "last_changed_timestamp")`. Current HA uses `propcache` (a C-extension cached property implementation) which raises `NotImplementedError` on `__delete__`.

**Fix:** pop keys from `state_obj._cache` directly.

### 4. `database without the database executor` warning (pre-existing, fixed earlier)

Used `hass.async_add_executor_job()` for recorder work. HA wants `recorder.get_instance(hass).async_add_executor_job()` for DB calls.

## Key discovery: HA State object internals (stable branch, 2026-04)

- `State` uses `__slots__` only. Slots include: `_cache`, `attributes`, `context`, `domain`, `entity_id`, `last_changed`, `last_reported`, `last_updated`, **`last_updated_timestamp`**, `object_id`, `state`, `state_info`.
- `last_updated_timestamp` is a **slot**, written directly by `State.__init__`. Serializers (`as_dict`) read it directly. Must be updated when mutating `last_updated`.
- `last_changed_timestamp`, `last_reported_timestamp`, `as_dict_json`, `json_fragment`, `as_compressed_state`, `as_compressed_state_json`, `_as_dict`, `_as_read_only_dict`, `name` are `@under_cached_property` — stored in `state_obj._cache` dict.
- `setattr` works for slot attributes, not for `@under_cached_property` entries. Use `_cache.pop(key, None)` for those.

## Key discovery: recorder session boundaries

- `recorder.get_instance(hass).recorder_runs_manager.recording_start` → datetime of current session start (set very early, before entity setup).
- The `recorder_runs` table has a row per session: `start`, `end`, `closed_incorrect`, `created`. The current session has `end=None`.
- State rows written during "this" session will have `last_updated_ts >= recording_start`; pre-session rows will be strictly less.

## Remaining work (not blocking)

- The `async_set`, `async_set_force`, and `replace` strategies haven't been re-tested against current HA. The `mutate` strategy (default) now works, so this is low priority.
- Integration doesn't filter by `last_changed_ts` — it treats the most recent pre-session row as "the state" even if only `last_updated` changed (e.g. attribute-only update). That matches current behaviour but worth documenting if users ask.

## Dev environment

- Docker compose in `dev/` — `docker compose restart homeassistant` picks up source changes (bind-mounted).
- Config in `dev/config/configuration.yaml` — has logger at debug for the integration, plus `input_boolean.test_one`, `input_boolean.test_two`, `input_number.test_number`.
- Local HA user is `remy` / `remy-mcp`. Get a bearer token via `/auth/login_flow` → `/auth/token` (code example in test scripts from this session).
- REST API `/api/services/restore_last_changed/dump` and `/api/services/restore_last_changed/restore` work for hand-testing mid-session.
