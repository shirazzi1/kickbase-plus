import { act } from "react"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"

import { mockDataServer, restoreFetch } from "./hooks/mockDataServer"

// A run where one stage failed. That is the case the whole manifest exists for: the other
// datasets are current, turnovers is a run behind, and nothing on the page said so before.
//
// This used to be a jest.mock of ./data/timestamps/*.json - fourteen module mocks standing in
// for fourteen compile-time imports. There is one document now, fetched from
// /api/data/timestamps, so the fixture is one object.
const TIMESTAMPS = {
    run_manifest: {
        runId: "20260813T090317Z-9c41",
        startedAt: "2026-08-13T09:01:44+00:00",
        finishedAt: "2026-08-13T09:03:17+00:00",
        allOk: false,
        abortedBy: null,
        stages: [
            { name: "market", status: "ok", durationSeconds: 12.1, error: null },
            { name: "turnovers", status: "failed", durationSeconds: 3.1, error: "KeyError: 'trp'" }
        ]
    },
    main: { time: "2026-08-13T09:03:17", runId: "20260813T090317Z-9c41", allOk: false },
    market: { time: "2026-08-13T09:03:17", runId: "20260813T090317Z-9c41", rows: 91 },
    turnovers: { time: "2026-08-13T05:01:02", runId: "20260813T050102Z-1a2b", rows: 42 }
}

// Nothing else is served, so every tab shows its own empty state. That is the point: the tabs
// have to come up on a deployment whose first run has not finished, which is precisely what the
// compile-time imports made impossible.
beforeEach(() => {
    mockDataServer({ timestamps: TIMESTAMPS })
})

afterEach(restoreFetch)

const App = require("./App").default

// The header date is the first thing the timestamp index feeds, so it is what every case waits
// for before it looks at anything
const showApp = async () => {
    render(<App />)
    await screen.findAllByText(/13\.8\.2026/)
    // The Tagesplan is the tab that opens first and it fetches too, so its answer belongs
    // inside act() with the index's rather than after the case has finished
    await act(async () => {})
}

// Opening a tab mounts tables that immediately fetch. Their answers land after the assertions of
// a case that was only about the tab bar, and React warns about every one of those updates -
// noise that would hide a real warning. This flushes them where they belong.
const openTab = async (name) => {
    fireEvent.click(screen.getByRole("tab", { name }))
    await act(async () => {})
}

describe("App", () => {
    it("mounts with the manifest wired in", async () => {
        await showApp()
        expect(screen.getAllByText("Kickbase Insights").length).toBeGreaterThan(0)
    })

    it("marks the header as not ok when a stage failed", async () => {
        // It used to be green whatever happened, so a scraper that had been failing for
        // two days looked exactly like one that had just finished
        await showApp()

        const header = screen.getAllByText(/13\.8\.2026/)[0]
        expect(header.textContent).toContain("Fehler")
    })

    it("renders the stage list in the Dev tab", async () => {
        await showApp()
        await openTab("Dev")

        expect(screen.getByText(/✓ market/)).toBeTruthy()
        expect(screen.getByText(/✗ turnovers/)).toBeTruthy()
        // The error belongs on the page, not only in a log file nobody opens
        expect(screen.getByText("KeyError: 'trp'")).toBeTruthy()
    })

    it("tells the current datasets apart from the stale one", async () => {
        await showApp()
        await openTab("Dev")

        expect(screen.getAllByText("aktuell").length).toBeGreaterThan(0)
        expect(screen.getAllByText("Fehler").length).toBeGreaterThan(0)
    })

    it("shows the row count a dataset was written with", async () => {
        await showApp()
        await openTab("Dev")

        expect(screen.getByText("91 Zeilen")).toBeTruthy()
    })

    it("puts a freshness chip on the tab whose data is behind", async () => {
        // The header carries one badge for the whole run, which cannot say that the table in
        // front of you is the stale one. This is what the per-tab chips are for.
        await showApp()
        await openTab("Transfererlöse")

        expect(await screen.findByText("turnovers: Fehler")).toBeTruthy()
    })

    it("puts no chip on a tab whose data is current", async () => {
        // A row of green chips on every tab is decoration. Only the exceptions are shown.
        await showApp()
        await openTab("Transfers")

        expect(screen.queryByText("market: aktuell")).toBeNull()
        expect(screen.queryByText(/^market:/)).toBeNull()
    })

    it("makes the Live tab reachable", async () => {
        // Commented out until this phase: live_points.json is not written by a scheduled run,
        // and a compile-time import of a missing file failed the build for every other tab too
        await showApp()

        expect(screen.getByRole("tab", { name: "Live" })).toBeTruthy()

        await openTab("Live")

        // Nothing served it, so the tab says so rather than breaking
        expect(await screen.findByText(/Live-Punkte: noch keine Daten/)).toBeTruthy()
    })

    it("survives a backend that answers nothing at all", async () => {
        // The container starting up, or a reverse proxy that does not forward /api. Every tab
        // has to come up and say what happened.
        restoreFetch()
        global.fetch = jest.fn(() => Promise.reject(new Error("Failed to fetch")))

        await act(async () => { render(<App />) })

        await waitFor(() => expect(screen.getAllByText("Kickbase Insights").length).toBeGreaterThan(0))

        // The header has no date to show and does not invent one. `new Date(undefined)` renders
        // as "Invalid Date", which was impossible while these were compile-time imports.
        expect(screen.queryAllByText(/Invalid Date/).length).toBe(0)
    })
})
