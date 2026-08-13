import {
    datasetStatus,
    runStatus,
    runSummary,
    statusColour,
    statusLabel,
    CURRENT,
    STALE,
    FAILED,
    UNKNOWN
} from "./freshness"

const RUN = "20260813T120000Z-4f21"
const EARLIER_RUN = "20260813T080000Z-1a2b"

const manifest = (stages) => ({
    runId: RUN,
    allOk: stages.every((s) => s.status === "ok"),
    stages
})

const ok = (name) => ({ name, status: "ok", durationSeconds: 1, error: null })
const failed = (name) => ({ name, status: "failed", durationSeconds: 1, error: "KeyError: trp" })
const skipped = (name) => ({ name, status: "skipped", durationSeconds: 0, error: "Skipped" })

describe("datasetStatus", () => {
    it("calls a dataset current when this run wrote it", () => {
        const stamp = { time: "2026-08-13T12:00:00", runId: RUN }
        expect(datasetStatus(stamp, manifest([ok("market")]), "market")).toBe(CURRENT)
    })

    it("calls a dataset stale when an earlier run wrote it", () => {
        // The stage succeeded this run but wrote nothing new — not a case the pipeline
        // produces today, but the reading must not depend on that
        const stamp = { time: "2026-08-13T08:00:00", runId: EARLIER_RUN }
        expect(datasetStatus(stamp, manifest([ok("market")]), "market")).toBe(STALE)
    })

    it("says failed when the stage that owns the dataset did not succeed", () => {
        // The file is still there and still plausible; only the manifest knows better
        const stamp = { time: "2026-08-13T08:00:00", runId: EARLIER_RUN }
        expect(datasetStatus(stamp, manifest([failed("market")]), "market")).toBe(FAILED)
    })

    it("says failed for a stage that was skipped after a fatal error", () => {
        const stamp = { time: "2026-08-13T08:00:00", runId: EARLIER_RUN }
        expect(datasetStatus(stamp, manifest([skipped("balances")]), "balances")).toBe(FAILED)
    })

    it("maps both player tables onto the one stage that writes them", () => {
        const stamp = { time: "2026-08-13T08:00:00", runId: EARLIER_RUN }
        const m = manifest([failed("taken_free_players")])

        expect(datasetStatus(stamp, m, "taken_players")).toBe(FAILED)
        expect(datasetStatus(stamp, m, "free_players")).toBe(FAILED)
    })

    it("maps the revenue chart onto the turnovers stage that produces it", () => {
        const stamp = { time: "2026-08-13T08:00:00", runId: EARLIER_RUN }
        expect(datasetStatus(stamp, manifest([failed("turnovers")]), "revenue_sum")).toBe(FAILED)
    })

    it("admits it does not know rather than guessing current", () => {
        const stamp = { time: "2026-08-13T12:00:00", runId: RUN }

        // No manifest at all — an old deployment, or a container that has never run
        expect(datasetStatus(stamp, null, "market")).toBe(UNKNOWN)
        // A timestamp from before run ids existed
        expect(datasetStatus({ time: "2026-08-13T12:00:00" }, manifest([ok("market")]), "market")).toBe(UNKNOWN)
        // No timestamp
        expect(datasetStatus(null, manifest([ok("market")]), "market")).toBe(UNKNOWN)
    })

    it("does not fall over on a dataset no stage claims", () => {
        const stamp = { time: "2026-08-13T08:00:00", runId: EARLIER_RUN }
        expect(datasetStatus(stamp, manifest([ok("market")]), "something_new")).toBe(STALE)
    })
})

describe("runStatus", () => {
    it("is current only when every stage succeeded", () => {
        expect(runStatus(manifest([ok("market"), ok("balances")]))).toBe(CURRENT)
    })

    it("is failed as soon as one stage did not", () => {
        expect(runStatus(manifest([ok("market"), failed("balances")]))).toBe(FAILED)
    })

    it("is unknown without a manifest", () => {
        // The old header was green unconditionally, which is what made a dead scraper
        // invisible for as long as nobody noticed the numbers had stopped moving
        expect(runStatus(null)).toBe(UNKNOWN)
        expect(runStatus({})).toBe(UNKNOWN)
    })
})

describe("runSummary", () => {
    it("counts the stages when everything worked", () => {
        expect(runSummary(manifest([ok("market"), ok("balances")]))).toContain("Alle 2")
    })

    it("names the stages that did not", () => {
        const summary = runSummary(manifest([ok("market"), failed("balances"), skipped("turnovers")]))

        expect(summary).toContain("balances")
        expect(summary).toContain("turnovers")
        expect(summary).toContain("1/3")
    })

    it("says so when there is no manifest", () => {
        expect(runSummary(null)).toContain("Kein Lauf-Protokoll")
    })
})

describe("statusColour and statusLabel", () => {
    it("gives every status a colour and a German label", () => {
        for (const status of [CURRENT, STALE, FAILED, UNKNOWN]) {
            expect(typeof statusColour(status)).toBe("string")
            expect(typeof statusLabel(status)).toBe("string")
        }
    })

    it("falls back to the unknown colour rather than to undefined", () => {
        expect(statusColour("nonsense")).toBe(statusColour(UNKNOWN))
        expect(statusLabel("nonsense")).toBe(statusLabel(UNKNOWN))
    })
})
