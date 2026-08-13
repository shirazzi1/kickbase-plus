import { render, screen } from "@testing-library/react"

// The same table as in MarketTable.test.js, but with manager profiles on disk - which is a
// separate file rather than a second describe block, because swapping the profiles module out
// mid-file means re-requiring the table and with it a second copy of React.

// Three listings from three different clubs: one Kickbase listing, one from a rival, one of
// the user's own. Team ids are what the chip matches on.
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
        playerId: "755", teamId: "8", position: "STU", firstName: "Thomas",
        lastName: "Müller", status: 0, statusText: null,
        marketValue: 5000000, price: 5000000, ownBid: null,
        seller: "shirazzi", sellerId: "2", isFreeAgent: false,
        expiration: null,
        listedSince: new Date(Date.now() - 30 * 60 * 60 * 1000).toISOString(), offerCount: 0,
        today: 1000, yesterday: 1000, twoDays: 1000, sevenDaysAvg: null, thirtyDaysAvg: null
    }
]))

// Anna is the only manager who can pay 10.000.000: the user is not their own rival and the
// seller of the Ginter listing has nothing.
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

// Anna buys from two clubs and from nowhere else, and she is no momentum buyer - so of the
// three listings exactly the two from her clubs fit her. The loader itself is what gets
// replaced: it reads the file through require.context, which Jest has no webpack for. Every
// pure function around it stays real, so this runs the same heuristic the app runs.
jest.mock("./managerProfiles", () => ({
    ...jest.requireActual("./managerProfiles"),
    loadManagerProfiles: () => ({
        marketValueCoverage: { players: 20, of: 20 },
        managers: {
            1: {
                managerId: "1",
                managerName: "Anna",
                holdDuration: { medianDays: 2, medianSeconds: 172800, n: 6, roundTripsWithinAnHour: 0 },
                purchaseMarkup: { meanPercent: 4.2, medianPercent: 3.1, n: 6, buysConsidered: 6 },
                momentumBuys: { share: 0.2, risingBuys: 1, n: 6, windowDays: 7 },
                topClubs: {
                    clubs: [
                        { teamId: "5", teamName: "Borussia Dortmund", buys: 6 },
                        { teamId: "8", teamName: "FC Bayern", buys: 3 }
                    ],
                    n: 9
                },
                activityWindow: { hourCounts: Array(24).fill(0), peakHour: null, n: 0, timezone: "Europe/Berlin" }
            }
        }
    })
}))

const MarketTable = require("./MarketTable").default

// jsdom reports every element as 0x0, and the DataGrid then virtualises away every row and
// every column but the first
beforeAll(() => {
    for (const [property, value] of Object.entries({
        clientWidth: 4000, clientHeight: 1200, offsetWidth: 4000, offsetHeight: 1200
    })) {
        Object.defineProperty(HTMLElement.prototype, property, {
            configurable: true, value
        })
    }
})

const rowOf = (name) => screen.getByText(name).closest("[role='row']")

describe("MarketTable, the likely bidder chip", () => {
    beforeEach(() => render(<MarketTable />))

    it("names the affordable managers whose buying pattern fits the player", () => {
        expect(screen.getByText("Wahrscheinliche Mitbieter")).toBeTruthy()

        // Ginter plays for team 5, where six of Anna's purchases come from - and she can pay
        // the asking price, which is the other half of the claim
        expect(rowOf("Matthias Ginter").textContent).toContain("Anna")
        expect(screen.getAllByLabelText(/^Passt zum Beuteschema von: Anna \(6 Käufe bei Borussia Dortmund\)/).length)
            .toBe(1)
    })

    it("says nothing about a player no pattern covers", () => {
        // Gouweleeuw plays for team 13. Anna could pay for him and may well bid - but nothing
        // she has done says she buys players like this one, and a chip there would be the
        // confident nonsense this feature exists to avoid.
        expect(rowOf("Jeffrey Gouweleeuw").textContent).not.toContain("Anna")
        expect(screen.queryAllByLabelText(/Beuteschema.*FC Augsburg/).length).toBe(0)
    })

    it("reads the user's own listing as who might buy the player off them", () => {
        // Müller plays for team 8, which Anna buys from as well. On an own listing the same
        // set is not a set of co-bidders, so the wording changes with it.
        expect(rowOf("Thomas Müller").textContent).toContain("Anna")
        expect(screen.getAllByLabelText(/^Passt zum Beuteschema möglicher Käufer: Anna \(3 Käufe bei FC Bayern\)/).length)
            .toBe(1)
    })

    it("does not let the chip disagree with the affordability columns", () => {
        // Pleite has nothing to bid with, so however well a player fits their pattern they
        // never show up here
        expect(screen.queryAllByLabelText(/Beuteschema.*Pleite/).length).toBe(0)
    })
})
