# MBTA Monitor

A small Flask app that shows live MBTA arrival predictions for the stations
near a fixed point — built to run on a Raspberry Pi on a home network and
displayed on a wall-mounted tablet.

- **Nearby stations** — every stop within walking distance, grouped into parent
  stations, with the routes that serve them.
- **Station monitor** — live predictions for one station, pushed over
  Server-Sent Events rather than polled.
- **Alerts** — current service alerts, sorted by severity.
- Dark mode, because it sits on a wall.

Data comes from the [MBTA V3 API](https://www.mbta.com/developers/v3-api).

## Quick start

```bash
git clone https://github.com/ColinLangella/mbta.git
cd mbta
cp .env.example .env      # then add your API key
./commands/build.sh
```

Open <http://localhost:5000>.

Get a free API key at <https://api-v3.mbta.com/>. Without one you are limited
to 20 requests per minute; with one, 1,000.

`.env` holds three values:

| Variable | Meaning |
|---|---|
| `MBTA_API_KEY` | Your V3 API key |
| `MBTA_LAT` / `MBTA_LON` | Center point for the "nearby stations" page. Defaults to Park Street. |

### Commands

```bash
./commands/build.sh          # build and start the current checkout
./commands/build.sh v0.5     # build and start the tagged version v0.5
./commands/logs.sh           # follow logs
./commands/bash.sh           # shell into the running container
./commands/down.sh           # stop
```

### Running without Docker

```bash
pip install -r requirements.txt
cd src && python app.py --debug --route_type "0,1,3"
```

`--route_type` takes comma-separated integers: `0` light rail, `1` subway,
`2` commuter rail, `3` bus, `4` ferry.

## ⚠️ This is a LAN tool, not an internet-facing service

The app binds `0.0.0.0` with no authentication, and the earliest tagged
versions (`v0.1`, `v0.2`) hardcode Flask's `debug=True`. **Werkzeug's debugger
allows arbitrary code execution.** Do not port-forward this to the internet or
run the old tags on an untrusted network.

## Version history

This project spent about a year under a folder-based versioning scheme —
each milestone was a copy of the working directory into `v_0.1/`, `v_0.2/`,
and so on, with no version control at all. When it was published, those
eleven snapshots were replayed into git as one commit each:

```bash
git tag --list 'v*' | sort -V     # v0.1 .. v1.0
git checkout v0.5                 # src/ is now exactly the v_0.5 snapshot
./commands/build.sh v0.5          # ...and it still builds and runs
```

**The history is a reconstruction, and every commit says so.** The commits are
not original — each is a folder-to-folder delta, not a real development step —
and the author dates are the snapshot folders' mtimes, not commit times. Two
things were changed from the snapshots as committed: home coordinates were
replaced with a public landmark, and the build files (`Dockerfile`,
`docker-compose.yml`, `requirements.txt`, `commands/`) were written during the
reconstruction and back-fitted to each tag so that old versions still build.

That last part is why each tag carries its own build files rather than sharing
one set: the versions genuinely differ. `v0.1` listens on port 4995 and uses
`requests`; `v0.3` swapped in the generated API client and dropped `requests`;
only `v0.5` and later understand `--route_type`; only `v0.8` and later read
`.env`. `./commands/build.sh <tag>` checks the tag out into a git worktree
under `.worktrees/` and builds it there, so it gets that version's own
Dockerfile.

Roughly what changed when:

| Tag | |
|---|---|
| `v0.1` | Flask + hand-rolled `requests` client, one station page, port 4995 |
| `v0.2` | Threaded prediction fetches, port 5000 |
| `v0.3` | Replaced the hand-rolled client with the generated OpenAPI client |
| `v0.4` | `CacheManager` with per-entry TTL and HTTP 304 conditional requests |
| `v0.5` | `argparse` CLI (`--debug`, `--route_type`) |
| `v0.6` | Alerts and nearby-stations pages; formatters split by concern |
| `v0.7` | Single-call predictions via `include=trip,vehicle,route` |
| `v0.8` | PEP 8 module names, `base.html` inheritance, coordinates moved to `.env` |
| `v0.9` | SSE station stream replacing polling; dark mode |
| `v1.0` | First stable release |

## Architecture

```
Browser  ->  Flask (app.py)  ->  MBTA_API (mbta_api.py)  ->  MBTA V3 API
                             ->  Formatters  ->  JSON  ->  Vue 3 frontend
```

| File | |
|---|---|
| `src/app.py` | Flask routes: pages, `/api/` JSON endpoints, the `/api/station/<name>/stream` SSE endpoint, `/health` |
| `src/mbta_api.py` | Wraps the vendored client. Caching TTLs: predictions 10s, routes 1h, stops 24h, alerts 1m |
| `src/cache_manager.py` | Thread-safe cache with per-entry TTL and `Last-Modified` tracking for 304s |
| `src/station_formatter.py` | Predictions to display rows; wait times and boarding statuses |
| `src/alert_formatter.py` | Alerts sorted by severity |
| `src/multi_stop_formatter.py` | Groups stops into parent stations for the nearby page |

The frontend is Vue 3 and Tailwind from CDNs, with Jinja2 templates that use
`[[ ]]` delimiters so they don't collide with Vue's `{{ }}`.

### The vendored API client

`lib/mbta_client/` is generated from the MBTA V3 OpenAPI spec and should not be
hand-edited — with one exception. The spec omits some alert enum values the API
actually returns (for example `NOTICE`), so the validators in
`models/alert_resource_attributes.py` were relaxed to accept unknown values.
That change is recorded in `lib/mbta_client.enum-relax.patch`; apply it if you
regenerate the client.

## Tests

There are none. CI byte-compiles the source and builds the Docker image, which
catches syntax errors and broken dependency pins — it is a smoke check, not a
test suite.

## License

MIT — see [LICENSE](LICENSE).
