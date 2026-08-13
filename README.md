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
| `RUN_SCHEDULE` | No | The cron expression when the script should fetch new information from the API. If not set, defaults to `10 2,6,10,14,18,22 * * *`. |
| `START_DATE` | **Yes** | The instant the season started or your league was reset, as an ISO 8601 timestamp with an explicit UTC offset, e.g. `2026-08-01T18:00:00Z`. Events in the Kickbase activity feed from before this instant are excluded from the transfer, revenue and balance calculations. |
| `START_MONEY` | No | The amount of money you started with. If not set, defaults to 50.000.000€ |
| `TZ` | No | The timezone to use. Defaults to `Europe/Berlin` |
| `BID_TOKEN` | **Yes** | A shared secret required by the two Flask endpoints that place and withdraw a market bid (see [Transfermarkt](#transfermarkt)). **The container will not start without it, even if you never use the bid field.** Pick any long random string; only the frontend dev server ever sends it, and it never reaches the browser. |

> [!IMPORTANT]
> The format of `START_DATE` changed: the old `dd.mm.yyyy` format is no longer accepted and now causes a hard error on startup.
> If you are upgrading, migrate your value to an ISO 8601 timestamp with an explicit UTC offset (e.g. `2026-08-01T18:00:00Z`) and use the actual time of day the season started or your league was reset - the container will refuse to start otherwise.

> [!IMPORTANT]
> `BID_TOKEN` is a new **required** variable. If you are upgrading an existing container, add it before you restart - `entrypoint.py` exits immediately if it is missing, so the whole container (scraper included) will refuse to start, not just the bid field.
> That is a deliberate trade-off: one column's secret becoming a requirement for the entire container is a feature-level need escalated to an app-level one, made on purpose to keep the check simple and fail loudly rather than have the bid field fail confusingly later. See [Transfermarkt](#transfermarkt) for what the token protects.

> [!IMPORTANT]
> The live points feature is currently on-hold and not present as of v2.4.0!
> To handle the re-implementation of the live points with more ease, the ports for the backend are not commented out.

### docker run
```bash
docker run -d \
    --name=kickbase_insights \
    --restart=unless-stopped \
    -p <frontend_port>:3000 -p <backend_port>:5000 \
    -e KB_MAIL=<kickbase_email> \
    -e KB_PASSWORD=<kickbase_password> \
    -e DISCORD_WEBHOOK=<discord_webhook> \
    -e START_DATE=<start_timestamp> \
    -e BID_TOKEN=<bid_token> \
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
      - <frontend_port>:3000 # Web GUI
      - <backend_port>:5000 # Backend API (../api/livepoints)  
    environment:
      - KB_MAIL=<kickbase_email>
      - KB_PASSWORD=<kickbase_password>
      - DISCORD_WEBHOOK=<discord_webhook>
      - START_DATE=<start_timestamp> # ISO 8601 in UTC, e.g. 2026-08-01T18:00:00Z
      - BID_TOKEN=<bid_token> # any long random string; container exits on startup without it
```  
The backend port `5000` above is configurable via the `FLASK_PORT` environment variable and defaults to `5000` if unset. On macOS, the AirPlay Receiver occupies port `5000` by default, which stops Flask from binding to it - set `FLASK_PORT` to something else in that case.  

### Health check
The container reports its own state, so `docker ps` shows `healthy` or `unhealthy` instead
of just `Up`. The details are at `http://<host>:<backend_port>/api/health`:

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
note whenever the frontend or the API had to be restarted.

---

If you run this container in your LAN (via IP), you'll need to change the following line in the `App.js` file in the `frontend/src` folder to this (obv. change `<backend_port>`):     
```js
const response = await fetch('http://localhost:<backend_port>/api/livepoints')
```  

If you make this container publically available via a domain, you'll need to create/update the following entry in your reverse proxy:  
`your.domain.com -> <container_ip_or_hostname>:3000`  
`your.domain.com/api/livepoints -> <container_ip_or_hostname>:5000`  
> [!IMPORTANT]
In order to this to work, both your reverse proxy and the container need to be in the same network.  

In Traefik, the dynamic config would look like this:  
```yaml
http:
  routers:
    kickbase-web:
      service: kickbase-web
      rule: Host(`your.domain.de`)
      entryPoints:
        - websecure
      tls:
        certResolver: cloudflare

    kickbase-api:
      service: kickbase-api
      rule: Host(`your.domain.de`) && PathPrefix(`/api/livepoints`)
      entryPoints:
        - websecure
      tls:
        certResolver: cloudflare

  services:
    kickbase-web:
      loadBalancer:
        servers:
          - url: http://<container_hostname>:3000

    kickbase-api:
      loadBalancer:
        servers:
          - url: http://<container_hostname>:5000
```

> [!NOTE]
It may take some time to initially start the container, so check the logs!  

---

## Development
If you want to contribute to this project, you can follow the steps below to jump right into the development environment.  
```bash
docker run -dit --name=Kickbase -p <frontend_port>:3000 -p <backend_port>:5000 -e KB_MAIL=<kickbase_mail> -e KB_PASSWORD=<kickbase_password> -e DISCORD_WEBHOOK=<discord_webhook> -e WATCHPACK_POLLING=true -e START_DATE=<start_timestamp> ubuntu
```  
Run this long command to setup the container:  
```bash
mkdir /code && cd /code && apt update && apt upgrade -y && apt install tree nano python3 python3-pip git curl -y && git clone https://github.com/casudo/Kickbase-Insights.git . && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs && pip install --upgrade pip && pip install --upgrade -r requirements.txt && mkdir -p frontend/src/data/timestamps && mkdir logs && cd frontend && npm install
```  

If you have this project already cloned, you can run the following command to bind mount the files inside the container:  
```bash
docker run -dit --name=Kickbase -p <frontend_port>:3000 -p <backend_port>:5000 -e KB_MAIL=<kickbase_mail> -e KB_PASSWORD=<kickbase_password> -e DISCORD_WEBHOOK=<discord_webhook> -e WATCHPACK_POLLING=true -e START_DATE=<start_timestamp> -v <your_folder>\Kickbase-Insights:/code ubuntu
```  
Run this long command to setup the container:  
```bash
cd /code && apt update && apt upgrade -y && apt install tree nano python3 python3-pip curl -y && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs && pip install --upgrade pip && pip install --upgrade -r requirements.txt && mkdir -p frontend/src/data/timestamps && mkdir logs && cd frontend && npm install
```  

Now you're ready to go. Keep in mind that you'll first need to run `main.py` to get the required data for the frontend.  
`python3 main.py`  

You'll also need to manually run `npm start` in the `frontend` folder as well as `python3 -u -m flask run --host=0.0.0.0 --port=5000` in the `/code` folder.  
The port `5000` here is configurable via the `FLASK_PORT` environment variable and defaults to `5000` if unset - pass it as `--port=$FLASK_PORT` instead if you set one. On macOS, the AirPlay Receiver occupies port `5000` by default, which stops Flask from binding to it - set `FLASK_PORT` to something else in that case.  

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