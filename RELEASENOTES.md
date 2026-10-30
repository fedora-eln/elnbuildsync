# Release Notes

## 2.2.0

This release adds support for Bodhi-less finalisation, Koji instance
filtering, improved Bodhi timeout handling with email alerts, and
stale Koji task cancellation on daemon restart.

### Highlights

- **Bodhi-less finalization (`bodhi: false`).** When the static config
  sets `bodhi` to the literal value `false`, `RebuildBatch.run()` now
  calls a new `finalize_via_tagging()` path instead of going through
  Bodhi. This promotes draft builds, tags the NVRs directly into the
  stable tag, and waits for each NVR to appear using the configured
  `koji.side_tag_timeout`. Tagging failures are logged at ERROR level
  and trigger a failure email if the emailer is configured.

- **Koji instance differentiation.** Multiple Koji instances may share
  the same Fedora Messaging routing keys (e.g. the primary Koji and a
  secondary new-architecture instance). The daemon now checks
  `body.instance` in incoming messages and filters for the configured
  instance, preventing cross-instance interference.
  Fixes [#81](https://github.com/fedora-eln/elnbuildsync/issues/81).

- **Bodhi timeout monitoring and email alerts.** The daemon now sends a
  warning email when a Bodhi update is taking longer than expected and
  a failure email if the update tag wait times out entirely.
  Fixes [#80](https://github.com/fedora-eln/elnbuildsync/issues/80).

- **Cancel stale Koji tasks on restart.** On startup, the daemon now
  cancels any Koji tasks left running from a previous process, with a
  `--skip-stale-task-cancel` CLI flag for local/personal Koji
  credentials and automatic skipping in dry-run mode.

### Features

- Added `koji.side_tag_timeout` config option, separating the initial
  side-tag creation wait from the Bodhi update tagging wait.
- Made the Bodhi stable timeout configurable (default: 24 hours).
- Added validation checks for unlikely static config values.
- Added release and deployment documentation.

### Build / packaging

- Reordered Dockerfile `COPY` layer after package installs to improve
  layer caching and speed up build times.
- Bumped version to 2.2.0; added James Troy as a project author.

## 2.1.0

This release represents a large body of work built on top of the `2.0.2`
release, headlined by a full migration off Twisted onto native `asyncio`
and `FastAPI`, plus a hardened, fully-enforced build-pause feature and a
substantial new integration test suite.

### Highlights

- **Twisted → asyncio/FastAPI migration.** `web.py` was rewritten from
  `twisted.web` to `FastAPI`/`Starlette` (with `uvicorn`), `auth.py` was
  adapted to Starlette's `Request`/`Response`, and the daemon now runs the
  web app on the same shared `asyncio` event loop as the rest of the
  service. `listener.py`, `db_models`/`buildtrigger`, and the periodic
  config-update loop were all converted to be async-native:
  `Deferred`/`@as_deferred` bridging, `twisted.internet.task.LoopingCall`,
  and `deferToThread` were replaced with `asyncio.Future`, a pure-asyncio
  `PeriodicTask`, and `asyncio.to_thread`, respectively. `txrequests` and
  `getProcessOutput` were replaced with `httpx`/`asyncio` subprocess calls,
  and `python-httpx` was subsequently swapped for `python-httpx2`.
  Minimum supported Python is now 3.12.
- **Pause enforcement is now centralized and reliable.** Build-pause
  checking moved out of the message listener and web trigger endpoint and
  into `batching.process_message_batch()`, so no new Koji build can ever be
  started while paused. Incoming trigger messages, `/trigger` POSTs, and
  `periodic_cleanup()` all keep queuing/persisting work as normal while
  paused instead of being dropped or rejected — nothing is lost, and the
  message queue no longer backs up during a pause. A batch that is already
  running when a pause is requested is allowed to finish normally; only the
  *next* batch is held back until unpause. Extensive new integration
  coverage (`tests/integration/test_pause_behavior.py`) exercises pause
  toggled both via the static/dynamic config and via the runtime
  `/control/pause` and `/control/unpause` endpoints, including mid-batch
  pause timing.
- **New `bodhi.max_single_batch_size` config option.** Lets Bodhi update
  splitting (`bodhi.batch_size`) kick in only once a rebuild's total build
  count exceeds a separate threshold, so a mass-rebuild of many packages
  can be routed into smaller multi-build updates without also forcing
  small day-to-day rebuilds to be split. Defaults to `batch_size` to
  preserve prior behavior when unset.

### Features

- Added a `/trigger` request body JSON-schema validation step.
- `/trigger`'s content-type parsing now correctly handles a `Content-Type`
  header that includes parameters (e.g. `application/json; charset=utf-8`).
- Wired up periodic web session cleanup, which previously existed as
  unused code and let the active-session table grow without bound.
- Old/incomplete OIDC login states are now expired periodically to avoid
  unbounded memory growth and stale valid logins hanging around.
- Dropped support for untagging old package versions.
- Dropped the unused `kojihelpers.builds.wait_for_task()` helper.

### Reliability / bug fixes

- Task registration now always happens before a wait begins, closing a
  race where a very fast Koji response could be delivered and ignored
  before the waiter was registered.
- Unknown/unexpected task errors are now handled as failures (populated
  with synthetic timeout-like data) instead of raising `CancelledError`,
  so a single bad task no longer aborts the whole batch.
- Task cancellation now reliably surfaces as a task **failure** rather
  than as a cancellation.
- `pending_nvr_tags` entries are now cleaned up on timeout instead of
  leaking.
- Fixed `Scheduling.reset()`.
- Ensured triggered builds finish submission before being considered
  handled.
- `git-ls` errors are now handled instead of propagating unhandled.
- The daemon now handles pod shutdown safely, and its shutdown
  "terminator" was converted to an `asyncio.Future`.
- Fixed a "no running event loop" crash on daemon startup.
- Fixed a periodic config-update crash under the Twisted/asyncio reactor,
  and `update_config()` now catches unexpected errors so the periodic
  update loop keeps running afterward.
- `ruff`-driven cleanup of `datetime.UTC` usage.

### Testing / CI

- Added a large new end-to-end integration test suite
  (`tests/integration/`) with fake Koji, Bodhi, and Fedora Messaging bus
  implementations and a reusable test harness, covering full rebuild
  flows, task timeouts, `check_tasks()` polling, transient Koji submission
  errors, and pause behavior.
- Added `tests/test_daemon.py`, `tests/test_listener.py`,
  `tests/test_scheduling.py`, `tests/test_state.py`, and greatly expanded
  `tests/test_web.py` and `tests/test_parse_config.py`.
- Added `tox.ini` for easy local test runs and simplified
  `local_test_daemon` configuration.
- CI now runs pytest unit tests and dedicated integration-test jobs
  (using a virtualenv), with a corrected messaging configuration passed to
  the test daemon.
- Removed obsolete Twisted-era test scaffolding (`tests/deferredlist.py`,
  Twisted-specific mocks) in favor of mocking `create_subprocess_exec`
  and HTTP calls directly.

### Dependencies

- Added `fastapi` and `uvicorn`.
- Replaced `httpx` with `httpx2`.
- Removed `txrequests`.
- Raised minimum Python version to 3.12.
