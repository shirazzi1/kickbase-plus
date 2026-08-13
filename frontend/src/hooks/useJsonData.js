// Reading the datasets over HTTP instead of importing them into the bundle.
//
// Every table used to do `import data from "../data/market.json"`, which webpack resolved at
// build time. That is why a create-react-app dev server had to run in production: the only
// way fresh numbers reached a browser was a recompile after every scrape. It is also why one
// missing file failed the whole build.
//
// Now the backend serves data/public under /api/data/<name> and this module fetches it. Three
// things follow, and each of them is handled here rather than in fourteen components:
//
//   1. **There is a moment with no data.** Components used to get an array synchronously.
//      They now get a status first, so every caller renders a loading state.
//   2. **A missing file is normal.** events.json exists from the second run on,
//      manager_profiles.json from the first, live_points.json only when the live endpoint has
//      been called. The backend answers 404 for those, and this module turns that into an
//      empty value rather than an error - which is what require.context was faking before.
//   3. **The build no longer validates the shape.** See dataContracts.js: the one property
//      every caller depends on - list or keyed object - is checked before the payload is
//      handed over, so a dataset that is there but is the wrong kind of thing surfaces as an
//      error state instead of as a crash inside a .map().
//
// The freshness index is polled on a timer, and a run id that has changed bumps a generation
// counter every data hook depends on. That is the part of this phase a user actually sees: a
// finished scrape reaches an open tab without a page reload.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react"

import { emptyValue, matchesContract } from "./dataContracts"

// Where app.py serves the datasets. Relative on purpose: the same origin serves the frontend,
// and package.json's "proxy" forwards it to Flask under `npm start`.
export const DATA_BASE = "/api/data"

export const LOADING = "loading"
export const READY = "ready"
export const ERROR = "error"

// How often to ask whether a run has finished. The scrape runs every four hours by default,
// so this is not about catching it quickly - it is about not having to reload the page. One
// small JSON document per minute.
export const POLL_INTERVAL_MS = 60 * 1000

/**
 * Fetch one dataset.
 *
 * Returns `{ value }` with the parsed payload, or `{ value: null, missing: true }` when the
 * backend says the file has not been written yet. Anything else throws, with a German message
 * the error state can show.
 */
export async function fetchDataset(name, { signal } = {}) {
    const response = await fetch(`${DATA_BASE}/${name}`, { signal })

    // A dataset a run has not produced yet. The backend distinguishes this from an unknown
    // name, but the caller does not need to: both mean "nothing to show".
    if (response.status === 404)
        return { value: null, missing: true }

    if (!response.ok)
        throw new Error(`${name}: Server antwortete mit ${response.status}.`)

    let payload

    try {
        payload = await response.json()
    } catch (error) {
        throw new Error(`${name}: Antwort ist kein gültiges JSON.`)
    }

    if (!matchesContract(name, payload))
        throw new Error(`${name}: unerwartete Datenform.`)

    return { value: payload, missing: false }
}

/**
 * Fetch the whole freshness index: every ts_*.json in one response.
 *
 * The run manifest comes back under "run_manifest" and is pulled out, because it is what
 * turns a date into a judgement - see components/freshness.js.
 */
export async function fetchTimestamps({ signal } = {}) {
    const response = await fetch(`${DATA_BASE}/timestamps`, { signal })

    if (!response.ok)
        throw new Error(`Zeitstempel: Server antwortete mit ${response.status}.`)

    const index = await response.json()

    if (!index || typeof index !== "object" || Array.isArray(index))
        throw new Error("Zeitstempel: unerwartete Datenform.")

    return index
}

// What a component sees when nothing has provided a refresh context - which is every test
// that renders a component on its own. Generation 0 never changes, so the data is fetched
// once and that is the whole behaviour.
const NO_REFRESH = { generation: 0, timestamps: {}, manifest: null, status: LOADING }

export const DataRefreshContext = createContext(NO_REFRESH)

export function useDataRefresh() {
    return useContext(DataRefreshContext)
}

/**
 * The freshness index, kept current on a timer.
 *
 * `generation` is the point of it. It only moves when the run manifest reports a run id this
 * page has not seen, so a poll that finds nothing new costs one request and re-renders
 * nothing, while a finished scrape makes every data hook refetch.
 *
 * A failed poll leaves the previous index in place. The alternative - blanking the freshness
 * markers because one request did not land - would report every tab as unknown over a hiccup.
 */
export function useTimestampIndex({ pollInterval = POLL_INTERVAL_MS } = {}) {
    const [state, setState] = useState({ status: LOADING, index: {}, error: null })
    const [generation, setGeneration] = useState(0)

    // The run the current data belongs to. A ref rather than state: it is a comparison, and
    // storing it in state would make the poll re-run the effect that schedules it.
    //
    // `seeded` is separate from it on purpose. Both were once the same null, which broke
    // exactly the case this whole mechanism exists for: on a fresh volume the first answer
    // carries no run at all, so `seenRunId` stayed null, so *every* poll took the "this is
    // the first answer" branch - and when the first real run finally finished, its id was
    // recorded without ever bumping the generation. The freshness chips went green and the
    // tables stayed empty until someone reloaded the page.
    const seenRunId = useRef(null)
    const seeded = useRef(false)
    const [attempt, setAttempt] = useState(0)

    const reload = useCallback(() => setAttempt((n) => n + 1), [])

    useEffect(() => {
        let cancelled = false
        const controller = new AbortController()

        const poll = async () => {
            try {
                const index = await fetchTimestamps({ signal: controller.signal })

                if (cancelled)
                    return

                setState({ status: READY, index, error: null })

                const runId = index.run_manifest?.runId ?? index.main?.runId ?? null

                // The first answer establishes what "current" is - whatever it says, including
                // "no run at all" - and must not count as a change, or every mount would
                // refetch everything twice. Every answer after it is compared, and null is a
                // value like any other here: null -> RUN-1 is the first finished run on a
                // fresh volume, and it is precisely the moment the open tabs have to refetch.
                if (!seeded.current) {
                    seeded.current = true
                    seenRunId.current = runId
                } else if (runId !== seenRunId.current) {
                    seenRunId.current = runId
                    setGeneration((n) => n + 1)
                }
            } catch (error) {
                if (cancelled || error.name === "AbortError")
                    return

                setState((previous) => ({
                    status: previous.status === READY ? READY : ERROR,
                    index: previous.index,
                    error: error.message
                }))
            }
        }

        poll()

        // A non-positive interval switches polling off, which is what the tests want
        const timer = pollInterval > 0 ? setInterval(poll, pollInterval) : null

        return () => {
            cancelled = true
            controller.abort()

            if (timer)
                clearInterval(timer)
        }
    }, [pollInterval, attempt])

    return useMemo(() => ({
        status: state.status,
        error: state.error,
        timestamps: state.index,
        manifest: state.index.run_manifest ?? null,
        generation,
        reload
    }), [state, generation, reload])
}

/**
 * Fetch several datasets together.
 *
 * `status` is LOADING until all of them have settled, ERROR if any of them failed, READY
 * otherwise - a missing file is not a failure. `data` is keyed by file name, with the shape's
 * empty value where a file is absent *or where its fetch failed*, so a caller that renders
 * anyway cannot crash on top of an error it has already been told about. `missing` names only
 * the absent ones; `error` carries the first real failure's message, and a caller that wants
 * to tell the two apart reads `status` rather than `data`.
 *
 * On a refetch the previous payloads stay in `data`. A reload that blanked the table it is
 * refreshing would be worse than the stale numbers it replaces.
 */
export function useJsonFiles(names) {
    // The array is rebuilt on every render, so the effect keys off its contents instead
    const key = names.join(",")

    const [state, setState] = useState(() => ({
        // An empty list is READY straight away: the caller brought its own data, so there is
        // nothing to wait for and no state to churn through on the way to saying so.
        status: names.length === 0 ? READY : LOADING,
        data: Object.fromEntries(names.map((name) => [name, emptyValue(name)])),
        missing: [],
        error: null
    }))

    const [attempt, setAttempt] = useState(0)
    const reload = useCallback(() => setAttempt((n) => n + 1), [])

    const { generation } = useDataRefresh()

    useEffect(() => {
        let cancelled = false
        const controller = new AbortController()
        const wanted = key.split(",").filter(Boolean)

        if (wanted.length === 0)
            return

        setState((previous) => ({ ...previous, status: LOADING }))

        Promise.all(wanted.map((name) =>
            fetchDataset(name, { signal: controller.signal })
                .then((result) => ({ name, ...result }))
                .catch((error) => ({ name, error }))
        )).then((results) => {
            if (cancelled)
                return

            const failed = results.filter((result) => result.error && result.error.name !== "AbortError")

            setState({
                status: failed.length > 0 ? ERROR : READY,
                data: Object.fromEntries(results.map((result) =>
                    [result.name, result.error || result.missing ? emptyValue(result.name) : result.value])),
                missing: results.filter((result) => result.missing).map((result) => result.name),
                error: failed.length > 0 ? failed[0].error.message : null
            })
        })

        return () => {
            cancelled = true
            controller.abort()
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [key, attempt, generation])

    return { ...state, reload }
}

/**
 * Fetch one dataset.
 *
 * `data` is the payload, or the shape's empty value when the file is absent or the fetch
 * failed. `missing` says which of those it was, for the components whose empty state is a
 * sentence about the scrape rather than about an error.
 *
 * A falsy name fetches nothing and reports READY. That is for the components that take their
 * data as a prop with the fetch as the default: the hook still has to be called on every
 * render, but a caller that brought its own data must not also cause a request.
 */
export function useJsonData(name) {
    const names = useMemo(() => (name ? [name] : []), [name])
    const { status, data, missing, error, reload } = useJsonFiles(names)

    return {
        status,
        data: name ? data[name] : null,
        missing: missing.includes(name),
        error,
        reload
    }
}
