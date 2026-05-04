"""Restore accurate last_changed / last_updated timestamps on HA startup.

Configuration example (configuration.yaml):

    restore_last_changed:
      entities:
        - sensor.my_sensor
        - binary_sensor.front_door

Two services are exposed for iterative testing without restarting HA:

    service: restore_last_changed.dump
    data:
      entity_id: sensor.my_sensor

        Logs, side by side, what the state machine currently holds and what the
        recorder believes was the last state before the most recent restart.

    service: restore_last_changed.restore
    data:
      entity_id: sensor.my_sensor
      strategy: mutate      # optional; see STRATEGIES below

        Attempts to restore last_changed/last_updated for a single entity using
        the selected strategy, with verbose logging so the outcome can be
        verified in Developer Tools -> States.
"""

import asyncio
import inspect
import logging
from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.components import recorder
from homeassistant.components.recorder import history
from homeassistant.core import Event, HomeAssistant, ServiceCall, State
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CONF_ENTITIES, CONF_GROUPS, DOMAIN, STARTUP_DELAY
from .schema import CONFIG_SCHEMA  # noqa: F401 – HA reads this from the module

_LOGGER = logging.getLogger(__name__)

__all__ = ["CONFIG_SCHEMA", "async_setup"]

STRATEGY_ASYNC_SET = "async_set"
STRATEGY_ASYNC_SET_FORCE = "async_set_force"
STRATEGY_MUTATE = "mutate"
STRATEGY_REPLACE = "replace"

STRATEGIES = (
    STRATEGY_ASYNC_SET,
    STRATEGY_ASYNC_SET_FORCE,
    STRATEGY_MUTATE,
    STRATEGY_REPLACE,
)

DEFAULT_STRATEGY = STRATEGY_MUTATE

SERVICE_DUMP = "dump"
SERVICE_RESTORE = "restore"

SERVICE_DUMP_SCHEMA = vol.Schema({vol.Required("entity_id"): cv.entity_id})

SERVICE_RESTORE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("strategy", default=DEFAULT_STRATEGY): vol.In(STRATEGIES),
    }
)

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the restore_last_changed integration."""

    domain_config = config.get(DOMAIN) or {}
    explicit_entities: list[str] = list(domain_config.get(CONF_ENTITIES, []))
    group_ids: list[str] = list(domain_config.get(CONF_GROUPS, []))

    _register_services(hass)

    if not explicit_entities and not group_ids:
        _LOGGER.debug(
            "No entities or groups configured; services are still available."
        )
        return True

    async def _restore_on_start(_event: Event) -> None:
        """Run after HA is fully started."""
        await asyncio.sleep(STARTUP_DELAY)
        entity_ids = _resolve_entity_ids(hass, explicit_entities, group_ids)
        if not entity_ids:
            _LOGGER.warning(
                "restore_last_changed: no entities resolved from config "
                "(entities=%s groups=%s)",
                explicit_entities,
                group_ids,
            )
            return
        for entity_id in entity_ids:
            try:
                await _restore_entity(hass, entity_id, DEFAULT_STRATEGY)
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception(
                    "Unexpected error restoring timestamps for %s", entity_id
                )

    hass.bus.async_listen_once("homeassistant_started", _restore_on_start)

    return True


def _resolve_entity_ids(
    hass: HomeAssistant,
    explicit_entities: list[str],
    group_ids: list[str],
) -> list[str]:
    """Flatten group members into a deduplicated entity list.

    Groups are resolved by reading the ``entity_id`` attribute on the group's
    state. Nested groups are expanded recursively; cycles are guarded by a
    visited set. Group entities themselves are dropped from the final list —
    only their leaf members get their timestamps restored.
    """
    resolved: list[str] = []
    seen: set[str] = set()

    def _add(entity_id: str) -> None:
        if entity_id in seen:
            return
        seen.add(entity_id)
        resolved.append(entity_id)

    for entity_id in explicit_entities:
        _add(entity_id)

    visited_groups: set[str] = set()

    def _expand_group(group_id: str) -> None:
        if group_id in visited_groups:
            return
        visited_groups.add(group_id)
        state = hass.states.get(group_id)
        if state is None:
            _LOGGER.warning(
                "Configured group %s not found in state machine; skipping",
                group_id,
            )
            return
        members = state.attributes.get("entity_id") or ()
        for member in members:
            if member.startswith("group."):
                _expand_group(member)
            else:
                _add(member)

    for group_id in group_ids:
        _expand_group(group_id)

    return resolved


def _register_services(hass: HomeAssistant) -> None:
    """Register dump / restore services. Idempotent across reloads."""

    async def handle_dump(call: ServiceCall) -> None:
        await _dump_entity(hass, call.data["entity_id"])

    async def handle_restore(call: ServiceCall) -> None:
        await _restore_entity(
            hass, call.data["entity_id"], call.data["strategy"]
        )

    hass.services.async_register(
        DOMAIN, SERVICE_DUMP, handle_dump, schema=SERVICE_DUMP_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RESTORE, handle_restore, schema=SERVICE_RESTORE_SCHEMA
    )


async def _dump_entity(hass: HomeAssistant, entity_id: str) -> None:
    """Log what the state machine has vs. what the recorder has."""

    current = hass.states.get(entity_id)
    if current is None:
        _LOGGER.warning("[dump] %s: not present in state machine", entity_id)
    else:
        _LOGGER.warning(
            "[dump] %s state-machine: state=%r last_changed=%s last_updated=%s",
            entity_id,
            current.state,
            current.last_changed.isoformat(),
            current.last_updated.isoformat(),
        )

    recorded = await _fetch_last_recorded_state(hass, entity_id)
    if recorded is None:
        _LOGGER.warning("[dump] %s recorder: no history found", entity_id)
        return

    _LOGGER.warning(
        "[dump] %s recorder: state=%r last_changed=%s last_updated=%s",
        entity_id,
        recorded.state,
        recorded.last_changed.isoformat(),
        recorded.last_updated.isoformat(),
    )


async def _restore_entity(
    hass: HomeAssistant, entity_id: str, strategy: str
) -> None:
    """Restore last_changed / last_updated for a single entity."""

    current = hass.states.get(entity_id)
    if current is None:
        _LOGGER.warning(
            "[restore] %s: not in state machine, skipping", entity_id
        )
        return

    recorded = await _fetch_last_recorded_state(hass, entity_id)
    if recorded is None:
        _LOGGER.warning("[restore] %s: no recorder history, skipping", entity_id)
        return

    _LOGGER.info(
        "[restore:%s] %s before: last_changed=%s -> target=%s",
        strategy,
        entity_id,
        current.last_changed.isoformat(),
        recorded.last_changed.isoformat(),
    )

    if strategy == STRATEGY_ASYNC_SET:
        _apply_async_set(hass, entity_id, recorded, force=False)
    elif strategy == STRATEGY_ASYNC_SET_FORCE:
        _apply_async_set(hass, entity_id, recorded, force=True)
    elif strategy == STRATEGY_MUTATE:
        _apply_mutate(hass, entity_id, recorded)
    elif strategy == STRATEGY_REPLACE:
        _apply_replace(hass, entity_id, recorded)
    else:
        _LOGGER.error("[restore] unknown strategy %r", strategy)
        return

    after = hass.states.get(entity_id)
    if after is None:
        _LOGGER.warning("[restore:%s] %s: vanished after set", strategy, entity_id)
        return

    match = after.last_changed == recorded.last_changed
    _LOGGER.warning(
        "[restore:%s] %s after: last_changed=%s last_updated=%s match=%s",
        strategy,
        entity_id,
        after.last_changed.isoformat(),
        after.last_updated.isoformat(),
        match,
    )


def _apply_async_set(
    hass: HomeAssistant, entity_id: str, recorded: State, force: bool
) -> None:
    """Strategy: use the public StateMachine.async_set() API."""
    params = inspect.signature(hass.states.async_set).parameters
    kwargs: dict[str, Any] = {}
    if force and "force_update" in params:
        kwargs["force_update"] = True

    if "last_changed" in params:
        kwargs["last_changed"] = recorded.last_changed
        if "last_updated" in params:
            kwargs["last_updated"] = recorded.last_updated
    elif "timestamp" in params:
        kwargs["timestamp"] = recorded.last_changed.timestamp()

    hass.states.async_set(
        entity_id, recorded.state, recorded.attributes, **kwargs
    )


def _apply_mutate(
    hass: HomeAssistant, entity_id: str, recorded: State
) -> None:
    """Strategy: mutate timestamps on the live State object in place.

    Bypasses the state machine's dedup path entirely. Slightly invasive but
    usually safe: State objects use __slots__ for these fields and HA's
    change-tracking only fires when async_set() is called.
    """
    state_obj = hass.states.get(entity_id)
    if state_obj is None:
        return

    _set_state_attr(state_obj, "last_changed", recorded.last_changed)
    _set_state_attr(state_obj, "last_updated", recorded.last_updated)
    # last_reported was added in recent HA; only set if present.
    if hasattr(state_obj, "last_reported"):
        _set_state_attr(state_obj, "last_reported", recorded.last_updated)
    # last_updated_timestamp is a __slots__ attribute on modern HA (used
    # directly by the REST API / websocket serialisers). Update it too or
    # callers will see the pre-mutation timestamp.
    if hasattr(state_obj, "last_updated_timestamp"):
        _set_state_attr(
            state_obj,
            "last_updated_timestamp",
            recorded.last_updated.timestamp(),
        )

    # Invalidate cached values derived from the timestamps. These are stored
    # on state_obj._cache by propcache and must be popped so they recompute
    # from the mutated slot values above.
    cache = getattr(state_obj, "_cache", None)
    if cache is not None:
        for key in (
            "last_changed_timestamp",
            "last_reported_timestamp",
            "as_compressed_state",
            "as_compressed_state_json",
            "json_fragment",
            "as_dict_json",
            "_as_dict",
            "_as_read_only_dict",
        ):
            cache.pop(key, None)


def _apply_replace(
    hass: HomeAssistant, entity_id: str, recorded: State
) -> None:
    """Strategy: drop a freshly built State into the private _states dict."""
    try:
        new_state = State(
            entity_id,
            recorded.state,
            dict(recorded.attributes),
            last_changed=recorded.last_changed,
            last_updated=recorded.last_updated,
            context=hass.states.get(entity_id).context if hass.states.get(entity_id) else None,
        )
    except TypeError:
        # Some HA versions only accept some of these kwargs; fall back.
        new_state = State(entity_id, recorded.state, dict(recorded.attributes))
        _set_state_attr(new_state, "last_changed", recorded.last_changed)
        _set_state_attr(new_state, "last_updated", recorded.last_updated)

    # hass.states._states is a dict-like internal store across HA versions.
    store = getattr(hass.states, "_states", None)
    if store is None:
        _LOGGER.error("[restore:replace] hass.states._states not accessible")
        return
    store[entity_id] = new_state


def _set_state_attr(state_obj: State, attr: str, value: Any) -> None:
    """Set an attribute on a State object, tolerating __slots__ / frozen fields."""
    try:
        setattr(state_obj, attr, value)
    except (AttributeError, TypeError) as err:
        _LOGGER.debug(
            "Cannot set %s.%s directly (%s); trying object.__setattr__",
            type(state_obj).__name__, attr, err,
        )
        try:
            object.__setattr__(state_obj, attr, value)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception(
                "Failed to set %s on State for diagnostic", attr
            )


async def _fetch_last_recorded_state(
    hass: HomeAssistant, entity_id: str
) -> State | None:
    """Query recorder for the last state change before this HA session started.

    Using the recorder's "latest state" directly is wrong: once HA starts,
    entities immediately write new rows (default values, restored values, etc.)
    and those become the "latest" state — which is the post-restart value, not
    the pre-restart one we want to restore.

    The recorder's ``recording_start`` is captured when the recorder itself
    initialises, which is earlier than any entity platform setup, so rows
    written at/after that time belong to the current session and must be
    skipped.
    """
    instance = recorder.get_instance(hass)
    session_start: datetime = instance.recorder_runs_manager.recording_start

    states = await instance.async_add_executor_job(
        _fetch_blocking, hass, entity_id
    )
    if not states:
        return None
    # ``states`` is ordered oldest-first. Walk from newest backwards to find
    # the first state recorded strictly before this session started.
    for state in reversed(states):
        if state.last_updated < session_start:
            return state
    return None


# Number of most-recent state rows to inspect when looking for the last
# pre-startup state. HA can record a burst of rows during startup (entity
# registration, restore_state rehydrate, etc.), so this must be comfortably
# larger than that burst — 50 is plenty in practice.
FETCH_BATCH_SIZE = 50


def _fetch_blocking(hass: HomeAssistant, entity_id: str) -> list[State]:
    """Blocking recorder query — must be called via async_add_executor_job."""
    try:
        result = history.get_last_state_changes(
            hass, FETCH_BATCH_SIZE, entity_id
        )
        states = result.get(entity_id) or []
        _LOGGER.debug(
            "get_last_state_changes(%s, %d) -> %d rows, newest=%s",
            entity_id,
            FETCH_BATCH_SIZE,
            len(states),
            states[-1].last_updated.isoformat() if states else None,
        )
        return states
    except Exception:  # pylint: disable=broad-except
        _LOGGER.warning(
            "Failed to query recorder history for %s.", entity_id, exc_info=True
        )
        return []
