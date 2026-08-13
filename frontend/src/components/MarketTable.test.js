import { render, screen } from "@testing-library/react"

// Two rows that differ in exactly the way the market does: a Kickbase listing with a real
// expiry, and a user listing that only has a listing date.
//
// The listing dates are written relative to the clock on purpose. Fixed ones would age with
// the calendar, and the forced sale score is built on the listing age - so a date in the
// fixture would quietly change what the distress column claims from one day to the next.
jest.mock("../data/market.json", () => ([
    {
        playerId: "1811", teamId: "13", position: "ABW", firstName: "Jeffrey",
        lastName: "Gouweleeuw", status: 0, statusText: null,
        marketValue: 10000000, price: 10000000, ownBid: null,
        seller: "Kickbase", sellerId: null, isFreeAgent: true,
        expiration: "2999-01-01T12:00:00+00:00",
        listedSince: new Date(Date.now() - 12 * 60 * 60 * 1000).toISOString(), offerCount: 0,
        today: 1000, yesterday: 1000, twoDays: 1000, sevenDaysAvg: null, thirtyDaysAvg: null
    },
    {
        playerId: "49", teamId: "5", position: "ABW", firstName: "Matthias",
        lastName: "Ginter", status: 0, statusText: null,
        marketValue: 26260331, price: 10000000, ownBid: null,
        seller: "Pleite", sellerId: "3", isFreeAgent: false,
        expiration: null,
        listedSince: new Date(Date.now() - 30 * 60 * 60 * 1000).toISOString(), offerCount: 0,
        today: 1000, yesterday: 1000, twoDays: 1000, sevenDaysAvg: null, thirtyDaysAvg: null
    },
    {
        // Listed by the user. Nobody can bid on their own player, so this row must not carry
        // a minimum bid at all - let alone one marked as too expensive for its own owner.
        playerId: "755", teamId: "8", position: "STU", firstName: "Thomas",
        lastName: "Müller", status: 0, statusText: null,
        marketValue: 5000000, price: 5000000, ownBid: null,
        seller: "shirazzi", sellerId: "2", isFreeAgent: false,
        expiration: null,
        listedSince: new Date(Date.now() - 30 * 60 * 60 * 1000).toISOString(), offerCount: 0,
        today: 1000, yesterday: 1000, twoDays: 1000, sevenDaysAvg: null, thirtyDaysAvg: null
    }
]))

// MarketTable.js also imports config.json at module scope now (the break-even horizons) -
// mocked for the same reason market.json above is: this suite runs against fixed fixtures,
// not against whatever main.py happened to write into a checkout most recently.
jest.mock("../data/config.json", () => ({ bepGrowthDays: 3, bepTargetDays: 3 }))

// Three managers: one rich rival, the user, and a seller deep in the red. Both listings are
// priced at 10.000.000, so Anna can pay and the seller's own money is irrelevant.
jest.mock("../data/balances.json", () => ([
    {
        userId: "1", username: "Anna", profilePic: null, teamValue: 100000000,
        balance: 5000000, maxBid: 30000000,
        balanceWithBonuses: 5000000, maxBidWithBonuses: 30000000
    },
    {
        userId: "2", username: "shirazzi", profilePic: null, teamValue: 100000000,
        balance: 2000000, maxBid: 20000000,
        balanceWithBonuses: 2000000, maxBidWithBonuses: 20000000, isSelf: true
    },
    {
        userId: "3", username: "Pleite", profilePic: null, teamValue: 100000000,
        balance: -6000000, maxBid: 0,
        balanceWithBonuses: -6000000, maxBidWithBonuses: 0
    }
]))

const MarketTable = require("./MarketTable").default

// jsdom reports every element as 0x0, and the DataGrid then virtualises away every row and
// every column but the first. Giving the layout a size is what makes the grid render at
// all; without it the test can only ever assert that nothing threw.
beforeAll(() => {
    for (const [property, value] of Object.entries({
        clientWidth: 4000, clientHeight: 1200, offsetWidth: 4000, offsetHeight: 1200
    })) {
        Object.defineProperty(HTMLElement.prototype, property, {
            configurable: true, value
        })
    }
})

// The grid row a player sits in, so an assertion cannot be satisfied by the same text
// somewhere else on the page
const rowOf = (name) => screen.getByText(name).closest("[role='row']")

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
        expect(rows.length).toBe(4)
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

    it("names the managers who could pay the asking price", () => {
        render(<MarketTable />)

        expect(screen.getByText("Verdeckte Bieter")).toBeTruthy()
        // Anna can pay 10.000.000 on either foreign listing. The user is not their own
        // rival, and the seller of the Ginter listing has nothing anyway. Kickbase never
        // says who is bidding, so the count comes with who it could be.
        // Anchored, so the same name inside the Mindestgebot tooltip does not count
        expect(screen.getAllByLabelText(/^Anna \(max\. 30\.000\.000/).length).toBe(2)
    })

    it("shows a minimum winning bid capped at the user's own budget", () => {
        render(<MarketTable />)

        expect(screen.getByText("Mindestgebot")).toBeTruthy()
        // Beating Anna's 30.000.000 is out of reach, so what is offered is the user's own
        // ceiling rather than a bid they could never place
        // Matched loosely: the currency formatter separates the amount from the sign with
        // a non breaking space
        expect(rowOf("Matthias Ginter").textContent).toMatch(/20\.000\.000/)
        expect(screen.getAllByLabelText(/Nötig wären 30\.000\.001/).length).toBe(2)
        // Marked as the own limit rather than coloured like an error: on the real market
        // nine rows in ten sit above the own ceiling, and a red column there would read as
        // "you can buy nothing"
        expect(rowOf("Matthias Ginter").textContent).toContain("dein Max.")
    })

    it("offers no bid on the user's own listing", () => {
        render(<MarketTable />)

        // Bidding on your own player is impossible, so a number here would be nonsense -
        // and before the guard it was the richest manager's whole budget, in red
        const own = rowOf("Thomas Müller").textContent
        expect(own).toContain("eigene Listung")
        expect(own).not.toMatch(/20\.000\.000/)
    })

    it("names the possible buyers of the user's own listing", () => {
        render(<MarketTable />)

        // The same set, read the other way round: who could take the player off you. Anna
        // can pay the 5.000.000, the broke seller of the other listing cannot.
        expect(screen.getAllByLabelText(/^Mögliche Käufer: Anna \(max\. 30\.000\.000/).length).toBe(1)
        expect(screen.queryAllByLabelText(/^Mögliche Käufer:.*Pleite/).length).toBe(0)
    })

    it("flags a listing from a seller who is deep in the red", () => {
        render(<MarketTable />)

        // 6.000.000 in the red with nothing left to bid, up for over a day, no bids
        expect(rowOf("Matthias Ginter").textContent).toContain("Zwangsverkauf droht")
        // A Kickbase listing has no seller under pressure behind it, and neither has one
        // of your own - however deep in the red you are, you are not lowballing yourself
        expect(rowOf("Jeffrey Gouweleeuw").textContent).not.toContain("Zwangsverkauf")
        expect(rowOf("Thomas Müller").textContent).not.toContain("Zwangsverkauf")
    })

    it("lists every manager's stack above the table", () => {
        render(<MarketTable />)

        // Collapsed, so only the summary shows - and it places the user in the field
        expect(screen.getByText(/Bieter-Übersicht/)).toBeTruthy()
        expect(screen.getByText(/Platz 2 von 3/)).toBeTruthy()
        // isSelf is in the fixture, so the transition warning must stay away
        expect(screen.queryByText(/kein Manager als/)).toBeNull()
    })
})
