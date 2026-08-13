"""Tests for the HTTP routes the frontend now reads its data through.

The datasets used to be compiled into the React bundle, so there was no route to test and
no allowlist to get wrong. Both exist now, and the interesting cases are all refusals: a
backend-private file, a traversal attempt, a dataset that has not been written yet.

Dependency free on purpose: the project has no test framework, so this runs with the project
venv directly and needs no extra packages.

    ./venv/bin/python tests/test_data_api.py
"""

import json
import sys
import tempfile

from os import makedirs, path

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import state_migration

### app.py migrates the old data layout when it is imported, which is right in a container
### and wrong in a test: it would create directories in the checkout. Stubbed before the
### import, since the module looks the function up on this object when it calls it.
state_migration.migrate_legacy_layout = lambda: 0

import app as flask_app

### ===============================================================================

PASSED = []


def check(name, fn):
    """Run a single test and record the result."""
    try:
        fn()
    except AssertionError as e:
        print(f"  FAIL  {name}\n        {e}")
        PASSED.append(False)
    except Exception as e:
        print(f"  ERROR {name}\n        {type(e).__name__}: {e}")
        PASSED.append(False)
    else:
        print(f"  ok    {name}")
        PASSED.append(True)


class Deployment:
    """A temporary data directory and build directory, wired into the Flask app."""

    def __init__(self, public=None, timestamps=None, build=None):
        self.public = public or {}
        self.timestamps = timestamps or {}
        self.build = build or {}

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name

        self.public_dir = path.join(root, "public")
        self.ts_dir = path.join(self.public_dir, "timestamps")
        self.state_dir = path.join(root, "state")
        self.build_dir = path.join(root, "build")

        for directory in (self.ts_dir, self.state_dir, self.build_dir):
            makedirs(directory, exist_ok=True)

        for name, payload in self.public.items():
            with open(path.join(self.public_dir, name), "w") as f:
                json.dump(payload, f)

        for name, payload in self.timestamps.items():
            ### Written as text so a deliberately broken file is possible
            with open(path.join(self.ts_dir, name), "w") as f:
                f.write(payload if isinstance(payload, str) else json.dumps(payload))

        for name, body in self.build.items():
            with open(path.join(self.build_dir, name), "w") as f:
                f.write(body)

        self.original = (flask_app.PUBLIC_DIR, flask_app.TIMESTAMP_DIR,
                         flask_app.FRONTEND_BUILD_DIR)
        flask_app.PUBLIC_DIR = self.public_dir
        flask_app.TIMESTAMP_DIR = self.ts_dir
        flask_app.FRONTEND_BUILD_DIR = self.build_dir

        ### The static handler resolves its directory per request, so pointing it at the
        ### temporary build is enough - no second app instance needed.
        flask_app.app.static_folder = self.build_dir

        self.client = flask_app.app.test_client()

        return self

    def __exit__(self, *exc):
        (flask_app.PUBLIC_DIR, flask_app.TIMESTAMP_DIR,
         flask_app.FRONTEND_BUILD_DIR) = self.original
        flask_app.app.static_folder = self.original[2]
        self.tmp.cleanup()
        return False


def stamp(run_id, rows=None):
    """A timestamp document the way write_timestamp() writes one."""
    payload = {"time": "2026-08-13T09:03:17", "runId": run_id}

    if rows is not None:
        payload["rows"] = rows

    return payload


### ===============================================================================
### /api/data/<name>


def test_a_public_dataset_is_served():
    with Deployment(public={"market.json": [{"playerId": "1"}]}) as d:
        response = d.client.get("/api/data/market.json")

        assert response.status_code == 200, f"got {response.status_code}"
        assert response.get_json() == [{"playerId": "1"}], f"got {response.get_json()}"


def test_a_public_dataset_is_served_as_json():
    """A DataGrid does response.json(), which needs the content type to say so."""
    with Deployment(public={"market.json": []}) as d:
        response = d.client.get("/api/data/market.json")

        assert "json" in response.headers["Content-Type"], \
            f"got {response.headers['Content-Type']}"


def test_a_backend_private_dataset_is_refused():
    """The point of the split: nothing serves data/state, so this must fail even when the
    file is sitting in the served directory by mistake."""
    with Deployment(public={"STATIC_users.json": {"1": "Meier"}}) as d:
        response = d.client.get("/api/data/STATIC_users.json")

        assert response.status_code == 404, f"got {response.status_code}"
        assert "Meier" not in response.get_data(as_text=True), \
            "the league's names must not reach the response"


def test_a_traversal_attempt_is_refused():
    with Deployment() as d:
        for attempt in ("../state/STATIC_users.json",
                        "..%2Fstate%2FSTATIC_users.json",
                        "....//state//STATIC_users.json"):
            response = d.client.get(f"/api/data/{attempt}")

            assert response.status_code == 404, f"{attempt} got {response.status_code}"


def test_an_unwritten_dataset_says_so():
    """events.json only exists from the second run on. That is a state, not a failure."""
    with Deployment() as d:
        response = d.client.get("/api/data/events.json")

        assert response.status_code == 404, f"got {response.status_code}"
        assert response.get_json()["written"] is False, f"got {response.get_json()}"


def test_an_unknown_api_route_stays_a_404():
    """Handing index.html to a fetch() would turn a missing route into a parse error."""
    with Deployment(build={"index.html": "<html>app</html>"}) as d:
        response = d.client.get("/api/nonsense")

        assert response.status_code == 404, f"got {response.status_code}"
        assert response.get_json()["error"], "an API 404 answers with JSON"


### ===============================================================================
### /api/data/timestamps


def test_the_timestamp_index_holds_every_file():
    with Deployment(timestamps={
        "ts_market.json": stamp("RUN-2", rows=91),
        "ts_turnovers.json": stamp("RUN-1", rows=42),
        "ts_run_manifest.json": {"runId": "RUN-2", "allOk": True, "stages": []},
    }) as d:
        index = d.client.get("/api/data/timestamps").get_json()

        assert set(index) == {"market", "turnovers", "run_manifest"}, f"got {sorted(index)}"
        assert index["market"]["rows"] == 91, f"got {index['market']}"
        assert index["run_manifest"]["allOk"] is True, f"got {index['run_manifest']}"


def test_the_timestamp_index_survives_one_broken_file():
    """One damaged file must not cost the freshness of the other thirteen."""
    with Deployment(timestamps={
        "ts_market.json": stamp("RUN-2"),
        "ts_turnovers.json": "{ half a write",
    }) as d:
        response = d.client.get("/api/data/timestamps")

        assert response.status_code == 200, f"got {response.status_code}"
        assert set(response.get_json()) == {"market"}, f"got {sorted(response.get_json())}"


def test_the_timestamp_index_is_empty_before_the_first_run():
    with Deployment() as d:
        response = d.client.get("/api/data/timestamps")

        assert response.status_code == 200, f"got {response.status_code}"
        assert response.get_json() == {}, f"got {response.get_json()}"


### ===============================================================================
### The frontend


def test_the_root_serves_the_built_index():
    with Deployment(build={"index.html": "<html>kickbase</html>"}) as d:
        response = d.client.get("/")

        assert response.status_code == 200, f"got {response.status_code}"
        assert "kickbase" in response.get_data(as_text=True)


def test_a_build_asset_is_served():
    with Deployment(build={"index.html": "<html/>", "asset-manifest.json": "{}"}) as d:
        response = d.client.get("/asset-manifest.json")

        assert response.status_code == 200, f"got {response.status_code}"


def test_an_unknown_path_falls_back_to_the_app():
    with Deployment(build={"index.html": "<html>kickbase</html>"}) as d:
        response = d.client.get("/irgendwas")

        assert "kickbase" in response.get_data(as_text=True), \
            "a route inside the single page app has to reach the app"


def test_a_missing_build_says_what_is_missing():
    """A bare 404 here reads as 'wrong URL' when the answer is 'the image skipped a stage'."""
    with Deployment() as d:
        response = d.client.get("/")

        assert response.status_code == 503, f"got {response.status_code}"
        assert "npm run build" in response.get_json()["error"], f"got {response.get_json()}"


### ===============================================================================
### The bid token the page is served with


def test_the_index_hands_out_a_bid_token():
    """The bid field's token used to reach Flask only through the CRA dev server's proxy.

    There is no dev server in front of Flask any more, so the token had no carrier left and
    every bid would have answered 401. index.html now arrives with a cookie holding a token
    this process generated at boot, and the frontend puts it in the X-Bid-Token header.
    """
    with Deployment(build={"index.html": "<html/>"}) as d:
        response = d.client.get("/")

        cookie = response.headers.get("Set-Cookie", "")

        assert flask_app.BID_TOKEN_COOKIE in cookie, f"got {cookie!r}"
        assert flask_app.BOOT_BID_TOKEN in cookie, "the cookie must carry the boot token"


def test_the_bid_token_cookie_is_readable_by_the_page():
    """Deliberately not HttpOnly. The frontend has to read the value and put it in a header -
    a cookie the browser attaches by itself would travel with a cross-site request too, which
    is exactly what has to be prevented."""
    with Deployment(build={"index.html": "<html/>"}) as d:
        cookie = d.client.get("/").headers.get("Set-Cookie", "")

        assert "HttpOnly" not in cookie, f"got {cookie!r}"
        assert "SameSite=Strict" in cookie, f"got {cookie!r}"


def test_the_bid_token_cookie_is_not_marked_secure():
    """This is routinely served over plain HTTP on a LAN. A Secure cookie would never
    arrive there, and every bid would be a 401 with no hint why."""
    with Deployment(build={"index.html": "<html/>"}) as d:
        cookie = d.client.get("/").headers.get("Set-Cookie", "")

        assert "Secure" not in cookie, f"got {cookie!r}"


### ===============================================================================
### What must not have changed


def test_the_health_route_still_answers():
    """It shipped in phase 1c and the Docker healthcheck depends on it. The static handler
    now owns /<path>, so this also proves the API routes still win over it."""
    with Deployment() as d:
        response = d.client.get("/api/health")

        assert response.status_code in (200, 503), f"got {response.status_code}"
        assert "status" in response.get_json(), f"got {response.get_json()}"


def test_there_is_exactly_one_health_route():
    """Phase 1c added it; this phase must not have added a second one."""
    rules = [r.rule for r in flask_app.app.url_map.iter_rules() if r.rule == "/api/health"]

    assert len(rules) == 1, f"got {rules}"


def test_cors_is_gone():
    """One origin serves both halves now, so there is no cross origin request to permit."""
    with Deployment(public={"market.json": []}) as d:
        response = d.client.get("/api/data/market.json")

        assert "Access-Control-Allow-Origin" not in response.headers, \
            "nothing should be handing out a CORS header any more"


### ===============================================================================

if __name__ == "__main__":
    print("/api/data/<name>")
    check("serves a public dataset", test_a_public_dataset_is_served)
    check("serves it as JSON", test_a_public_dataset_is_served_as_json)
    check("refuses a backend-private dataset", test_a_backend_private_dataset_is_refused)
    check("refuses a traversal attempt", test_a_traversal_attempt_is_refused)
    check("says when a dataset is not written yet", test_an_unwritten_dataset_says_so)
    check("keeps unknown API routes as 404", test_an_unknown_api_route_stays_a_404)

    print("\n/api/data/timestamps")
    check("holds every timestamp file", test_the_timestamp_index_holds_every_file)
    check("survives one broken file", test_the_timestamp_index_survives_one_broken_file)
    check("is empty before the first run", test_the_timestamp_index_is_empty_before_the_first_run)

    print("\nthe frontend")
    check("the root serves index.html", test_the_root_serves_the_built_index)
    check("a build asset is served", test_a_build_asset_is_served)
    check("an unknown path falls back to the app", test_an_unknown_path_falls_back_to_the_app)
    check("a missing build says what is missing", test_a_missing_build_says_what_is_missing)

    print("\nthe bid token")
    check("index.html hands out a bid token", test_the_index_hands_out_a_bid_token)
    check("the page can read the cookie", test_the_bid_token_cookie_is_readable_by_the_page)
    check("the cookie is not Secure", test_the_bid_token_cookie_is_not_marked_secure)

    print("\nwhat must not have changed")
    check("the health route still answers", test_the_health_route_still_answers)
    check("there is exactly one health route", test_there_is_exactly_one_health_route)
    check("no CORS headers are handed out", test_cors_is_gone)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
