# Local HA dev instance

Runs Home Assistant in Docker with this integration bind-mounted, so you can
exercise `restore_last_changed.dump` and `restore_last_changed.restore` against
live entities without touching your real HA.

## One-time setup

```bash
cd dev
docker compose up -d
# Watch startup (first boot pulls the image + runs onboarding).
docker compose logs -f homeassistant
```

Open http://localhost:8123 and complete onboarding (create a user; accept
defaults for location/units). You only do this once — the state lives in
`dev/config/` which is gitignored.

## Reload loop

The integration folder is bind-mounted into the container. To pick up Python
changes:

```bash
docker compose restart homeassistant
```

To apply changes to `configuration.yaml`, the same restart works, or use
*Developer Tools → YAML → All YAML configuration* inside HA.

## Test recipe

1. In the UI, toggle `input_boolean.test_one` a couple of times, change
   `input_number.test_number`. Wait a few seconds so the recorder flushes.
2. Call `restore_last_changed.dump` on `input_boolean.test_one` via
   *Developer Tools → Services*. Check the HA log: the recorder's
   `last_changed` should match the toggle time you just did.
3. Restart the container: `docker compose restart homeassistant`. After
   startup, `last_changed` for the test entities will equal the restart time.
4. Call `restore_last_changed.restore` with different `strategy` values and
   re-check *Developer Tools → States* each time to see which strategy
   actually moves `last_changed` back to the recorded time.

Strategy legend (see `__init__.py` for detail):

| strategy          | what it does                                         |
| ----------------- | ---------------------------------------------------- |
| `async_set`       | Public API, no force flag. Usually deduped by HA.    |
| `async_set_force` | Public API with `force_update=True`.                 |
| `mutate`          | Writes `last_changed`/`last_updated` in place.       |
| `replace`         | Rebuilds a `State` object and swaps it in.           |

## Teardown

```bash
docker compose down           # stop; keep data
docker compose down -v        # stop; also wipe named volumes (not used here)
rm -rf dev/config/*           # nuclear: wipe the HA config entirely
```

## Gotchas

- The first boot on Apple Silicon can be slow (a minute+); later boots are fast.
- If you change the integration's `manifest.json`, HA must be restarted.
- If you edit files on the host and HA doesn't see them, verify the bind mount
  with `docker compose exec homeassistant ls /config/custom_components/restore_last_changed`.
