/* eslint-disable testing-library/no-unnecessary-act -- `await act(async () => {})` is
   how a fetch that no assertion waits for gets settled inside act(). Without it React
   warns about the update, and the warning would hide a real one. */
import { act } from "react"
import { render, screen } from "@testing-library/react"

import { emptyValue, matchesContract } from "./dataContracts"
import { fetchDataset, useJsonData, useJsonFiles, useTimestampIndex } from "./useJsonData"
import { currentTimestamps, mockDataServer, restoreFetch } from "./mockDataServer"

// The seam that replaced thirteen compile-time imports. Everything the components stopped
// having to think about lives here, so it is worth testing on its own rather than only through
// a DataGrid.

afterEach(restoreFetch)

// A component that renders whatever the hook says, so the states can be read off the DOM
const Probe = ({ name }) => {
    const { status, data, missing, error } = useJsonData(name)

    return (
        <div>
            <span data-testid="status">{status}</span>
            <span data-testid="missing">{String(missing)}</span>
            <span data-testid="error">{error ?? ""}</span>
            <span data-testid="data">{JSON.stringify(data)}</span>
        </div>
    )
}

const read = (id) => screen.getByTestId(id).textContent

describe("fetchDataset", () => {
    it("hands back the payload", async () => {
        mockDataServer({ datasets: { "market.json": [{ playerId: "1" }] } })

        expect(await fetchDataset("market.json")).toEqual({
            value: [{ playerId: "1" }], missing: false
        })
    })

    it("reads a 404 as 'not written yet' rather than as a failure", async () => {
        // events.json exists from the second run on, manager_profiles.json from the first,
        // live_points.json only when the live endpoint has been called
        mockDataServer({ datasets: {} })

        expect(await fetchDataset("events.json")).toEqual({ value: null, missing: true })
    })

    it("refuses a payload of the wrong shape", async () => {
        // The compile-time imports were free schema validation. This is the part of it that
        // matters: a dataset that is there but is not the kind of thing the caller is about
        // to iterate used to be impossible and now crashes inside a .map().
        mockDataServer({ datasets: { "market.json": { not: "a list" } } })

        await expect(fetchDataset("market.json")).rejects.toThrow(/Datenform/)
    })

    it("says what went wrong on a server error", async () => {
        mockDataServer({ datasets: {}, failing: ["market.json"] })

        await expect(fetchDataset("market.json")).rejects.toThrow(/500/)
    })
})

describe("matchesContract", () => {
    it("knows a list from a keyed object", () => {
        expect(matchesContract("market.json", [])).toBe(true)
        expect(matchesContract("market.json", {})).toBe(false)
        expect(matchesContract("team_values.json", {})).toBe(true)
        expect(matchesContract("team_values.json", [])).toBe(false)
    })

    it("lets a dataset it does not know through", () => {
        // A new dataset must not be blocked by a table that has not heard of it yet
        expect(matchesContract("whatever.json", [1, 2])).toBe(true)
    })
})

describe("emptyValue", () => {
    it("matches the dataset's own shape, so a caller can iterate without guarding", () => {
        expect(emptyValue("market.json")).toEqual([])
        expect(emptyValue("team_values.json")).toEqual({})
    })
})

describe("useJsonData", () => {
    it("starts loading and ends up with the data", async () => {
        mockDataServer({ datasets: { "market.json": [{ playerId: "1" }] } })

        render(<Probe name="market.json" />)
        expect(read("status")).toBe("loading")

        await act(async () => {})

        expect(read("status")).toBe("ready")
        expect(read("data")).toBe(JSON.stringify([{ playerId: "1" }]))
    })

    it("reports a missing dataset as ready and empty", async () => {
        mockDataServer({ datasets: {} })

        render(<Probe name="events.json" />)
        await act(async () => {})

        expect(read("status")).toBe("ready")
        expect(read("missing")).toBe("true")
        expect(read("data")).toBe("[]")
    })

    it("reports a failure with its reason", async () => {
        mockDataServer({ datasets: {}, failing: ["market.json"] })

        render(<Probe name="market.json" />)
        await act(async () => {})

        expect(read("status")).toBe("error")
        expect(read("error")).toMatch(/500/)
        // And the empty value, so a component that renders anyway cannot crash on top of it
        expect(read("data")).toBe("[]")
    })

    it("fetches nothing at all for a falsy name", async () => {
        // The seam for components that take their data as a prop with the fetch as the default
        const server = mockDataServer({ datasets: {} })

        render(<Probe name={null} />)
        await act(async () => {})

        expect(server).not.toHaveBeenCalled()
        expect(read("status")).toBe("ready")
    })
})

describe("useJsonFiles", () => {
    const Several = () => {
        const { status, data, missing, error } = useJsonFiles(["market.json", "manager_profiles.json"])

        return (
            <div>
                <span data-testid="status">{status}</span>
                <span data-testid="missing">{missing.join(",")}</span>
                <span data-testid="error">{error ?? ""}</span>
                <span data-testid="data">{JSON.stringify(data["market.json"])}</span>
            </div>
        )
    }

    it("is ready when one of several is merely absent", async () => {
        // The market table's normal state on a deployment whose profiles stage has never run
        mockDataServer({ datasets: { "market.json": [{ playerId: "1" }] } })

        render(<Several />)
        await act(async () => {})

        expect(read("status")).toBe("ready")
        expect(read("missing")).toBe("manager_profiles.json")
        expect(read("data")).toBe(JSON.stringify([{ playerId: "1" }]))
    })

    it("is in error when one of several actually failed", async () => {
        mockDataServer({ datasets: { "market.json": [] }, failing: ["manager_profiles.json"] })

        render(<Several />)
        await act(async () => {})

        expect(read("status")).toBe("error")
    })

    it("asks once per dataset rather than once per render", async () => {
        const server = mockDataServer({ datasets: { "market.json": [] } })

        render(<Several />)
        await act(async () => {})

        expect(server).toHaveBeenCalledTimes(2)
    })
})

describe("useTimestampIndex", () => {
    const Index = ({ onRefresh }) => {
        const refresh = useTimestampIndex({ pollInterval: 0 })
        onRefresh(refresh)

        return (
            <div>
                <span data-testid="status">{refresh.status}</span>
                <span data-testid="generation">{String(refresh.generation)}</span>
                <span data-testid="error">{refresh.error ?? ""}</span>
                <span data-testid="run">{refresh.manifest?.runId ?? ""}</span>
            </div>
        )
    }

    // The latest refresh object the hook produced, so a case can call reload()
    const showIndex = async () => {
        let latest = null

        render(<Index onRefresh={(refresh) => { latest = refresh }} />)
        await act(async () => {})

        return () => latest
    }

    it("reads the manifest out of the index", async () => {
        mockDataServer({ timestamps: currentTimestamps(["market"], "RUN-1") })

        await showIndex()

        expect(read("status")).toBe("ready")
        expect(read("run")).toBe("RUN-1")
    })

    it("does not count the first answer as a change", async () => {
        // Otherwise every mount would refetch every dataset a second time
        mockDataServer({ timestamps: currentTimestamps(["market"], "RUN-1") })

        await showIndex()

        expect(read("generation")).toBe("0")
    })

    it("bumps the generation when a new run has finished", async () => {
        // This is what makes a finished scrape reach an open tab without a page reload
        mockDataServer({ timestamps: currentTimestamps(["market"], "RUN-1") })

        const refresh = await showIndex()

        mockDataServer({ timestamps: currentTimestamps(["market"], "RUN-2") })

        await act(async () => { refresh().reload() })

        expect(read("generation")).toBe("1")
    })

    it("leaves the generation alone when the run is the same", async () => {
        mockDataServer({ timestamps: currentTimestamps(["market"], "RUN-1") })

        const refresh = await showIndex()

        await act(async () => { refresh().reload() })

        expect(read("generation")).toBe("0")
    })

    it("keeps the last index when a poll fails", async () => {
        // Blanking every freshness marker over one dropped request would report the whole
        // dashboard as unknown for a hiccup
        mockDataServer({ timestamps: currentTimestamps(["market"], "RUN-1") })

        const refresh = await showIndex()

        global.fetch = jest.fn(() => Promise.reject(new Error("Failed to fetch")))

        await act(async () => { refresh().reload() })

        expect(read("run")).toBe("RUN-1")
        expect(read("status")).toBe("ready")
        expect(read("error")).toMatch(/Failed to fetch/)
    })
})
