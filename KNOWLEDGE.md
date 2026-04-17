# KNOWLEDGE — restore_last_changed

## Current status (2026-04-17)

The integration is **working** in the Docker dev environment. All three test entities restore with `match=True` and no errors in logs.

The Docker dev instance is running at **http://localhost:8123** (onboarding completed). Managed from `dev/` directory with `docker compose`.

## Bugs fixed this session

### 1. `NotImplementedError: __delete__` on cached properties

**File:** `__init__.py` `_apply_mutate()` (~line 246)

The original code tried to `delattr()` cached timestamp floats (`last_changed_timestamp`, `last_updated_timestamp`, `last_reported_timestamp`) to invalidate them. Current HA uses `propcache` (a C-extension cached property implementation) which raises `NotImplementedError` on `__delete__`.

**Root cause:** `propcache` descriptors don't support deletion or direct setting — they're read-only computed properties backed by a `_cache` dict on the State object.

**Fix:** Instead of `delattr()`, pop keys from `state_obj._cache` directly. This invalidates the cached values so they recompute from the mutated `last_changed`/`last_updated` attrs. Must also clear serialisation caches (`as_compressed_state`, `as_compressed_state_json`, `json_fragment`, `as_dict_json`) or the UI crashes with `AttributeError: 'State' object has no attribute 'last_updated_timestamp'`.

### 2. `database without the database executor` warning

**File:** `__init__.py` `_fetch_last_recorded_state()` (~line 304)

The code used `hass.async_add_executor_job()` for the recorder query. HA wants `recorder.get_instance(hass).async_add_executor_job()` for database operations.

**Fix:** Switched to `recorder.get_instance(hass).async_add_executor_job()`.

## Key discovery: HA State object internals (stable branch, 2026-04)

- `State` uses `__slots__` (no `__dict__`)
- Cached properties use `propcache` C extension, stored in `state_obj._cache` (a regular dict)
- `_cache` keys include: `last_changed_timestamp`, `last_updated_timestamp`, `last_reported_timestamp`, `as_compressed_state`, `as_compressed_state_json`, `json_fragment`, `as_dict_json`
- `setattr`/`object.__setattr__` works for slot attributes (`last_changed`, `last_updated`, `last_reported`) but NOT for `propcache` properties
- `_cache.pop(key, None)` is the safe way to invalidate cached properties

## What still needs testing

- The `before` and `target` timestamps in the logs are currently identical (because the test entities have no history from before the restart — they were created fresh). To properly verify the restore works:
  1. Toggle test entities in the UI, wait for recorder to flush
  2. Restart the container
  3. Check logs — `before` (post-restart) should differ from `target` (pre-restart recorded value)
  4. After restore, `after` should match `target`
- The `dump` and `restore` services haven't been tested via Developer Tools → Services yet
- Other strategies (`async_set`, `async_set_force`, `replace`) haven't been tested against this HA version

## Dev environment

- Docker compose in `dev/`
- Config in `dev/config/configuration.yaml` — has logger at debug for the integration, plus `input_boolean.test_one`, `input_boolean.test_two`, `input_number.test_number`
- Integration source is bind-mounted, so edits on host are picked up on `docker compose restart homeassistant`
