"""
### What a run did, stage by stage.

`main()` used to be one `try` around eight calls. The first exception aborted every
remaining stage, and the run then stamped `ts_main.json` fresh anyway - so a run that
failed at the first stage was, from the frontend's side, indistinguishable from a perfect
one. Somebody reading the market values had no way to tell whether they were minutes or
days old.

Two things fix that, and they only work together:

  - **Per-stage isolation.** A failing stage no longer takes the seven behind it with it,
    so a broken turnovers calculation does not also cost the market data.
  - **A record of what actually happened.** Isolation on its own is worse than nothing:
    it makes a partly failed run look complete, because the files the failed stage did not
    write simply keep their previous content. Every dataset therefore carries the id of
    the run that produced it, and the manifest says which stages of that run succeeded.

That is what makes "the market table is from run X, and run X's market stage was fine"
a question the frontend can answer per dataset, rather than one green badge for
everything.
"""

import logging
import uuid

from datetime import datetime, timezone

from backend import exceptions

### ===============================================================================

### A stage that ended in one of these has taken the ground out from under every stage
### behind it. An expired token does not become valid again three stages later - carrying
### on would mean seven more rounds of retries and backoff for seven identical failures,
### against an API that is already unhappy with us.
FATAL_EXCEPTIONS = (exceptions.AuthExpiredException, exceptions.LoginException)

### Stage outcomes, as they appear in the manifest
OK = "ok"
FAILED = "failed"
SKIPPED = "skipped"

### The run currently writing data. Set by start_run(), read by write_timestamp() in
### miscellaneous, so a stage does not have to thread the id through every call.
_current_run_id = None


def new_run_id() -> str:
    """### Make an id for one run of main.py.

    Sortable first, unique second: the timestamp makes a directory listing or a log grep
    read in order, and the suffix keeps two runs in the same second apart - which happens
    when a run fails early and the supervisor starts another.

    Returns:
        str: e.g. "20260813T114233Z-4f21".
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:4]}"


def start_run(run_id: str = None) -> str:
    """### Begin a run and make its id the one every write is stamped with.

    Args:
        run_id (str): An id to use instead of a fresh one. For tests.

    Returns:
        str: The run id.
    """
    global _current_run_id

    _current_run_id = run_id or new_run_id()

    return _current_run_id


def current_run_id() -> str:
    """### The id of the run currently writing, or None outside a run.

    Returns:
        str: The run id.
    """
    return _current_run_id


def end_run() -> None:
    """### Forget the current run id.

    app.py is a long lived process that also writes data files. Without this it would keep
    stamping them with the id of whichever run happened to be the last one.
    """
    global _current_run_id

    _current_run_id = None


class RunManifest:
    """### The record of one run: which stages ran, and how they ended."""

    def __init__(self, run_id: str):
        """### Start a manifest for a run.

        Args:
            run_id (str): The id of the run being recorded.
        """
        self.run_id = run_id
        self.started_at = datetime.now(timezone.utc)
        self.finished_at = None
        self.stages = []
        self.aborted_by = None

    def run(self, name: str, stage) -> bool:
        """### Run one stage, record how it went, and keep going.

        A stage is only skipped once something fatal has already happened - see
        FATAL_EXCEPTIONS. Everything else is contained here, because the whole point is
        that a broken stage costs its own dataset and no other.

        Args:
            name (str): The stage name, as the manifest and the frontend know it.
            stage (callable): The stage to run.

        Returns:
            bool: True if the stage succeeded.
        """
        if self.aborted_by is not None:
            self._record(name, SKIPPED, 0.0,
                         error=f"Skipped after {self.aborted_by} ended the run.")
            logging.warning(f"Skipping stage '{name}': the run was already over.")
            return False

        started = datetime.now(timezone.utc)
        logging.info(f"Stage '{name}' starting...")

        try:
            stage()
        except FATAL_EXCEPTIONS as e:
            duration = (datetime.now(timezone.utc) - started).total_seconds()
            self.aborted_by = name
            self._record(name, FAILED, duration, error=f"{type(e).__name__}: {e}")
            logging.error(f"Stage '{name}' failed fatally after {duration:.1f}s: {e}. "
                          "Nothing behind it can succeed either, so the run stops here.")
            return False
        except Exception as e:
            duration = (datetime.now(timezone.utc) - started).total_seconds()
            self._record(name, FAILED, duration, error=f"{type(e).__name__}: {e}")
            logging.exception(f"Stage '{name}' failed after {duration:.1f}s: {e}. "
                              "Its data keeps whatever the last successful run wrote.")
            return False

        duration = (datetime.now(timezone.utc) - started).total_seconds()
        self._record(name, OK, duration)
        logging.info(f"Stage '{name}' done in {duration:.1f}s.")

        return True

    def _record(self, name: str, status: str, duration: float, error: str = None) -> None:
        """### Add one stage result to the manifest.

        Args:
            name (str): The stage name.
            status (str): OK, FAILED or SKIPPED.
            duration (float): How long it took, in seconds.
            error (str): The failure, if there was one.
        """
        self.stages.append({
            "name": name,
            "status": status,
            "durationSeconds": round(duration, 1),
            "error": error,
        })

    def finish(self) -> None:
        """### Mark the run as over."""
        self.finished_at = datetime.now(timezone.utc)

    @property
    def all_ok(self) -> bool:
        """### Whether every stage of this run succeeded.

        An empty manifest is not ok. A run that got no further than the login has nothing
        to show, and reporting that as a success is the exact lie this replaces.

        Returns:
            bool: True if there is at least one stage and all of them are OK.
        """
        return bool(self.stages) and all(s["status"] == OK for s in self.stages)

    @property
    def failed_stages(self) -> list:
        """### The names of the stages that did not succeed.

        Returns:
            list: Stage names, in the order they ran.
        """
        return [s["name"] for s in self.stages if s["status"] != OK]

    def to_dict(self) -> dict:
        """### The manifest as the frontend reads it.

        Returns:
            dict: run id, times, the overall verdict and every stage.
        """
        return {
            "runId": self.run_id,
            "startedAt": self.started_at.isoformat(),
            "finishedAt": self.finished_at.isoformat() if self.finished_at else None,
            "allOk": self.all_ok,
            "abortedBy": self.aborted_by,
            "stages": self.stages,
        }

    def summary(self) -> str:
        """### One line for the log and for a Discord alert.

        Returns:
            str: e.g. "6/8 stages ok, failed: turnovers, balances".
        """
        ok = sum(1 for s in self.stages if s["status"] == OK)
        failed = self.failed_stages

        if not failed:
            return f"{ok}/{len(self.stages)} stages ok."

        return f"{ok}/{len(self.stages)} stages ok, not ok: {', '.join(failed)}."
