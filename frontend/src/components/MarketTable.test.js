import { render, screen } from "@testing-library/react"

// Two rows that differ in exactly the way the market does: a Kickbase listing with a real
// expiry, and a user listing that only has a listing date.
jest.mock("../data/market.json", () => ([
    {
        playerId: "1811", teamId: "13", position: "ABW", firstName: "Jeffrey",
        lastName: "Gouweleeuw", status: 0, statusText: null,
        marketValue: 10000000, price: 10000000, ownBid: null,
        seller: "Kickbase", isFreeAgent: true,
        expiration: "2999-01-01T12:00:00+00:00",
        listedSince: "2026-08-13T09:00:00+00:00", offerCount: 0,
        today: 1000, yesterday: 1000, twoDays: 1000, sevenDaysAvg: null, thirtyDaysAvg: null
    },
    {
        playerId: "49", teamId: "5", position: "ABW", firstName: "Matthias",
        lastName: "Ginter", status: 0, statusText: null,
        marketValue: 26260331, price: 32000000, ownBid: null,
        seller: "shirazzi", isFreeAgent: false,
        expiration: null,
        listedSince: "2026-08-12T09:00:00+00:00", offerCount: 3,
        today: 1000, yesterday: 1000, twoDays: 1000, sevenDaysAvg: null, thirtyDaysAvg: null
    }
]))

const MarketTable = require("./MarketTable").default

// jsdom reports every element as 0x0, and the DataGrid then virtualises away every row and
// every column but the first. Giving the layout a size is what makes the grid render at
// all; without it the test can only ever assert that nothing threw.
beforeAll(() => {
    for (const [property, value] of Object.entries({
        clientWidth: 2400, clientHeight: 1200, offsetWidth: 2400, offsetHeight: 1200
    })) {
        Object.defineProperty(HTMLElement.prototype, property, {
            configurable: true, value
        })
    }
})

describe("MarketTable", () => {
    it("renders a row per listing", () => {
        render(<MarketTable />)

        expect(screen.getByText("Jeffrey Gouweleeuw")).toBeTruthy()
        expect(screen.getByText("Matthias Ginter")).toBeTruthy()
    })

    it("keys its rows by player id rather than by array position", () => {
        // A duplicate or missing id makes the grid drop rows and log about it
        const errors = []
        const original = console.error
        console.error = (...args) => errors.push(String(args[0]))

        try {
            render(<MarketTable />)
        } finally {
            console.error = original
        }

        expect(errors.filter((message) => /row.*id|id.*row/i.test(message))).toEqual([])

        const rows = screen.getAllByRole("row")
        // The header row plus one row per listing
        expect(rows.length).toBe(3)
    })

    it("shows a listing age for both listing sources", () => {
        // The user listing has no expiry, so its age is the only time signal it carries
        render(<MarketTable />)

        expect(screen.getByText("Gelistet seit")).toBeTruthy()
        expect(screen.getAllByText(/Std\.|Tag/).length).toBeGreaterThanOrEqual(2)
    })

    it("leaves the countdown empty where there is no real expiry", () => {
        render(<MarketTable />)

        expect(screen.getByText("Restzeit")).toBeTruthy()
        // Guessing a deadline for a user listing would put a wrong number in this column
        expect(screen.getAllByText("–").length).toBeGreaterThanOrEqual(1)
    })
})
