# TinyFish Search Agent

Use this page when you need to configure, operate, or debug Clawcross's TinyFish internet search agent.

## What It Does

The TinyFish search agent adds web crawling and data extraction capabilities to Clawcross:

- submit site-crawl jobs to the TinyFish Web Agent API
- persist each run and extracted data snapshots into SQLite
- detect `NEW`, `UPDATED`, and `REMOVED` items between runs
- expose REST endpoints for the settings UI
- support a live SSE crawl view that also persists the final result

This feature is shared by the frontend, the scheduler, and a standalone CLI wrapper.

## Main Files

| Path | Role |
|---|---|
| `src/services/tinyfish_monitor_service.py` | shared TinyFish client, SQLite persistence, change detection, live SSE stream handling |
| `scripts/tinyfish_competitor_monitor.py` | thin CLI wrapper around the shared service |
| `src/front.py` | `/api/tinyfish/*` endpoints for status, run, live crawl, and site snapshots |
| `src/utils/scheduler_service.py` | restores the built-in TinyFish cron job from `config/.env` |
| `config/tinyfish_targets.example.json` | example target file schema |
| `config/tinyfish_targets.json` | local target list used by the runtime |
| `test/test_tinyfish_monitor.py` | unit tests for target loading, persistence, and polling |
| `test/tinyfish_live_smoke.py` | opt-in real API smoke test |

## Configuration

Clawcross reads these keys from `config/.env`:

| Key | Purpose | Default |
|---|---|---|
| `TINYFISH_API_KEY` | TinyFish Web Agent API key sent as `X-API-Key` | required |
| `TINYFISH_BASE_URL` | TinyFish API base URL | `https://agent.tinyfish.ai` |
| `TINYFISH_MONITOR_DB_PATH` | SQLite file for runs, snapshots, and changes | `data/tinyfish_monitor.db` |
| `TINYFISH_MONITOR_TARGETS_PATH` | search target JSON path | `config/tinyfish_targets.json` |
| `TINYFISH_MONITOR_ENABLED` | enable the built-in scheduled monitor | `false` |
| `TINYFISH_MONITOR_CRON` | five-field cron expression for the scheduled run | unset |

Recommended operator flow:

1. Configure the keys from the settings UI's `TinyFish Monitor` group, or edit `config/.env` directly.
2. Copy `config/tinyfish_targets.example.json` to `config/tinyfish_targets.json`.
3. Save and restart Clawcross if you changed the cron or target path so `src/utils/scheduler_service.py` can restore the scheduler job with the new values.

## Target File Format

The target file is a JSON document with optional shared defaults plus a `targets` array:

```json
{
  "defaults": {
    "browser_profile": "lite",
    "extra_payload": {}
  },
  "targets": [
    {
      "site_key": "competitor-a",
      "name": "Competitor A",
      "url": "https://example.com/pricing",
      "goal": "Return strict JSON with plan names, prices, periods, and availability."
    }
  ]
}
```

Important fields:

- `site_key`: stable identifier used by the API, DB, and CLI filters
- `name`: human-readable site name
- `url`: initial page to crawl
- `goal`: extraction instruction passed to TinyFish
- `browser_profile`: optional per-site browser mode such as `lite` or `stealth`
- `extra_payload`: optional raw TinyFish request extensions such as proxy settings

`defaults` are merged into each target before submission. The loader can also filter by `site_key`.

## How To Run It

### From the Settings UI

The TinyFish settings group supports three main operator actions:

- `Run Now`: submit the configured targets immediately
- `Refresh`: reload config, pending runs, recent runs, latest site snapshots, and recent data changes
- `Live Crawl`: open a real-time SSE-backed crawl for one target and persist the final result into the same SQLite database

### From HTTP Endpoints

`src/front.py` exposes these routes:

| Route | Purpose |
|---|---|
| `GET /api/tinyfish/status` | config summary, configured targets, pending runs, recent runs, latest site snapshots, recent changes |
| `POST /api/tinyfish/run` | submit selected targets; supports async submit or wait-for-completion |
| `POST /api/tinyfish/live-run` | proxy TinyFish `/v1/automation/run-sse` and stream events back to the browser |
| `GET /api/tinyfish/sites/<site_key>` | latest stored snapshots for one site |

`GET /api/tinyfish/status?sync=1` also polls already-pending runs before returning the overview.

### From the CLI Wrapper

Use the wrapper when you want a direct operator flow without the Web UI:

```bash
uv run python scripts/tinyfish_competitor_monitor.py run --site competitor-a
uv run python scripts/tinyfish_competitor_monitor.py poll
uv run python scripts/tinyfish_competitor_monitor.py report --limit 20
```

Useful switches:

- `run --no-wait`: submit jobs only and return immediately
- `run --site <site_key>`: repeatable site filter
- `poll --site <site_key>`: only wait on pending runs for selected sites
- `--db`: point to a temporary or alternate SQLite file
- `--targets`: point to a non-default target JSON file

## Scheduler Behavior

When `TINYFISH_MONITOR_ENABLED=true` and `TINYFISH_MONITOR_CRON` is set, `src/utils/scheduler_service.py` restores a built-in APScheduler job on service startup.

That job:

- loads the target file configured in `.env`
- submits the monitor run
- waits for completion
- prints a short summary to the scheduler logs

If the feature is disabled or the cron is blank, the scheduler skips registration.

## Persistence Model

The monitor DB stores three layers of data:

- `tinyfish_runs`: submission metadata, status, timestamps, raw result, and errors
- `tinyfish_price_snapshots`: normalized per-item snapshots for each completed run
- `tinyfish_price_changes`: derived deltas against the previous known snapshot for the same site

Change detection is item-key based:

- `NEW`: item appears for the first time
- `UPDATED`: existing item changes value or related fields
- `REMOVED`: item existed previously but is absent from the latest result

The service keeps both raw text values and parsed numeric amounts when it can extract them.

## Verification

Fast checks:

- `uv run python -m unittest test.test_tinyfish_monitor`
- `uv run python test/tinyfish_live_smoke.py --site <site_key>`

The smoke test is opt-in and hits the real TinyFish API, so it requires a valid `TINYFISH_API_KEY` plus a usable target in `config/tinyfish_targets.json`.

## Related Docs

- [index.md](./index.md)
- [runtime-reference.md](./runtime-reference.md)
- [repo-index.md](./repo-index.md)
- [ports.md](./ports.md)
