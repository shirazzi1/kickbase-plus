import { render, screen, within, fireEvent, act } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import MarketTable from "./MarketTable"

// Kept apart from MarketTable.test.js rather than merged into it: that file needs the
// real @mui/x-data-grid (it asserts on the row-id warning the real component logs), while
// every test here needs the "Dein Gebot" column to actually render past jsdom's zero-size
// layout, which only the mock below achieves. jest.mock("@mui/x-data-grid", ...) is
// file-scoped, so both cannot live under one root without one suite silently testing
// against the other's fixture.
//
// @mui/x-data-grid's column/row virtualization measures real layout (offsetWidth,
// clientWidth, ...) to decide which cells are worth mounting, and jsdom's layout engine
// always reports zero - so the real DataGrid renders only a handful of columns here,
// never the "Dein Gebot" one this suite exists to test. A thin stand-in that just maps
// every row through every column's own renderCell/valueFormatter sidesteps that
// limitation entirely while still exercising the real column definitions in MarketTable.js
// (including the exact props BidCell is wired up with). Only the grid package is faked -
// MUI's Dialog/Snackbar/Alert below are the real components.
jest.mock("@mui/x-data-grid", () => ({
    __esModule: true,
    DataGrid: ({ rows, columns }) => (
        <div>
            {rows.map((row) => (
                <div key={row.id}>
                    {columns.map((column) => (
                        <div key={column.field}>
                            {column.renderCell
                                ? column.renderCell({ row, value: row[column.field], id: row.id })
                                : column.valueFormatter
                                    ? column.valueFormatter({ value: row[column.field] })
                                    : row[column.field]}
                        </div>
                    ))}
                </div>
            ))}
        </div>
    ),
    // PagedDataGrid imports these for its own Pagination component, which this stand-in
    // never renders - present only so that import does not fail
    useGridApiContext: () => ({ current: {} }),
    useGridSelector: () => undefined,
    gridPageSelector: () => 0,
    gridPageSizeSelector: () => 0,
    gridVisibleTopLevelRowCountSelector: () => 0
}))

// MarketTable imports its data at module scope, so the fixture is mocked rather than
// pointed at the real 92-row market.json - this keeps every row's numbers deliberately
// distinct and lets a fetch that never resolves be held open on purpose, neither of which
// is practical against the real file.
//
// Both mocks are fully self-contained (no reference to anything outside the factory):
// jest.mock calls are hoisted above the imports below, so a variable declared later in
// this file would not exist yet when the factory actually runs.
jest.mock("../data/market.json", () => {
    const row = (overrides) => ({
        playerId: "1",
        teamId: "5",
        position: "ABW",
        firstName: "Test",
        lastName: "Player",
        status: 0,
        statusText: null,
        marketValue: 200000,
        price: 250000,
        ownBid: null,
        isOwnListing: false,
        seller: "Rivale",
        isFreeAgent: false,
        // A real future expiry and listing date, plus a non-null offer count: these three
        // (main's freshness columns) would otherwise render their own "–" too, colliding
        // with the one this suite cares about (the "Dein Gebot" column after a
        // withdrawal) and breaking findByText("–")'s uniqueness assumption below.
        expiration: "2999-01-01T12:00:00+00:00",
        listedSince: "2026-08-01T09:00:00+00:00",
        offerCount: 0,
        avgDailyGrowth: 500,
        // Non-null by default so the delta columns never render their own "–", for the
        // same reason as the three fields above
        today: 1000,
        yesterday: 1000,
        twoDays: 1000,
        sevenDaysAvg: 1000,
        thirtyDaysAvg: 1000,
        ...overrides
    })

    return [
        // suggestedBid = round(211000 + 3 * 500) = 212500
        row({ playerId: "303", lastName: "Thrown", marketValue: 211000, price: 260000 }),
        // suggestedBid = 223500
        row({ playerId: "404", lastName: "JsonError", marketValue: 222000, price: 270000 }),
        // suggestedBid = 234500
        row({ playerId: "505", lastName: "PlainText", marketValue: 233000, price: 280000 }),
        // Falling market value (negative growth) - no suggestion, so a withdrawal leaves
        // both ownBid and suggestedBid null and the cell shows a dash
        row({
            playerId: "606", lastName: "Withdraw", marketValue: 340000, price: 340000,
            avgDailyGrowth: -50, ownBid: 480000
        }),
        // An existing bid more than double its own suggestion (104000), so resubmitting it
        // unchanged asks for confirmation - the one gap where a second row's cell can still
        // be opened before the request for this row is actually sent
        row({
            playerId: "701", lastName: "RaceA", marketValue: 101000, price: 151000,
            avgDailyGrowth: 1000, ownBid: 305000
        }),
        // suggestedBid = round(502000 + 3 * 2000) = 508000
        row({ playerId: "702", lastName: "RaceB", marketValue: 502000, price: 602000, avgDailyGrowth: 2000 })
    ]
})

jest.mock("../data/config.json", () => ({ bepGrowthDays: 3, bepTargetDays: 3 }))

const apiUnreachable = "Die Flask-API ist nicht erreichbar. Läuft app.py?"

describe("MarketTable bidding", () => {
    it("keeps the typed draft and reports the API as unreachable when fetch throws outright", async () => {
        global.fetch = jest.fn().mockRejectedValue(new Error("network down"))
        render(<MarketTable />)

        await userEvent.click(screen.getByText("212.500 €"))
        const input = screen.getByRole("textbox")
        await userEvent.clear(input)
        await userEvent.type(input, "150000")

        await userEvent.click(screen.getByLabelText("Gebot abgeben"))

        expect(await screen.findByText(apiUnreachable)).toBeInTheDocument()
        // closeEdit() never ran - the draft the user typed is still there, not thrown away
        expect(screen.getByRole("textbox")).toHaveValue("150.000")
    })

    it("shows the server's German error verbatim and keeps the draft on a non-OK JSON response", async () => {
        global.fetch = jest.fn().mockResolvedValue({
            ok: false,
            status: 400,
            json: async () => ({ error: "Das Gebot liegt unter dem Marktwert." })
        })
        render(<MarketTable />)

        await userEvent.click(screen.getByText("223.500 €"))
        const input = screen.getByRole("textbox")
        await userEvent.clear(input)
        await userEvent.type(input, "160000")

        await userEvent.click(screen.getByLabelText("Gebot abgeben"))

        expect(await screen.findByText("Das Gebot liegt unter dem Marktwert.")).toBeInTheDocument()
        expect(screen.getByRole("textbox")).toHaveValue("160.000")
    })

    it("reports the API as unreachable rather than a raw HTTP status when a non-OK response carries no JSON error", async () => {
        // What the dev-server's proxy actually answers when Flask isn't running: a
        // text/plain 500, so response.json() rejects and body.error is never set - the
        // regression case for "Kickbase antwortete mit HTTP 500." showing up instead
        global.fetch = jest.fn().mockResolvedValue({
            ok: false,
            status: 500,
            json: async () => { throw new Error("Unexpected token in JSON") }
        })
        render(<MarketTable />)

        await userEvent.click(screen.getByText("234.500 €"))
        const input = screen.getByRole("textbox")
        await userEvent.clear(input)
        await userEvent.type(input, "170000")

        await userEvent.click(screen.getByLabelText("Gebot abgeben"))

        expect(await screen.findByText(apiUnreachable)).toBeInTheDocument()
        expect(screen.queryByText(/Kickbase antwortete mit HTTP/)).not.toBeInTheDocument()
        expect(screen.getByRole("textbox")).toHaveValue("170.000")
    })

    it("shows no bid rather than the stale value after a withdrawal", async () => {
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            status: 200,
            json: async () => ({ ownBid: null })
        })
        render(<MarketTable />)

        await userEvent.click(screen.getByText("480.000 €"))
        await userEvent.click(screen.getByLabelText("Gebot zurückziehen"))

        // The override map distinguishes "withdrawn, so null" from "not touched yet" by
        // key presence (`playerId in bids`) rather than truthiness - a truthiness check
        // would let the old 480.000 € linger since `null` is falsy either way.
        //
        // Scoped to the "Dein Gebot" cell's own tooltip rather than a page-wide
        // findByText("–"): main's auction solver added a "Zwangsverkauf droht" column
        // that also renders "–" for every row with no seller in distress - which, against
        // the real balances.json this suite does not mock, is every row here. A bare
        // findByText("–") would then match more than one element and fail regardless of
        // whether the withdrawal itself worked.
        const cell = await screen.findByTitle(/^Kein Vorschlag/)
        expect(cell).toHaveTextContent("–")
        expect(screen.queryByText("480.000 €")).not.toBeInTheDocument()
    })

    it("does not let a late response for one row discard another row's in-progress draft", async () => {
        const resolvers = {}
        global.fetch = jest.fn((url) => new Promise((resolve) => {
            const [, playerId] = url.match(/\/market\/(\w+)\/bid/)
            resolvers[playerId] = resolve
        }))

        render(<MarketTable />)

        // Row A already carries a bid well past double its own suggestion, so resubmitting
        // it unchanged goes through the confirmation dialog rather than straight to fetch
        await userEvent.click(screen.getByText("305.000 €"))
        await userEvent.click(screen.getByLabelText("Gebot abgeben"))
        expect(await screen.findByText("Gebot bestätigen")).toBeInTheDocument()

        // No request for row A has been sent yet - pendingId is still null, so opening a
        // different row's cell is not blocked. This is the gap the fix has to survive.
        // The dialog being open marks the rest of the page aria-hidden (MUI's modal
        // manager) and focus-traps into itself, so role queries need { hidden: true } and
        // the draft is changed via fireEvent rather than userEvent.type (which focuses the
        // element first, and MUI would immediately steal that focus back to the dialog) -
        // both accessibility-tree/focus-trap details of the test, not of the fix itself.
        await userEvent.click(screen.getByText("508.000 €"))
        const draftInput = screen.getByRole("textbox", { hidden: true })
        fireEvent.change(draftInput, { target: { value: "123456" } })
        expect(draftInput).toHaveValue("123.456")

        // Confirming row A is what actually sends its request and sets pendingId
        const dialog = screen.getByRole("dialog")
        await userEvent.click(within(dialog).getByText("Gebot abgeben"))

        // Row B's draft must still be there while row A's request is in flight
        expect(screen.getByRole("textbox", { hidden: true })).toHaveValue("123.456")

        // Row A's response arrives late, after row B has already been edited. Resolving
        // and then immediately asserting would let waitFor's first (synchronous) check
        // pass on the DOM exactly as it stood before the resolution - true by coincidence,
        // not because the update was actually observed. A macrotask flush inside act()
        // forces send()'s whole await chain (fetch -> response.json() -> the state
        // updates) to finish first, so the assertion below is checking the settled state.
        await act(async () => {
            resolvers["701"]({ ok: true, status: 200, json: async () => ({ ownBid: 305000 }) })
            await new Promise((resolve) => setTimeout(resolve, 0))
        })

        // The scoped update (`current?.playerId === playerId ? null : current`) leaves row
        // B's edit alone; the old unconditional closeEdit() would have wiped it out here
        expect(screen.getByRole("textbox", { hidden: true })).toHaveValue("123.456")
        // Row A itself is back to its resting display, not stuck mid-edit or pending
        expect(screen.getByText("305.000 €")).toBeInTheDocument()
    })
})
