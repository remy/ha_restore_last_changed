# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] - 2026-04-18

### Fixed

- Restore now actually sets `last_changed` / `last_updated` back to the
  pre-restart recorder values. The previous release matched the latest
  recorder row, but HA writes a fresh state row at startup (`RestoreEntity`
  rehydrate, `input_number.initial`, etc) — so "the latest row" was the
  post-restart state, not what we wanted. The integration now walks recent
  recorder rows and picks the newest one written before the current recorder
  session started.
- REST API and frontend now reflect the restored timestamps. Previously the
  mutation updated the in-memory `State` object but left
  `State.last_updated_timestamp` (a `__slots__` attribute, not a cached
  property) and the `_as_dict` / `_as_read_only_dict` caches untouched, so
  `/api/states/<entity>` and the UI still showed the pre-mutation value.

## [1.0.0] - 2026-04-16

Initial public release.
