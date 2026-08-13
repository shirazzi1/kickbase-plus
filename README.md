<div align="center">
  <a href="https://de.kickbase.com/"><img width="400" alt="Logo" src="repo_pictures/kickbase.jpg"></a>
  <br>
  <h1>Kickbase Insights</h1>
  This project is a used to gather data from <a href="https://www.kickbase.com/">Kickbase</a> API endpoint and visualize it in a web interface, acting as alternative for the pro/member membership.

  ---

  <!-- Placeholder for badges -->
  ![GitHub License](https://img.shields.io/github/license/casudo/kickbase-insights) ![GitHub release (with filter)](https://img.shields.io/github/v/release/casudo/kickbase-insights)


</div>

> [!NOTE]
This is a hobby project to test stuff with JSON and the cores of Python. Feel free to create issues and contribute.  

##### Table of Contents
- [Screenshots](#screenshots)
- [Transfermarkt](#transfermarkt)
- [Docker](#docker)
  - [Persistent data](#persistent-data)
  - [docker run](#docker-run)
  - [Docker Compose](#docker-compose)
  - [Health check](#health-check)
- [Development](#development)
- [Planned for the future](#planned-for-the-future)
- [Thanks to](#thanks-to)
- [License](#license)

---

## Screenshots
You can find some screenshots of the frontend below, not all features are shown.  

> [!WARNING]
As of v1.4.0  

![Transfers](repo_pictures/transfers.png)  
![MarketValue](repo_pictures/marketvalue.png)  
![Revenue](repo_pictures/revenue.png)  
![LivePoints](repo_pictures/livepoints.png)  

## Transfermarkt
The "Dein Gebot" column shows, greyed out, the bid that would break even after
`BEP_TARGET_DAYS` days at the average daily market value growth of the last
`BEP_GROWTH_DAYS` days. Clicking the cell makes it editable: the checkmark places the bid
with Kickbase, the X withdraws a bid that is already standing. A dash means the market
value is currently flat or falling, or that the history is too short — the cell can still
be clicked to bid anyway.

This is the one feature that writes to Kickbase, so it needs the Flask API (`app.py`)
running alongside the frontend. Without it the cell reports that the API is unreachable;
everything else in the table keeps working.

The bid field also needs `BID_TOKEN` set. Both write endpoints require it on an
`X-Bid-Token` header; the frontend dev server adds that header itself (from its own
`BID_TOKEN` environment variable, via `frontend/src/setupProxy.js`) and it is never sent
to the browser. Because of that, **port 3000 must not be exposed publicly** — anything
that can reach the dev server can bid through its proxy exactly as the frontend does.
`BID_TOKEN` is required for the whole container to start, not only for this field — see
the Docker section below for why.

The bid field only works against the frontend dev server (`npm start`), which is what
the Docker container runs. A static `npm run build` bundle ships neither the proxy nor
the token, so the bid field cannot function there; that build target is left as-is.

## Docker
If you want to run this in a Docker container, you'll first need to set some mandatory environment variables:  

| Variable | Required | Description |
| --- | --- | --- |
| `KB_MAIL` | **Yes** | Your Kickbase E-Mail. |
| `KB_PASSWORD` | **Yes** | Your Kickbase password. |
| `KB_LIGA` | No | The name of the league you want to see data for in the GUI. If not set, defaults to the first league you're in. |
| `DISCORD_WEBHOOK` | **Yes** | The Discord webhook URL to send notifications to. |
| `DISCORD_MIN_SEVERITY` | No | How urgent an event from the Tagesplan has to be to reach the webhook: `1` sends everything, `2` adds the "watch this" events, `3` (the default) only the ones where waiting for the next run is probably too late. |
| `RUN_SCHEDULE` | No | The cron expression when the script should fetch new information from the API. If not set, defaults to `10 2,6,10,14,18,22 * * *`. |
| `START_DATE` | **Yes** | The instant the season started or your league was reset, as an ISO 8601 timestamp with an explicit UTC offset, e.g. `2026-08-01T18:00:00Z`. Events in the Kickbase activity feed from before this instant are excluded from the transfer, revenue and balance calculations. |
| `START_MONEY` | No | The amount of money you started with. If not set, defaults to 50.000.000€ |
| `TZ` | No | The timezone to use. Defaults to `Europe/Berlin` |
| `FLASK_PORT` | No | The port the dashboard and the API are served on. Defaults to `5000`. |
| `BID_TOKEN` | No | An extra shared secret accepted by the two Flask endpoints that place and withdraw a market bid (see [Transfermarkt](#transfermarkt)). **No longer required:** the server generates a token per start and hands it to the browser itself. Set it only if a script or a dev proxy has to bid without loading the page. |

> [!IMPORTANT]
> The format of `START_DATE` changed: the old `dd.mm.yyyy` format is no longer accepted and now causes a hard error on startup.
> If you are upgrading, migrate your value to an ISO 8601 timestamp with an explicit UTC offset (e.g. `2026-08-01T18:00:00Z`) and use the actual time of day the season started or your league was reset - the container will refuse to start otherwise.

> [!IMPORTANT]
> **One port now.** The container used to publish 3000 (a create-react-app dev server) and 5000
> (the API). Flask serves both the dashboard and the API from **5000**, or from `FLASK_PORT` if
> you set it. A `-p 3000:3000` from an older setup has nothing behind it any more.

> [!IMPORTANT]
> **`BID_TOKEN` is no longer required.** It was, briefly: the bid field's token could only reach
> Flask through the dev server's proxy, so an operator had to supply one. There is no dev server
> in the container any more, so the server generates a token per start and hands it to the browser
> with the page. A `BID_TOKEN` you already set keeps working and is still accepted - you can also
> drop it. See [Transfermarkt](#transfermarkt) for what the token does and does not protect.

> [!IMPORTANT]
> The Live tab shows the last live-points snapshot that was taken, not a live one: no scheduled
> run fetches them. Its age is shown in the tab. Refreshing them means calling
> `/api/livepoints`, which performs a full Kickbase login per request - which is why nothing in
> the UI does it for you.

### Persistent data
**Mount `/code/data`.** Everything the container cannot fetch again lives there, and
without the mount it is deleted on every image pull:

| Path | What it holds |
| --- | --- |
| `/code/data/public/` | The datasets the dashboard reads, served under `/api/data/<name>`, plus `timestamps/`. Rewritten by every run. |
| `/code/data/state/` | What only the backend reads: the season's activity feed (`all_transfers.json`), the achievement ledger and the id-to-name tables. |
| `/code/data/history/<dataset>/<YYYY-MM-DD>.ndjson` | The append-only history: one line per run per dataset, `{"ts": ..., "rows": ...}`. |
| `/code/data/last-good/` | The previous copy of each data file, kept before it is overwritten. |
| `/code/data/market-values/<player_id>.json` | One player's market value curve, plus the marker that says whether it is still current. |
| `/code/data/teams/teams-<competition_id>.json` | The team ids of a competition, so they do not have to be found by probing every run. |

The history is the part that cannot be recovered. Kickbase serves 31 days of market value
curve and nothing at all about yesterday's transfer market, so the only record of what was
listed, at what price, with how many bids, is the one this container writes. A run that was
never recorded is a hole in the record for good - which is why this is a mount and not an
optional extra.

It grows by roughly **2-3 MiB a day** at the default schedule of six runs (about 70 MiB a
month, under 1 GiB over a full season). No rotation or pruning happens: at that size the
data is worth more than the disk. If it ever needs to shrink, the day files are independent
and compress to about a tenth of their size, so `gzip` on everything older than a week is
the obvious first move.

`data/public` and `data/state` are rebuilt by the next scheduled run, so losing them costs one
run rather than data - but they are inside the mount anyway, which is what keeps the dashboard
from being empty between an image pull and the first run finishing. Only the logs are outside it.

**Upgrading from a version before this one:** these two directories are new. Everything that
used to sit in `frontend/src/data` is moved into them once, on the first start, by
`backend/state_migration.py` - it moves rather than copies, never overwrites a file the new
version already wrote, and never fails the start. Nothing to do by hand. If the old directory
still holds files afterwards, they are ones this project does not recognise, and the log says
which.

#### The two caches
Both are rebuilt from Kickbase if they are lost, so losing them costs requests rather than
data - but that is the whole point of them, so they belong in the mount.

**Market value curves** (`market-values/`). A run reads one curve per player in the
competition, around 466 of them, and Kickbase moves market values once a day. Each entry
keeps the curve plus the `mvud` marker the market response carried when it was fetched:

```json
{"version": 1, "playerId": "755", "mvud": "2026-08-13T21:00:00Z", "days": 31,
 "fetchedAt": "2026-08-13T09:12:44+02:00", "history": [{"dt": 20678, "mv": 12300000}]}
```

An entry is only used when the marker still matches, the window (`days`) is at least as wide
as the run needs, and the newest point of the curve is dated today or yesterday. Anything
else - including a market response without a marker at all - means the curve is fetched as it
always was. `fetchedAt` is for humans reading the directory; nothing depends on it. Delete
the directory to force a full refetch.

**Team ids** (`teams/`). There is no endpoint listing the teams of a competition, so they
used to be found by asking for ids 2 to 100. The list is remembered for 24 hours, which is
the clock a promoted team moves on, and a remembered list that no longer produces a full
competition is thrown away and the ids are looked for again.

### docker run
```bash
docker run -d \
    --name=kickbase_insights \
    --restart=unless-stopped \
    -p <port>:5000 \
    -e KB_MAIL=<kickbase_email> \
    -e KB_PASSWORD=<kickbase_password> \
    -e DISCORD_WEBHOOK=<discord_webhook> \
    -e START_DATE=<start_timestamp> \
    -e BID_TOKEN=<bid_token> \
    -v <your_folder>/kickbase-data:/code/data \
    ghcr.io/casudo/kickbase-insights:latest
```  
`<start_timestamp>` is an ISO 8601 timestamp in UTC, e.g. `2026-08-01T18:00:00Z`.  
`<bid_token>` is any long random string of your choosing; the container exits on startup without it.  
The backend port `5000` is configurable via the `FLASK_PORT` environment variable (e.g. `-e FLASK_PORT=<flask_port>`) and defaults to `5000` if unset. On macOS, the AirPlay Receiver occupies port `5000` by default, which stops Flask from binding to it - set `FLASK_PORT` to something else in that case.  

### Docker Compose
```yaml
version: "3.8"

services:
  kickbase-insights:
    image: ghcr.io/casudo/kickbase-insights:latest
    container_name: kickbase_insights
    restart: unless-stopped
    ports:
      - <port>:5000 # Dashboard and API, one port
    environment:
      - KB_MAIL=<kickbase_email>
      - KB_PASSWORD=<kickbase_password>
      - DISCORD_WEBHOOK=<discord_webhook>
      - START_DATE=<start_timestamp> # ISO 8601 in UTC, e.g. 2026-08-01T18:00:00Z
      - BID_TOKEN=<bid_token> # any long random string; container exits on startup without it
    volumes:
      # The append-only history, the last-good snapshots and the two request caches.
      # Without this they are deleted on every image pull, and the history cannot be
      # fetched again.
      - <your_folder>/kickbase-data:/code/data
```  
The backend port `5000` above is configurable via the `FLASK_PORT` environment variable and defaults to `5000` if unset. On macOS, the AirPlay Receiver occupies port `5000` by default, which stops Flask from binding to it - set `FLASK_PORT` to something else in that case.  

### Health check
The container reports its own state, so `docker ps` shows `healthy` or `unhealthy` instead
of just `Up`. The details are at `http://<host>:<port>/api/health`:

| Status | Meaning | HTTP |
|---|---|---|
| `ok` | The last run completed and every stage succeeded. | 200 |
| `degraded` | The last run was on time, but a stage failed. | 200 |
| `stale` | No run for far longer than `RUN_SCHEDULE` allows. | 503 |
| `unknown` | No run has ever completed. | 503 |

A failed stage stays **200** on purpose: restarting the container would not have made
Kickbase answer, and the affected tables are marked as out of date in the frontend's Dev
tab anyway. Only the two cases a restart can actually fix report unhealthy.

The threshold for `stale` follows `RUN_SCHEDULE`, so changing the schedule moves it along
instead of quietly invalidating it.

Discord gets one message when the runs start failing and one when they work again, plus a
note whenever the API had to be restarted.

### The HTTP surface
| Route | What it is |
|---|---|
| `/` and everything that is not `/api/...` | The prebuilt dashboard. |
| `/api/data/<name>` | One dataset out of `data/public`, from an allowlist. A dataset no run has written yet answers 404 with `"written": false`, and the dashboard renders that as an empty state. |
| `/api/data/timestamps` | Every `ts_*.json` in one document, keyed without the `ts_` prefix. The freshness markers are read from this, and it is polled once a minute so a finished run reaches an open tab without a reload. |
| `/api/health` | See above. |
| `/api/livepoints` | Fetches the live points from Kickbase. Performs a **full login per request**; nothing in the UI calls it. |

---

Behind a reverse proxy this is now a single service. In Traefik:

```yaml
http:
  routers:
    kickbase:
      service: kickbase
      rule: Host(`your.domain.de`)
      entryPoints:
        - websecure
      tls:
        certResolver: cloudflare

  services:
    kickbase:
      loadBalancer:
        servers:
          - url: http://<container_hostname>:5000
```
> [!IMPORTANT]
In order for this to work, both your reverse proxy and the container need to be in the same network.  

> [!NOTE]
It may take some time to initially start the container, so check the logs!  

---

## Development
The dashboard is a normal React app and the backend a normal Flask app, so neither needs a
container to work on.

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cd frontend && npm install
```

**The backend first.** Nothing in the frontend has any data to show until a run has written
some, and `main.py` needs real Kickbase credentials:

```bash
export KB_MAIL=... KB_PASSWORD=... DISCORD_WEBHOOK=... START_DATE=2026-08-01T18:00:00Z
./venv/bin/python main.py
```

It writes into `data/public` (what the dashboard reads) and `data/state` (what only the backend
reads). Both are gitignored.

**Then the two servers.** Flask serves the API:

```bash
./venv/bin/python -m flask run --port=5000
```

and, in a second shell, the React dev server with hot reload:

```bash
cd frontend && npm start
```

`npm start` serves the app on 3000 and forwards everything it cannot answer itself to Flask -
that is the `proxy` field in `frontend/package.json`, and it is why the relative `/api/data/...`
fetches work in development without any CORS setup. **The dev server is a development tool only.**
In the container there is none: the image builds the bundle once and Flask serves it.

To see what the container actually serves, build the frontend and let Flask hand it out:

```bash
cd frontend && npm run build     # writes frontend/build
./venv/bin/python -m flask run --port=5000
```

On macOS the AirPlay Receiver occupies port `5000` by default, which stops Flask from binding to
it. Set `FLASK_PORT` to something else in that case - `frontend/src/setupProxy.js` reads the same
variable, so the dev server follows along.

**Tests.** Python is plain scripts, no framework:

```bash
for t in tests/test_*.py; do ./venv/bin/python "$t"; done
```

and the frontend uses Jest through react-scripts:

```bash
cd frontend && CI=true npx react-scripts test
```


---

## Planned for the future
**Frontend:**  
- Market table: Maybe add ligainsider rating?
- Add base features
  - Feed
  - Lineup
  - Next matches
  - League table
  - Top players
- Transfererlöse: Hold player for X days  
- Sum. Transfererlöse: Add custom scale for chart  
- Misc: Unsold starter players    
- Reformat changelog  
- Other menu layout (+ mobile responsive)  
- Back to top button  
- ToC on pages with lot of content  
- Market value graph for players  

**Backend:**  
- Fix all TODOs  
- Add best practice to seperate duplicate variables names from modules (e.g. user and user. Which one is the module and which one is the variable?)   
- Discord notifications  
- Logging module for entrypoint.py and app.py    
- Add linter/formatter  
- Categorize components to frontend menu    
- Battles: Spieltagsdominator: Fix placements being wrong for people with the same amount of mdWins  
- Change behavior if player has the position number of "0". Instead of defaulting that to "1", do smth else
- Support for multiple leagues via ports (League 1: 5000, League 2: 5001, etc.)
- Rename "endpoints" to "classes" and put all of them into one file

**Misc:**  
- Add Postman workspace  
- Add Workflow chart  
- Automatically disable caching  

---

### Thanks to
- [@roman-la](https://github.com/roman-la) for the base of the frontend  

---

### License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details