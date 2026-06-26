# ELNBuildSync

ELNBuildSync (EBS) automatically rebuilds Fedora Rawhide packages for
[Fedora ELN](https://docs.fedoraproject.org/en-US/eln/) (Enterprise Linux
Next). It listens for Koji tagging events on the Fedora Messaging Bus,
batches work to account for side-tag merges, rebuilds packages in isolated
Koji side-tags, and publishes consolidated Bodhi updates for testing.

## Overview

When a package is tagged into Rawhide (after passing Fedora QA gating), EBS
checks whether it is targeted for ELN or ELN Extras. Eligible packages are
enqueued for the next rebuild batch. In order to ensure that all packages
that need to be are processed as part of the same batch, batches are
designed not to begin until a minimal lull timeout has passed.

While a batch is running, EBS stops accepting new Fedora Messages,
re-queueing them for later processing. When the batch finishes, message
processing resumes and a new batch can start. **Batches never run in
parallel**: the next batch may depend on buildroots produced by the
previous one.

## Rebuild algorithm

The following describes what EBS does for each batch. The implementation
lives primarily in `elnbuildsync/batching.py`,
`elnbuildsync/rebuildbatch.py`, `elnbuildsync/rebuildbatchslice.py`, and
`elnbuildsync/rebuildattempt.py`.

### 1. Batch formation

| Step | What happens |
|------|--------------|
| Listen | `listener.message_handler` receives `buildsys.tag` messages. |
| Filter | `config.is_eligible()` applies include/exclude and mappings. |
| Queue | Eligible messages go into an in-memory `message_queue`. |
| Lull | `batching.process_message_batch` drains the queue after lull. |
| Serialize | If `batching.running`, new triggers are `Nack`'d until done. |

### 2. Build side-tag preparation

`RebuildBatch` creates a **build side-tag** derived from the ELN buildroot:

1. Resolve the ELN target's parent and destination tags via Koji
   (`kojihelpers.tags`).
2. Create a new side-tag and tag most Rawhide builds into it (subject to
   `control.skip_tag`—packages whose Rawhide builds must not enter the ELN
   buildroot, e.g. LLVM, OCAML, `fedora-release`).
3. Wait for Koji to regenerate the buildroot (`buildsys.repo.init` /
   `buildsys.repo.done` messages, with periodic tag polling as a fallback).

This approach reuses successful Rawhide builds in the buildroot so ELN
rebuilds avoid most bootstrap and ordering problems.

### 3. Ordered batch slices and rebuild attempts

Packages in the batch are grouped into **batch slices** by
`control.ordering` in the configuration (default order `1000`; lower
numbers build first, e.g. `llvm` at `0`).

For each slice, `RebuildBatchSlice`:

1. Starts a **rebuild attempt**: all packages in the slice are submitted to
   Koji concurrently, using the SCM URL from the Rawhide build that
   triggered the tag (so dist-git drift after the Rawhide build does not
   change what gets built).
2. Waits for tasks to complete via `buildsys.task.state.change` messages,
   with a periodic `listener.check_tasks()` poll because Koji does not
   always emit AMQP events reliably.
3. **Retries failures** in new rebuild attempts until the failure count
   stops decreasing (the same set of failures twice in a row is treated as
   legitimate build breakage).

### 4. Errata creation (Bodhi)

After all slices succeed or exhaust retries:

1. Successful build NVRs are collected from task completion messages.
2. For non-scratch builds, EBS creates a separate **errata side-tag** and
   tags only the new ELN builds into it (not the Rawhide builds used to
   seed the build side-tag).
3. `BodhiClient` submits one Bodhi update per errata tag (large batches may
   be split using `bodhi.batch_size` in configuration).
4. EBS waits for builds to appear in the configured `stable_tag`, then
   removes the build side-tag.

Scratch builds (used in local testing) skip Bodhi submission and tagging.

### 5. Completion

The batch is marked complete in PostgreSQL, `batching.running` is cleared,
and queued Fedora Messages can be processed again.

## Software architecture

EBS is a long-running **Twisted** application using the **asyncio
reactor** (`elnbuildsync/daemon.py`). Blocking or threaded work (Koji
calls, Bodhi submission, Git operations) is delegated via `deferToThread`
and related helpers; Fedora Messaging integrates through
`fedora_messaging.api.twisted_consume`.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         elnbuildsync (daemon)                           │
├─────────────────────────────────────────────────────────────────────────┤
│  Fedora Messaging ──► listener.py ──► message_queue ──► batching.py     │
│       ▲                      │                               │          │
│       │                      ├── repo init/done              ▼          │
│       │                      ├── task state change    RebuildBatch      │
│       │                      └── tag (trigger/await)         │          │
│       │                                                      ▼          │
│  HTTP :8080 ◄── web.py (status, trigger, OIDC)    RebuildBatchSlice     │
│                                                      RebuildAttempt     │
│  PostgreSQL ◄── db_models.py (batches, slices, tasks, sessions)         │
│  Koji / Bodhi ◄── kojihelpers/ + bodhi-client                           │
│  Config YAML ◄── config.py (file, URL, or git checkout)                 │
└─────────────────────────────────────────────────────────────────────────┘
```

### Module reference

| Module | Responsibility |
|--------|----------------|
| `daemon.py` | CLI entry; DB, schedulers, messaging, HTTP. |
| `listener.py` | Fedora Messages; task/tag waiters; polling. |
| `batching.py` | Message queue, lull timer, manual rebuild helper. |
| `rebuildbatch.py` | Side-tag lifecycle, slices, Bodhi updates. |
| `rebuildbatchslice.py` | Per-ordering slice execution and retries. |
| `rebuildattempt.py` | Koji build submission and per-task tracking. |
| `rebuildtask.py` | Individual rebuild task state. |
| `tagmessage.py` | Tag messages; SCM URLs and DB records. |
| `kojihelpers/` | Koji connection pooling, tags, builds, errors. |
| `config.py` | YAML load/refresh; eligibility and ordering. |
| `db_models.py` | SQLAlchemy models for batches and sessions. |
| `web.py` | Health, status, `/trigger`, OIDC login/logout. |
| `status.py` | Periodic status page generation. |
| `cleanup.py` | Periodic cleanup of stale state. |
| `email.py` | SMTP notifications (e.g. build failures). |
| `auth.py` | OpenID Connect sessions for admin endpoints. |

### HTTP endpoints

When the daemon is running (including under `tests/local_test_daemon.sh`),
port **8080** exposes:

| Path | Purpose |
|------|---------|
| `/alive`, `/startup` | Liveness / startup probes |
| `/status.html`, `/status.json` | Operational status |
| `/trigger` | Manually queue rebuilds (OIDC when configured) |
| `/login`, `/logout`, `/oidc/*` | OpenID Connect authentication flow |

### Configuration

Runtime behavior is driven by static and dynamic YAML configuration.
Production static settings live in `/etc/elnbuildsync/elnbuildsync.yaml`;
dynamic settings come from the `distrobuildsync-config` git repository or
`/etc/elnbuildsync/elnbuildsync_dynamic.yaml`. Local testing uses
`tests/etc/local/elnbuildsync.yaml` and `tests/etc/local/elnbuildsync_dynamic.yaml`.

Important sections:

- **`configuration.koji`**: Koji profile, build target, stable tag,
  scratch/fail-fast flags (static).
- **`configuration.control`**: `trigger_tag`, `skip_tag`, `exclude`, `ordering`,
  pause flag, status interval (dynamic).
- **`configuration.bodhi`**: Maximum builds per Bodhi update (`batch_size`;
  `0` means no splitting).
- **`configuration.db`**: PostgreSQL connection settings.
- **`configuration.open_id_connect`**: OIDC settings for `/trigger`
  (client secret via `--openid-client-secret-file`, not in YAML)
- **`components`**: Autopackagelist resolver and per-package overrides.

### Deployment artifacts

| Path | Purpose |
|------|---------|
| `Dockerfile` / `run.sh` | Container image and entrypoint. |
| `helm_charts/` | Kubernetes/OpenShift deployment. |
| `requirements.txt` | Python dependencies |

## Getting started (development on Fedora)

Local development runs EBS in a Podman container against **Fedora
Messaging**, a **PostgreSQL** test database, and **Koji** (via Kerberos
credentials from your workstation). The primary workflow is
`tests/local_test_daemon.sh`.

### Prerequisites

Install development packages on a Fedora workstation:

```bash
sudo dnf install \
  podman \
  postgresql \
  python3 \
  python3-pip \
  python3-devel \
  koji \
  krb5-workstation \
  git \
  rpm-devel
```

You also need:

- A **Fedora account** with permission to run builds against the Koji
  targets referenced in your test configuration (the sample config uses
  staging-oriented settings and `scratch_build: true`).
- **Kerberos credentials** for Koji. EBS inside the container uses the
  host's KCM socket:

  ```bash
  kinit your_fedora_username@FEDORAPROJECT.ORG
  koji hello
  ```

- **Test secrets** under `tests/etc/local/` (and `tests/etc/crc/` for
  OpenShift testing). At minimum:

  | File | Purpose |
  |------|---------|
  | `tests/etc/local/ebs_db_pw` | PostgreSQL password (one line) |
  | `tests/etc/local/ebs_smtp_pw` | SMTP password (one line) |
  | `tests/etc/local/ebs_oidc_client_secret` | OIDC client secret (one line) |

  These may be overridden with `--db-pw-file`, `--smtp-pw-file`, and
  `--openid-client-secret-file` when calling `tests/local_test_daemon.sh`.

- **Fedora Messaging certificates** are vendored under
  `tests/fedora-messaging/` (see `tests/fedora-messaging/README.md`).

Optionally, edit `tests/etc/local/elnbuildsync.yaml` and
`tests/etc/local/elnbuildsync_dynamic.yaml` for your environment (Koji
tags, OIDC client ID for `/trigger`, package lists). The OIDC client
secret belongs in `tests/etc/local/ebs_oidc_client_secret`, not in the
YAML file. The default config points at tinystage for OIDC; register a
client at [tiny-stage](https://github.com/fedora-infra/tiny-stage) if you
need authenticated triggering. OIDC can be disabled by setting
`open_id_connect: false`.

### First run: build the container image

From the repository root:

```bash
./tests/local_test_daemon.sh --build-container
```

This builds `localhost/elnbuildsync:local_test_daemon` from the project
`Dockerfile`. You only need to repeat this after dependency or image
changes (or pass `--build-container` again).

### Run the test daemon

```bash
./tests/local_test_daemon.sh
```

The script:

1. Ensures Python dependencies are installed (`pip install -r
   requirements.txt` and editable install of this tree).
2. Creates a Podman network `ebs_local_test`.
3. Starts a temporary PostgreSQL 18 container (`temp_postgres`) unless a
   persistent one is already reachable on port 5432.
4. Runs the EBS container with:
   - `tests/etc/local/` mounted at `/etc/elnbuildsync/` (static and dynamic
     config plus secrets)
   - `--static-config-file` and `--dynamic-config-file` passed to the daemon
   - Fedora Messaging staging config (`--environment stg`, the default)
   - Port **8080** published for the web UI and APIs
   - A short **lull time** of 5 seconds (production uses 60)

Logs are written to `/tmp/elnbuildsync.log` and echoed to the terminal.

Useful options:

```bash
./tests/local_test_daemon.sh --help

# More verbose logging
./tests/local_test_daemon.sh --log-level DEBUG

# Shorter or longer batch coalescing window
./tests/local_test_daemon.sh --lull-time 10

# Keep database data between runs
./tests/local_test_daemon.sh --persistent-db \
  --persistent-db-path tests/persistent_db

# Use production Fedora Messaging broker config (instead of staging)
./tests/local_test_daemon.sh --environment prod
```

When you stop the script (Ctrl+C), the ephemeral PostgreSQL container is
removed unless you used `--persistent-db`.

### Verify it is working

1. Confirm `koji hello` works on the host before starting the daemon.
2. After startup, open
   [http://localhost:8080/status.html](http://localhost:8080/status.html).
3. Watch `/tmp/elnbuildsync.log` for batch activity when Rawhide tag
   messages arrive on the staging broker, or use `/trigger` (with OIDC if
   configured) to queue specific components.

### Unit tests

Install test dependencies and run pytest from the repository root:

```bash
pip install -e '.[test]'
pytest
```

Configuration parsing tests live in `tests/test_parse_config.py`; other
modules have targeted tests under `tests/`.

### OpenShift / CRC testing

For deployment-style testing on OpenShift Local, use
`tests/openshift_test_daemon.sh`, which builds the image, pushes to a
cluster registry, and installs using the Helm chart. That path requires a
service keytab and is intended for integration testing rather than
day-to-day code changes.

## Production configuration

Production deployments load static configuration from
`/etc/elnbuildsync/elnbuildsync.yaml` and dynamic configuration from the
[elnbuildsync-config](https://github.com/fedora-eln/elnbuildsync-config)
git repository (see `run.sh --dynamic-config-url`) or
`/etc/elnbuildsync/elnbuildsync_dynamic.yaml`. Database, SMTP, and OIDC
client secrets mount at `/etc/elnbuildsync/` (or use daemon defaults:
`/etc/ebs_db_pw`, `/etc/ebs_oidc_client_secret`). Pass
`--openid-client-secret-file` when the secret is mounted elsewhere. A
service keytab is used for `eln-buildsync@FEDORAPROJECT.ORG`. See
`helm_charts/` for Kubernetes resources.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
