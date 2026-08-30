# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

**Docker (primary method):**
```bash
./commands/build.sh        # Build and start the current checkout
./commands/build.sh v0.5   # Build and start a tagged historical version
./commands/logs.sh         # Follow logs
./commands/down.sh         # Stop
./commands/bash.sh         # Shell into the running container
```

**Direct Python:**
```bash
pip install -r requirements.txt
cd src && python app.py --debug --route_type "0,1,3"
```

The app binds `0.0.0.0:5000`. `MBTA_API_KEY`, `MBTA_LAT`, and `MBTA_LON` are read
from `.env` at startup (see `.env.example`).

`--route_type` accepts comma-separated integers (0=light rail, 1=subway,
2=commuter rail, 3=bus, 4=ferry).

## Versioning

Active code lives in `src/`. Releases are git tags (`v0.1` … `v1.0`).

To cut a new version, tag it — there is no folder copying:

```bash
git tag -a v1.1 -m "v1.1: <what changed>"
git push --follow-tags
```

Do not reintroduce the old `cp -r work v_0.X` scheme. Before this repo was put
under version control it used folder-based snapshots, and those eleven
snapshots were replayed into git as one commit each. Historical tags are
immutable — never rewrite or amend them.

The version string reported by `/health` and the startup log comes from the
`APP_VERSION` environment variable, which the Dockerfile sets from the build
arg. It is not hardcoded in `app.py`.

### Building an old version

`./commands/build.sh v0.5` checks that tag out into a git worktree under
`.worktrees/` (gitignored) and builds from there. This matters because the
versions are not interchangeable:

- `v0.1` listens on **port 4995** and depends on `requests`
- `v0.3` replaced the hand-rolled client with the vendored OpenAPI client and
  dropped `requests`
- `v0.1`–`v0.4` have no `argparse`; only `v0.5`+ accept `--route_type`
- only `v0.8`+ read `.env`

Each tagged commit therefore carries its own `Dockerfile`,
`docker-compose.yml`, and `requirements.txt`. When changing build tooling on
`main`, remember it will not retroactively apply to old tags — and must not.

Only one version runs at a time: the compose project (`mbta`) and container
name (`mbta-monitor`) are fixed, so building a different version replaces the
running container.

## Architecture

**Request flow:**
```
Browser → Flask (app.py) → MBTA_API (mbta_api.py) → MBTA v3 API
                         ↓
                   Formatter classes → JSON response → Vue.js frontend
```

**Core modules (all under `src/`):**

- `app.py` — Flask routes. Page routes (`/`, `/station/<name>`, `/alerts`) plus `/api/` JSON endpoints and `/api/station/<name>/stream` SSE endpoint. `/health` for status checks.
- `mbta_api.py` — `MBTA_API` class wrapping the auto-generated client in `lib/mbta_client/`. Station predictions use a single API call with `include=trip,vehicle,route` (parsed via `_parse_included` from `response.raw_data`) instead of N+1 parallel fetches. Caching TTLs: predictions=10s, routes=1hr, stops=24hr, alerts=1min. Rate-limit warning logged when `x-ratelimit-remaining < 20`.
- `cache_manager.py` — Thread-safe `CacheManager` with per-entry TTL and `Last-Modified` header tracking for HTTP 304 conditional requests.
- `station_formatter.py` — `StationDataFormatter(logger, debug)`. Converts `CollectedPrediction` objects into `FormattedStationData` (splits into `TLines` for rail/subway and `BusLines` for buses). Calculates human-readable wait times and handles special boarding statuses.
- `alert_formatter.py` — `AlertDataFormatter()`. Converts raw alerts into sorted `FormattedAlert` objects (sorted by severity descending).
- `multi_stop_formatter.py` — `MultiStopFormatter.format_nearby_stations(raw_stops, allowed_types)` (static method). Groups stops into parent stations, deduplicates routes, for the nearby stations page.

**Frontend:** Vue.js 3 via CDN + Tailwind CSS. Templates extend `base.html` (Jinja2 inheritance). Templates use `[[ ]]` delimiters (not `{{ }}`) to avoid Jinja2 conflicts. Dark mode is `localStorage`-backed with a toggle button on every page. The station monitor page (`stations.html`) uses SSE (`EventSource`) instead of polling.

**Auto-generated client:** `lib/mbta_client/` is generated from the MBTA v3 OpenAPI spec. Do not hand-edit it. Exception: `models/alert_resource_attributes.py` has had its enum validators relaxed to accept unknown values (e.g. `NOTICE`) that the API returns but the spec doesn't list. That edit is captured in `lib/mbta_client.enum-relax.patch` — reapply it if the client is regenerated.

## Key Behaviors to Know

- `CollectedPrediction` is a frozen dataclass — constructed once with all fields, never mutated.
- The nearby-stations center point comes from `MBTA_LAT`/`MBTA_LON` in `.env`. Change those values to relocate the home station. Do not commit real coordinates — `.env.example` intentionally points at Park Street.
- `_parse_included` in `mbta_api.py` parses the JSONAPI `included` array from `response.raw_data` using each model's `from_dict` constructor. The generated `Predictions` model silently drops the `included` field, so raw bytes must be used.
- The SSE endpoint (`/api/station/<name>/stream`) sleeps `PREDICTIONS_CACHE_TTL` (10s) between pushes. Each open connection holds one Flask thread — fine for a personal monitor, not for high concurrency.
- There are no tests. CI byte-compiles `src/` and builds the image; it is a smoke check, not a test suite.
- This is a LAN tool. It binds `0.0.0.0` with no auth, and `v0.1`/`v0.2` hardcode `debug=True` (Werkzeug debugger = RCE). Never expose it to the internet.

## MBTA v3 API Best Practices

Rate limit with an API key is 1,000 req/min. `x-ratelimit-remaining` is monitored and logged as a warning when below 20.

**Already implemented:** HTTP 304 caching (`If-Modified-Since` / `Last-Modified`), gzip compression (`Accept-Encoding: gzip`), nested includes (`include=trip,vehicle,route` on prediction calls), SSE for real-time station updates.

**Known improvement opportunities (not yet implemented):**

- **Sparse fieldsets** — All API methods in the generated client accept `fields_[type]` parameters (e.g. `fields_prediction`, `fields_trip`) to limit which attributes are returned. Not currently used.
- **Rate-limit header tracking** — `x-ratelimit-reset` is logged but not acted on. Under high load, the app could back off automatically when the limit is low.
