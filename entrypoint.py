import json
import subprocess

from os import getenv, chdir, path
from time import sleep, time
from croniter import croniter
from datetime import datetime

from backend import exceptions, health, miscellaneous, supervisor
from backend.paths import TIMESTAMP_DIR

### ===============================================================================

### How often to look at the frontend and API processes while waiting for the next run.
### They used to be started and never looked at again, so either could die at any point
### in the four hours between runs and the container would sit there apparently fine.
POLL_INTERVAL_SECONDS = 15

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

    ### Check if RUN_SCHEDULE is using the default value
    if RUN_SCHEDULE == DEFAULT_RUN_SCHEDULE:
        print("  ✅ Using default value for RUN_SCHEDULE:", RUN_SCHEDULE)
    else:
        print("  ⚠️ RUN_SCHEDULE has been set to a custom value:", RUN_SCHEDULE)

    ### Check if START_DATE is set by user.
    ### Uses the same parser as main.py so both agree on what a valid value is.
    try:
        miscellaneous.get_start_datetime()
        print(f"  ✅ START_DATE is set to '{START_DATE}'.")
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

    return {"discord_webhook": DISCORD_WEBHOOK, "run_schedule": RUN_SCHEDULE}


def build_children():
    """### The long lived processes the container serves from.

    They used to be started with Popen and never looked at again: either could die at any
    point in the four hours between runs, and the container would keep sitting there
    apparently fine, serving nothing.

    Args:
        None

    Returns:
        list: The frontend and the Flask API, not started yet.
    """
    return [
        supervisor.Child("frontend", ["npm", "start"], cwd="/code/frontend"),
        supervisor.Child("flask api",
                         ["python3", "-u", "-m", "flask", "run", "--host=0.0.0.0", "--port=5000"],
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

    # print("\nDEBUG ep.py: Running main")
    print("\n  🚀 Running main.py...\n\n")
    run_scraper(run_reporter)

    # print("\nDEBUG ep.py: Changing directiry")
    chdir("/code/frontend")
    # print("\nDEBUG ep.py: npm install")
    subprocess.run(["npm", "install"])
    # subprocess.run(["npm", "install", "jest"])

    supervised = build_children()

    # print("\nDEBUG ep.py: npm start")
    supervised[0].start()

    ### Sleep here to give the frontend time to start
    sleep(120)

    # print("\nDEBUG ep.py: Changing directiry")
    chdir("/code/")
    # print("\nDEBUG ep.py: Starting flask api")
    supervised[1].start()

    ### Sleep here to give the flask server time to start
    sleep(120)

    supervise(supervised, run_reporter, settings["run_schedule"],
              settings["discord_webhook"])