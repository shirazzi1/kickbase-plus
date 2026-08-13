// A stand-in for /api/data in the component tests.
//
// The tests used to say `jest.mock("../data/market.json", () => ([...]))`, which worked because
// the data was a module. It is a fetch now, so the seam moved from the module registry to
// `global.fetch` - and every test that renders a component which loads data needs one.
//
// Deliberately tiny and honest about status codes: a dataset that is not in the map answers 404,
// because that is what the backend does for a file the scrape has not written, and several
// components have a distinct empty state for exactly that case.
//
// Not a test file itself - it has no .test.js suffix, so react-scripts does not collect it, and
// nothing reachable from index.js imports it, so it stays out of the production bundle.

/**
 * Install a fetch that serves the given datasets.
 *
 * @param {object} datasets file name to payload, e.g. { "market.json": [] }
 * @param {object} timestamps the /api/data/timestamps response
 * @returns {object} the installed jest.fn, so a test can assert on the requests
 */
export function mockDataServer({ datasets = {}, timestamps = {}, failing = [] } = {}) {
    const respond = (body, status = 200) => Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        json: () => Promise.resolve(body)
    })

    const fetchMock = jest.fn((url) => {
        const path = String(url)

        if (path.endsWith("/api/data/timestamps"))
            return respond(timestamps)

        const name = path.split("/").pop()

        // A dataset the test wants to see fail, for the error states
        if (failing.includes(name))
            return respond({ error: "kaputt" }, 500)

        if (Object.prototype.hasOwnProperty.call(datasets, name))
            return respond(datasets[name])

        return respond({ error: `Unbekannter Datensatz: ${name}`, written: false }, 404)
    })

    global.fetch = fetchMock

    return fetchMock
}

/**
 * A timestamp index where every named dataset was written by the same, current run.
 *
 * The freshness markers only appear for datasets that are *not* current, so a test that does
 * not care about them wants this rather than an empty index - an empty one would put an
 * "unbekannt" chip above every tab.
 */
export function currentTimestamps(names, runId = "RUN-1") {
    const index = {
        run_manifest: { runId, allOk: true, stages: [] },
        main: { time: "2026-08-13T09:03:17", runId, allOk: true }
    }

    names.forEach((name) => {
        index[name] = { time: "2026-08-13T09:03:17", runId }
    })

    return index
}

/**
 * Put the global fetch back, so one suite's server cannot answer another suite's requests.
 */
export function restoreFetch() {
    delete global.fetch
}
