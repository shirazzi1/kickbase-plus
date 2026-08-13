import {
    CLUB_PATTERN_MIN_BUYS,
    MOMENTUM_PATTERN_MIN_N,
    bidderChipLabel,
    bidderReasons,
    coverageNote,
    isRising,
    likelyBidders,
    managerProfileList
} from "./managerProfiles"

// One manager who buys from a single club again and again, one who buys whatever is going up,
// and one who has done neither often enough to call it a pattern
const profile = (overrides) => ({
    managerId: "1",
    managerName: "Anna",
    holdDuration: { medianDays: 2, medianSeconds: 172800, n: 6, roundTripsWithinAnHour: 0 },
    purchaseMarkup: { meanPercent: 4.2, medianPercent: 3.1, n: 8, buysConsidered: 10 },
    momentumBuys: { share: 0.2, risingBuys: 2, n: 10, windowDays: 7 },
    topClubs: { clubs: [{ teamId: "5", teamName: "Borussia Dortmund", buys: 4 }], n: 9 },
    activityWindow: { hourCounts: Array(24).fill(0), peakHour: 0, n: 0, timezone: "Europe/Berlin" },
    ...overrides
})

const dortmundListing = { playerId: "49", teamId: "5", price: 10000000, sevenDaysAvg: null, today: null }

// loadManagerProfiles() and its test are gone with the require.context they existed for: the
// consumers fetch manager_profiles.json now, and a file the scrape has not written is a 404
// the hook turns into an empty document. What that looks like on the page is asserted in
// ManagerDossier.test.js and MarketTable.test.js.

describe("managerProfileList", () => {
    it("sorts the managers by name", () => {
        const profiles = {
            managers: {
                2: profile({ managerId: "2", managerName: "Zoe" }),
                1: profile({ managerId: "1", managerName: "Anna" })
            }
        }

        expect(managerProfileList(profiles).map((entry) => entry.managerName)).toEqual(["Anna", "Zoe"])
    })

    it("has nothing to list without a document", () => {
        // The backend writes an entry per manager even for one who has never traded, so an
        // empty list is a missing file - never a quiet league
        expect(managerProfileList(null)).toEqual([])
        expect(managerProfileList({})).toEqual([])
        expect(managerProfileList({ managers: {} })).toEqual([])
    })
})

describe("coverageNote", () => {
    it("warns when the market value stage delivered nothing", () => {
        const note = coverageNote({ marketValueCoverage: { players: 0, of: 178 }, managers: {} })

        expect(note.severity).toBe("warning")
        // n = 0 everywhere otherwise reads as a league that never buys anything
        expect(note.text).toContain("Marktwert-Vorstufe lieferte diesmal nichts")
        expect(note.text).toContain("178")
    })

    it("states partial coverage without calling it a failure", () => {
        const note = coverageNote({ marketValueCoverage: { players: 120, of: 178 }, managers: {} })

        expect(note.severity).toBe("info")
        expect(note.text).toContain("120 von 178")
    })

    it("says nothing when nobody has bought anything yet", () => {
        // No purchase means no curve was ever needed, so there is nothing missing
        expect(coverageNote({ marketValueCoverage: { players: 0, of: 0 } })).toBeNull()
        expect(coverageNote({})).toBeNull()
        expect(coverageNote(null)).toBeNull()
    })
})

describe("isRising", () => {
    it("prefers the seven day window the momentum metric itself uses", () => {
        expect(isRising({ sevenDaysAvg: 5000, today: -1 })).toBe(true)
        expect(isRising({ sevenDaysAvg: -5000, today: 1 })).toBe(false)
    })

    it("falls back to today where the history is too short", () => {
        expect(isRising({ sevenDaysAvg: null, today: 5000 })).toBe(true)
        expect(isRising({ sevenDaysAvg: null, today: -5000 })).toBe(false)
    })

    it("has no answer for a row without a trend", () => {
        // Which is the normal case right after a scrape that could not fetch the curves
        expect(isRising({ sevenDaysAvg: null, today: null })).toBeNull()
        expect(isRising(null)).toBeNull()
    })
})

describe("bidderReasons", () => {
    it("names the club a manager keeps buying from", () => {
        const reasons = bidderReasons(profile(), dortmundListing)

        expect(reasons.map((reason) => reason.kind)).toEqual(["club"])
        expect(reasons[0].text).toBe("4 Käufe bei Borussia Dortmund")
    })

    it("does not call the league's noise floor a pattern", () => {
        // One buy below the threshold is where most club entries in a real league sit - see
        // CLUB_PATTERN_MIN_BUYS for the distribution it was set from
        const once = profile({
            topClubs: { clubs: [{ teamId: "5", teamName: "Borussia Dortmund", buys: CLUB_PATTERN_MIN_BUYS - 1 }], n: 1 }
        })

        expect(bidderReasons(once, dortmundListing)).toEqual([])
    })

    it("matches a club whichever type the ids arrive as", () => {
        // Team ids are strings in market.json and can be numbers in the feed
        const numeric = profile({ topClubs: { clubs: [{ teamId: 5, teamName: "Borussia Dortmund", buys: 4 }], n: 9 } })

        expect(bidderReasons(numeric, { ...dortmundListing, teamId: "5" })).toHaveLength(1)
    })

    it("names a momentum buyer only when this market value is rising", () => {
        const chaser = profile({
            topClubs: { clubs: [], n: 0 },
            momentumBuys: { share: 0.8, risingBuys: 8, n: 10, windowDays: 7 }
        })

        const rising = bidderReasons(chaser, { ...dortmundListing, teamId: "13", sevenDaysAvg: 4000 })
        expect(rising.map((reason) => reason.kind)).toEqual(["momentum"])
        expect(rising[0].text).toContain("80 %")

        // The same manager, a player nobody is bidding the value up on
        expect(bidderReasons(chaser, { ...dortmundListing, teamId: "13", sevenDaysAvg: -4000 })).toEqual([])
        // And a player whose trend the data does not say anything about
        expect(bidderReasons(chaser, { ...dortmundListing, teamId: "13" })).toEqual([])
    })

    it("does not read a pattern out of a handful of buys", () => {
        const thin = profile({
            topClubs: { clubs: [], n: 0 },
            momentumBuys: { share: 1, risingBuys: MOMENTUM_PATTERN_MIN_N - 1, n: MOMENTUM_PATTERN_MIN_N - 1, windowDays: 7 }
        })

        expect(bidderReasons(thin, { ...dortmundListing, teamId: "13", sevenDaysAvg: 4000 })).toEqual([])
    })

    it("can name both reasons at once", () => {
        const both = profile({ momentumBuys: { share: 0.9, risingBuys: 9, n: 10, windowDays: 7 } })

        expect(bidderReasons(both, { ...dortmundListing, sevenDaysAvg: 4000 }).map((reason) => reason.kind))
            .toEqual(["club", "momentum"])
    })

    it("says nothing about a manager without a profile", () => {
        expect(bidderReasons(null, dortmundListing)).toEqual([])
        expect(bidderReasons(profile(), null)).toEqual([])
    })
})

describe("likelyBidders", () => {
    const rivals = [
        { userId: "1", username: "Anna", maxBid: 30000000 },
        { userId: "2", username: "Momo", maxBid: 20000000 }
    ]

    const profiles = {
        managers: {
            1: profile(),
            2: profile({
                managerId: "2",
                managerName: "Momo",
                topClubs: { clubs: [{ teamId: "13", teamName: "FC Augsburg", buys: 5 }], n: 7 }
            })
        }
    }

    it("keeps only the affordable managers whose pattern fits", () => {
        const bidders = likelyBidders(dortmundListing, profiles, { rivals })

        // Momo can pay the price too, but has never bought from this club
        expect(bidders.map((bidder) => bidder.username)).toEqual(["Anna"])
        expect(bidders[0].reasons[0].kind).toBe("club")
    })

    it("matches every affordable manager against this listing's club", () => {
        const augsburg = { ...dortmundListing, teamId: "13" }

        expect(likelyBidders(augsburg, profiles, { rivals }).map((bidder) => bidder.username)).toEqual(["Momo"])
    })

    it("derives the affordable set itself when it is not handed one", () => {
        // The same function the solver columns use, so the chip cannot disagree with them
        const balances = [
            { userId: "1", username: "Anna", maxBid: 30000000, maxBidWithBonuses: 30000000 },
            { userId: "9", username: "Arm", maxBid: 100, maxBidWithBonuses: 100 }
        ]

        const bidders = likelyBidders(dortmundListing, profiles, { balances, ownManagerId: "7" })

        expect(bidders.map((bidder) => bidder.username)).toEqual(["Anna"])
    })

    it("has nothing to say without profiles", () => {
        // Which is what leaves the column out of the table entirely
        expect(likelyBidders(dortmundListing, null, { rivals })).toEqual([])
        expect(likelyBidders(dortmundListing, { managers: {} }, { rivals })).toEqual([])
    })

    it("has nothing to say when nobody can afford the player", () => {
        expect(likelyBidders(dortmundListing, profiles, { rivals: [] })).toEqual([])
    })
})

describe("bidderChipLabel", () => {
    const named = (count) => Array.from({ length: count }, (unused, index) => ({ username: `M${index}` }))

    it("names up to two managers", () => {
        expect(bidderChipLabel(named(1))).toBe("M0")
        expect(bidderChipLabel(named(2))).toBe("M0, M1")
    })

    it("counts the rest instead of cutting the list off silently", () => {
        expect(bidderChipLabel(named(4))).toBe("M0, M1 +2")
    })

    it("has no label without bidders", () => {
        expect(bidderChipLabel([])).toBeNull()
        expect(bidderChipLabel(null)).toBeNull()
    })
})
