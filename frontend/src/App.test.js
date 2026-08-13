import { render, screen, fireEvent } from "@testing-library/react"

// A run where one stage failed. That is the case the whole manifest exists for: the other
// eight datasets are current, turnovers is a run behind, and nothing on the page said so
// before.
jest.mock("./data/timestamps/ts_run_manifest.json", () => ({
    runId: "20260813T090317Z-9c41",
    startedAt: "2026-08-13T09:01:44+00:00",
    finishedAt: "2026-08-13T09:03:17+00:00",
    allOk: false,
    abortedBy: null,
    stages: [
        { name: "market", status: "ok", durationSeconds: 12.1, error: null },
        { name: "turnovers", status: "failed", durationSeconds: 3.1, error: "KeyError: 'trp'" }
    ]
}))

jest.mock("./data/timestamps/ts_market.json", () => ({
    time: "2026-08-13T09:03:17", runId: "20260813T090317Z-9c41", rows: 91
}))

jest.mock("./data/timestamps/ts_turnovers.json", () => ({
    time: "2026-08-13T05:01:02", runId: "20260813T050102Z-1a2b", rows: 42
}))

// The Tagesplan tab reads events.json, which the scraper only writes from its first run on.
// Mocked virtually so this suite does not need a file that a fresh checkout cannot have.
jest.mock("./data/events.json", () => ([]), { virtual: true })

const App = require("./App").default

describe("App", () => {
    it("mounts with the manifest wired in", () => {
        render(<App />)
        expect(screen.getAllByText("Kickbase Insights").length).toBeGreaterThan(0)
    })

    it("marks the header as not ok when a stage failed", () => {
        // It used to be green whatever happened, so a scraper that had been failing for
        // two days looked exactly like one that had just finished
        render(<App />)

        const header = screen.getAllByText(/13\.8\.2026/)[0]
        expect(header.textContent).toContain("Fehler")
    })

    it("renders the stage list in the Dev tab", () => {
        render(<App />)
        fireEvent.click(screen.getByRole("tab", { name: "Dev" }))

        expect(screen.getByText(/✓ market/)).toBeTruthy()
        expect(screen.getByText(/✗ turnovers/)).toBeTruthy()
        // The error belongs on the page, not only in a log file nobody opens
        expect(screen.getByText("KeyError: 'trp'")).toBeTruthy()
    })

    it("tells the current datasets apart from the stale one", () => {
        render(<App />)
        fireEvent.click(screen.getByRole("tab", { name: "Dev" }))

        // Nine datasets are listed; the failed stage owns turnovers and revenue_sum
        expect(screen.getAllByText("aktuell").length).toBeGreaterThan(0)
        expect(screen.getAllByText("Fehler").length).toBeGreaterThan(0)
    })

    it("shows the row count a dataset was written with", () => {
        render(<App />)
        fireEvent.click(screen.getByRole("tab", { name: "Dev" }))

        expect(screen.getByText("91 Zeilen")).toBeTruthy()
    })
})
