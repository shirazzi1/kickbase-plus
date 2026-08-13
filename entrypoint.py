import json
import subprocess

from os import getenv, path
from time import sleep, time
from croniter import croniter
from datetime import datetime

from backend import exceptions, health, miscellaneous, state_migration, supervisor
from backend.paths import TIMESTAMP_DIR

### ===============================================================================

### How often to look at the Flask process while waiting for the next run. It used to be
### started and never looked at again, so it could die at any point in the four hours
### between runs and the container would sit there apparently fine.
POLL_INTERVAL_SECONDS = 15

### The one port the container serves from. Flask hands out the API and the prebuilt frontend,
### so the 3000/5000 split is gone along with the create-react-app dev server that needed it.
DEFAULT_FLASK_PORT = "5000"

### The same default backend/health.py uses for the staleness check, so the schedule and
### the threshold derived from it cannot disagree.
DEFAULT_RUN_SCHEDULE = health.DEFAULT_RUN_SCHEDULE

### ===============================================================================

### Convert RUN_SCHEDULE (cron expression) to a valid date. Only run the python script (main.py) when the cron expression is met.
### TODO: This works, but isnt beatiful.
def convert_cron_to_timestamp(cron_expression):
    current_time = datetime.now()
    # print(f"DB: DEF -> current time: {current_time}")
    cron = croniter(cron_expression, current_time)
    next_execution = cron.get_next(datetime)
    # print("DB: DEF -> next execution: ", next_execution)
    return next_execution.timestamp()


def read_run_manifest():
    """### Read what the run that just finished recorded about itself.

    Args:
        None

    Returns:
        dict: The manifest, or None if it could not be read.
    """
    manifest_path = path.join(TIMESTAMP_DIR, "ts_run_manifest.json")

    try:
        with open(manifest_path, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  ⚠️ Could not read the run manifest: {e}")
        return None


def run_scraper(reporter):
    """### Run main.py once and act on how it went.

    The exit code has been honest since the manifest was introduced, and nothing looked
    at it: subprocess.run() returns a result that was thrown away, so a run that failed
    every single stage was indistinguishable from one that worked.

    Args:
        reporter (supervisor.RunReporter): Decides whether this outcome is worth an alert.

    Returns:
        int: main.py's exit code.
    """
    result = subprocess.run(["python3", "-u", "/code/main.py"])
    manifest = read_run_manifest()

    if result.returncode == 0:
        print("  ✅ Run finished, every stage ok.")
    else:
        failed = [s["name"] for s in (manifest or {}).get("stages", [])
                  if s.get("status") != "ok"]
        print(f"  ❌ Run finished with exit code {result.returncode}. "
              f"Not ok: {', '.join(failed) if failed else 'see the log'}")

    reporter.report(result.returncode, manifest)

    return result.returncode

def check_environment():
    """### Read the environment variables and refuse to start without the required ones.

    Args:
        None

    Returns:
        dict: The settings the rest of the entrypoint needs.
    """
    ### Get the environment variables
    KB_MAIL = getenv("KB_MAIL")
    KB_PASSWORD = getenv("KB_PASSWORD")
    KB_LIGA = getenv("KB_LIGA")
    DISCORD_WEBHOOK = getenv("DISCORD_WEBHOOK")
    RUN_SCHEDULE = getenv("RUN_SCHEDULE", DEFAULT_RUN_SCHEDULE)
    ### 10 */8 * * * -> At minute 10 past every 8th hour
    ### 10 2,6,10,14,18,22 * * * -> At minute 10 past every 4th hour starting from 2am
    START_DATE = getenv("START_DATE")
    START_MONEY = getenv("START_MONEY", "50000000")
    BID_TOKEN = getenv("BID_TOKEN")
    FLASK_PORT = getenv("FLASK_PORT", "5000")

    ### Display a welcoming message in Docker logs
    print("👍 Container started. Welcome!")
    print("⏳ Checking environment variables...")

    ### Check if the environment variables are set
    ### Required Kickbase Account
    if KB_MAIL is None or KB_PASSWORD is None:
        print("  ❌ Your Kickbase credentials are not fully set. Exiting...")
        exit(1)
    else:
        print("  ✅ Your Kickbase credentials are set.")

    ### Optional preferred league name
    if KB_LIGA:
        print(f"  ✅ Your preferred league name is set: {KB_LIGA}")
    else:
        print("  ⚠️ No preferred league set, using default one.")

    ### Discord Webhook URL
    if DISCORD_WEBHOOK is None:
        print("  ❌ DISCORD_WEBHOOK is not set. Exiting...")
        exit(1)
    else:
        print("  ✅ DISCORD_WEBHOOK is set.")

    ### The bid field's token. No longer required: app.py generates one per boot and hands
    ### it to the browser as a cookie with index.html, because the only carrier it used to
    ### have was the create-react-app dev server's proxy - which does not run in a container
    ### any more. Refusing to start over a missing BID_TOKEN would now refuse over something
    ### the server supplies itself.
    ###
    ### A value that *is* set stays accepted alongside the generated one, so a script or a
    ### dev proxy that carries it keeps working. Announced rather than checked.
    if BID_TOKEN:
        print("  ✅ BID_TOKEN is set and will be accepted in addition to the per-boot token.")
    else:
        print("  ✅ Bid token is generated per start; no BID_TOKEN needed.")

    ### Check if RUN_SCHEDULE is using the default value
    if RUN_SCHEDULE == DEFAULT_RUN_SCHEDULE:
        print("  ✅ Using default value for RUN_SCHEDULE:", RUN_SCHEDULE)
    else:
        print("  ⚠️ RUN_SCHEDULE has been set to a custom value:", RUN_SCHEDULE)

    ### Check if FLASK_PORT is using the default value
    ### On macOS the AirPlay Receiver occupies port 5000 by default, so Flask cannot bind
    ### there unless this is changed - see frontend/src/setupProxy.js for the matching
    ### change on the frontend side.
    if FLASK_PORT == "5000":
        print("  ✅ Using default value for FLASK_PORT:", FLASK_PORT)
    else:
        print("  ⚠️ FLASK_PORT has been set to a custom value:", FLASK_PORT)

    ### Check if START_DATE is set by user.
    ### Uses the same parser as main.py so both agree on what a valid value is.
    try:
        miscellaneous.get_start_datetime()
        print(f"  ✅ START_DATE is set to '{START_DATE}'.")
    except exceptions.KickbaseException as e:
        print(f"  ❌ {e} Exiting...")
        exit(1)

    ### Check the break-even horizons, next to START_MONEY below. Same parser as
    ### main.py, so both agree on what a valid value is.
    try:
        bep_growth_days, bep_target_days = miscellaneous.get_bep_days()
        print(f"  ✅ Break-even horizons: {bep_growth_days} day growth average, "
              f"{bep_target_days} day payback.")
    except exceptions.KickbaseException as e:
        print(f"  ❌ {e} Exiting...")
        exit(1)

    ### Check if START_MONEY is set
    if START_MONEY in ("50000000", "200000000"):
        formatted_money = f"{int(START_MONEY):,}".replace(",", ".") + "€"
        mode = "Auslosung" if START_MONEY == "50000000" else "Ohne Team"
        print(f"  ✅ Using game mode '{mode}' with {formatted_money} as starting money.")
    else:
        print("  ❌ START_MONEY is not set to a valid value. Exiting...")
        exit(1)

    return {"discord_webhook": DISCORD_WEBHOOK, "run_schedule": RUN_SCHEDULE,
            "flask_port": FLASK_PORT}


def build_children(flask_port: str = DEFAULT_FLASK_PORT):
    """### The long lived process the container serves from.

    One, not two. There used to be a create-react-app dev server here as well, serving a
    bundle with the data compiled into it - which is why the container ran `npm install` on
    every start, slept two minutes twice waiting for two servers, and published two ports.
    Flask serves the prebuilt frontend and the API from the same port now.

    The supervision itself is unchanged: whatever is in this list gets polled and restarted.

    Args:
        flask_port (str): The port Flask binds to. Configurable because port 5000 is
            occupied by the AirPlay Receiver on macOS by default - see the FLASK_PORT
            check in check_environment() and frontend/src/setupProxy.js for the matching
            change on the frontend side.

    Returns:
        list: The Flask API, not started yet.
    """
    return [
        supervisor.Child("flask api",
                         ["python3", "-u", "-m", "flask", "run", "--host=0.0.0.0",
                          f"--port={flask_port}"],
                         cwd="/code"),
    ]


def supervise(children, reporter, run_schedule, webhook):
    """### Watch the children and run the scraper on schedule, forever.

    The loop used to sleep the entire gap to the next run in one go. Nothing looked at the
    children during those four hours, which is why they could die unnoticed.

    Args:
        children (list): The supervisor.Child processes to keep alive.
        reporter (supervisor.RunReporter): Decides which run outcomes are worth an alert.
        run_schedule (str): The cron expression for the scraper.
        webhook (str): The Discord webhook URL.
    """
    next_execution_timestamp = convert_cron_to_timestamp(run_schedule)
    announced_execution = None
    # print(f"DB: WHILE -> next execution timestamp: {next_execution_timestamp}")

    while True:
        current_time_timestamp = datetime.now().timestamp()
        # print(f"DB: WHILE -> current time timestamp: {current_time_timestamp}")

        ### Restart anything that died
        for restarted in supervisor.check_children(children, time()):
            if restarted.should_alert():
                supervisor.notify(
                    f"Kickbase Insights: {restarted.name} restarted",
                    f"`{restarted.name}` exited with code {restarted.last_exit_code} and was "
                    f"started again (attempt {restarted.restarts}).",
                    supervisor.COLOUR_WARNING, webhook)

        if current_time_timestamp >= next_execution_timestamp:
            ### Run the python script (auto_entry.py)
            print("\n  🚀 Running main.py...\n\n")
            ### TODO: Log output
            run_scraper(reporter)

            next_execution_timestamp = convert_cron_to_timestamp(run_schedule)
            announced_execution = None
            # print(f"DB: WHILE -> next execution timestamp: {next_execution_timestamp}")
        else:
            ### Log the next scheduled execution time, once per schedule rather than once
            ### per poll - the loop now wakes up every few seconds to check the children
            if announced_execution != next_execution_timestamp:
                next_execution_readable = datetime.fromtimestamp(next_execution_timestamp).strftime('%A, %B %d, %Y %I:%M %p')
                print("\n\n▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼")
                print(f"👀 Next execution will be on: {next_execution_readable}")
                print("▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲")
                announced_execution = next_execution_timestamp

            ### Sleep until the next scheduled time, or until the next look at the
            ### children, whichever comes first
            sleep_duration = min(POLL_INTERVAL_SECONDS,
                                 next_execution_timestamp - current_time_timestamp)
            # print("DB: WHILE -> sleeping for: ", sleep_duration)
            sleep(max(1, sleep_duration))


### ===============================================================================

if __name__ == "__main__":
    settings = check_environment()

    ### Whether the last run was already reported to Discord, so a Kickbase outage does
    ### not turn into six identical messages a day
    run_reporter = supervisor.RunReporter(settings["discord_webhook"])

    ### Move what an older version wrote in frontend/src/data into data/public and data/state,
    ### once. Both children do this too, since either can be started on its own - but doing it
    ### here first means they never race each other over the same files.
    moved = state_migration.migrate_legacy_layout()

    if moved:
        print(f"  📦 {moved} Datei(en) aus frontend/src/data nach data/ verschoben.")

    ### Flask first, so the dashboard answers while the first run is still walking the
    ### competition. It used to come up last, four minutes and one npm install after the
    ### container started.
    ###
    ### Nothing supervises it for the length of that first run: supervise() below is what
    ### polls the children, and run_scraper() sits in front of it. A Flask that dies in
    ### those minutes stays dead until the first run finishes, and is then restarted on the
    ### next poll. Accepted rather than fixed here - moving the scrape into the loop is a
    ### change to the supervisor's shape, and the loop already runs it on schedule.
    supervised = build_children(settings["flask_port"])

    for child in supervised:
        child.start()

    print("\n  🚀 Running main.py...\n\n")
    run_scraper(run_reporter)

    ### No sleeps here any more. They existed to give a create-react-app dev server and then
    ### Flask time to come up before anything looked at them - and nothing did look at them,
    ### which is what the supervisor loop below was built for. It polls every few seconds and
    ### restarts what died, so waiting a fixed four minutes bought nothing but four minutes.
    supervise(supervised, run_reporter, settings["run_schedule"],
              settings["discord_webhook"])