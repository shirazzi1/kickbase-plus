import {
    relativeChange,
    daysToBreakEven,
    formatDuration,
    elapsedSince,
    maxBidOf,
    ownManager,
    affordableRivals,
    minWinningBid,
    forcedSaleRisk,
    managerStacks,
    DISTRESS_ALERT_SCORE,
    DISTRESS_WATCH_SCORE
} from "./marketFormulas"

const MINUTE = 60 * 1000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

describe("relativeChange", () => {
    it("expresses a move as a share of the market value", () => {
        expect(relativeChange(100000, 1000000)).toBeCloseTo(0.1)
        expect(relativeChange(-50000, 1000000)).toBeCloseTo(-0.05)
    })

    it("keeps a missing move missing instead of calling it zero", () => {
        expect(relativeChange(null, 1000000)).toBeNull()
        expect(relativeChange(undefined, 1000000)).toBeNull()
    })

    it("has no answer without a market value to divide by", () => {
        expect(relativeChange(100000, 0)).toBeNull()
        expect(relativeChange(100000, null)).toBeNull()
    })
})

describe("daysToBreakEven", () => {
    const growing = { marketValue: 1000000, today: 100000, yesterday: 100000, twoDays: 100000 }

    it("divides the markup by the average daily growth", () => {
        expect(daysToBreakEven({ ...growing, price: 1300000 })).toBeCloseTo(3)
    })

    it("averages the three days rather than summing them", () => {
        // 60.000 € a day on average, so a 300.000 € markup takes five days
        const uneven = { marketValue: 1000000, today: 30000, yesterday: 60000, twoDays: 90000 }
        expect(daysToBreakEven({ ...uneven, price: 1300000 })).toBeCloseTo(5)
    })

    it("is zero when the price is already at or below the market value", () => {
        expect(daysToBreakEven({ ...growing, price: 1000000 })).toBe(0)
        expect(daysToBreakEven({ ...growing, price: 900000 })).toBe(0)
    })

    it("never breaks even on a flat or falling market value", () => {
        expect(daysToBreakEven({ ...growing, today: -10000, yesterday: -10000, twoDays: -10000, price: 1300000 })).toBeNull()
        expect(daysToBreakEven({ ...growing, today: 0, yesterday: 0, twoDays: 0, price: 1300000 })).toBeNull()
        // Averages to zero even though single days moved
        expect(daysToBreakEven({ ...growing, today: 50000, yesterday: -50000, twoDays: 0, price: 1300000 })).toBeNull()
    })

    it("needs all three days, since a short history is not a slow one", () => {
        expect(daysToBreakEven({ ...growing, twoDays: null, price: 1300000 })).toBeNull()
        expect(daysToBreakEven({ ...growing, today: undefined, price: 1300000 })).toBeNull()
    })

    it("has no answer without both a market value and a price", () => {
        expect(daysToBreakEven({ ...growing, marketValue: null, price: 1300000 })).toBeNull()
        expect(daysToBreakEven({ ...growing, price: null })).toBeNull()
    })
})

describe("formatDuration", () => {
    it("shows the two largest units that carry information", () => {
        expect(formatDuration(2 * DAY + 3 * HOUR + 40 * MINUTE)).toBe("2 Tage 3 Std.")
        expect(formatDuration(3 * HOUR + 40 * MINUTE)).toBe("3 Std. 40 Min.")
        expect(formatDuration(45 * MINUTE)).toBe("45 Min.")
    })

    it("drops a trailing zero unit instead of printing it", () => {
        expect(formatDuration(2 * DAY)).toBe("2 Tage")
        expect(formatDuration(3 * HOUR)).toBe("3 Std.")
    })

    it("keeps the singular for a single day", () => {
        expect(formatDuration(DAY + 2 * HOUR)).toBe("1 Tag 2 Std.")
    })

    it("clamps a span that already ran out", () => {
        // A countdown reading "-3 Std." looks like a listing running backwards
        expect(formatDuration(-3 * HOUR)).toBe("0 Min.")
    })

    it("has nothing to say about a missing span", () => {
        expect(formatDuration(null)).toBeNull()
        expect(formatDuration(undefined)).toBeNull()
        expect(formatDuration(NaN)).toBeNull()
    })
})

describe("elapsedSince", () => {
    const now = Date.parse("2026-08-13T12:00:00Z")

    it("measures the span back to the timestamp", () => {
        expect(elapsedSince("2026-08-13T09:00:00Z", now)).toBe(3 * HOUR)
    })

    it("reads a timestamp with an explicit offset as written", () => {
        expect(elapsedSince("2026-08-13T11:00:00+00:00", now)).toBe(HOUR)
    })

    it("has no answer for a missing or unreadable timestamp", () => {
        // Not every listing carries a date, and a NaN would sort ahead of every real value
        expect(elapsedSince(null, now)).toBeNull()
        expect(elapsedSince(undefined, now)).toBeNull()
        expect(elapsedSince("not a date", now)).toBeNull()
    })
})

// A league of five, the way balances.json holds them. "self" is the logged in user, and
// the ceilings are spread out far enough that every comparison below is unambiguous.
const manager = (userId, username, maxBid, extra = {}) => ({
    userId,
    username,
    teamValue: 100000000,
    balance: 1000000,
    maxBid,
    balanceWithBonuses: 1000000,
    maxBidWithBonuses: maxBid,
    ...extra
})

const BALANCES = [
    manager("1", "Anna", 30000000),
    manager("2", "Bernd", 20000000),
    manager("3", "Clara", 10000000),
    manager("4", "Verkäufer", 40000000),
    manager("5", "shirazzi", 25000000, { isSelf: true })
]

const OWN_ID = "5"

// Listed by manager 4, priced so that Anna and Bernd can afford it and Clara cannot
const LISTING = {
    playerId: "49",
    price: 15000000,
    seller: "Verkäufer",
    sellerId: "4",
    isFreeAgent: false,
    offerCount: 0,
    listedSince: "2026-08-13T00:00:00Z"
}

describe("maxBidOf", () => {
    it("picks the view the caller asked for", () => {
        const rich = manager("1", "Anna", 5000000, { maxBidWithBonuses: 7000000 })
        expect(maxBidOf(rich, false)).toBe(5000000)
        expect(maxBidOf(rich, true)).toBe(7000000)
    })

    it("defaults to the view that includes the bonuses", () => {
        // The narrower number is knowingly too low, so it is the worse default
        expect(maxBidOf(manager("1", "Anna", 5000000, { maxBidWithBonuses: 7000000 }))).toBe(7000000)
    })

    it("has no answer for a missing manager or a missing ceiling", () => {
        expect(maxBidOf(null)).toBeNull()
        expect(maxBidOf(manager("1", "Anna", 5000000, { maxBidWithBonuses: null }))).toBeNull()
    })
})

describe("ownManager", () => {
    it("finds the manager the backend flagged", () => {
        expect(ownManager(BALANCES).username).toBe("shirazzi")
    })

    it("is null when nothing marks one, rather than guessing", () => {
        // balances.json written before the flag existed carries no self at all
        expect(ownManager(BALANCES.map(({ isSelf, ...rest }) => rest))).toBeNull()
        expect(ownManager(null)).toBeNull()
    })
})

describe("affordableRivals", () => {
    it("keeps everyone whose ceiling reaches the asking price, richest first", () => {
        const rivals = affordableRivals(LISTING, BALANCES, OWN_ID)
        expect(rivals.map((rival) => rival.username)).toEqual(["Anna", "Bernd"])
        expect(rivals[0].maxBid).toBe(30000000)
    })

    it("excludes the seller, who does not bid against themselves", () => {
        // The seller has the highest ceiling in the league, so a leak would be obvious
        const rivals = affordableRivals(LISTING, BALANCES, OWN_ID)
        expect(rivals.map((rival) => rival.username)).not.toContain("Verkäufer")
    })

    it("excludes the seller by name when the listing predates the seller id", () => {
        const { sellerId, ...older } = LISTING
        const rivals = affordableRivals(older, BALANCES, OWN_ID)
        expect(rivals.map((rival) => rival.username)).toEqual(["Anna", "Bernd"])
    })

    it("excludes the user, who is not their own rival", () => {
        const rivals = affordableRivals(LISTING, BALANCES, OWN_ID)
        expect(rivals.map((rival) => rival.username)).not.toContain("shirazzi")
    })

    it("does not care whether the own id arrives as a string or a number", () => {
        expect(affordableRivals(LISTING, BALANCES, 5).map((r) => r.username)).toEqual(["Anna", "Bernd"])
    })

    it("counts a ceiling exactly at the asking price", () => {
        // Matching the price to the euro is all it takes to be in the auction
        const tied = [manager("1", "Anna", 15000000), BALANCES[4]]
        expect(affordableRivals(LISTING, tied, OWN_ID).map((r) => r.username)).toEqual(["Anna"])
    })

    it("drops a ceiling one euro short", () => {
        const short = [manager("1", "Anna", 14999999), BALANCES[4]]
        expect(affordableRivals(LISTING, short, OWN_ID)).toEqual([])
    })

    it("is empty when nobody can pay", () => {
        expect(affordableRivals({ ...LISTING, price: 90000000 }, BALANCES, OWN_ID)).toEqual([])
    })

    it("lets everyone but the user bid on a Kickbase listing, which has no seller", () => {
        const freeAgent = { ...LISTING, seller: "Kickbase", sellerId: null, isFreeAgent: true }
        expect(affordableRivals(freeAgent, BALANCES, OWN_ID).map((r) => r.username))
            .toEqual(["Verkäufer", "Anna", "Bernd"])
    })

    it("has nothing to say without a price or without balances", () => {
        expect(affordableRivals({ ...LISTING, price: null }, BALANCES, OWN_ID)).toEqual([])
        expect(affordableRivals(LISTING, null, OWN_ID)).toEqual([])
        expect(affordableRivals(null, BALANCES, OWN_ID)).toEqual([])
    })
})

describe("minWinningBid", () => {
    it("beats the richest affordable rival by a euro", () => {
        // Anna tops the affordable set at 30.000.000
        const solved = minWinningBid(LISTING, BALANCES, OWN_ID)
        expect(solved.required).toBe(30000001)
        expect(solved.isPhantom).toBe(false)
    })

    it("is the asking price when no rival can pay it", () => {
        // A phantom auction: the bid competes with nobody, so the floor is the whole answer
        const alone = [manager("1", "Anna", 1000000), BALANCES[4]]
        const solved = minWinningBid({ ...LISTING, price: 5000000 }, alone, OWN_ID)
        expect(solved.isPhantom).toBe(true)
        expect(solved.required).toBe(5000000)
        expect(solved.bid).toBe(5000000)
    })

    it("still calls a listing nobody at all can pay for a phantom auction", () => {
        // 31.000.000 is out of everyone's reach, the seller's 40.000.000 aside
        const solved = minWinningBid({ ...LISTING, price: 31000000 }, BALANCES, OWN_ID)
        expect(solved.isPhantom).toBe(true)
        expect(solved.required).toBe(31000000)
        // Including the user's, so the bid on offer is only what they could place
        expect(solved.exceedsBudget).toBe(true)
        expect(solved.bid).toBe(25000000)
    })

    it("never drops below the asking price", () => {
        // Kickbase rejects a bid under the asking price outright, so a lower "minimum
        // winning bid" would not be a bid at all
        const poor = [manager("1", "Anna", 15000000), BALANCES[4]]
        const solved = minWinningBid(LISTING, poor, OWN_ID)
        expect(solved.required).toBe(15000001)

        const cheap = minWinningBid({ ...LISTING, price: 5000000 }, poor, OWN_ID)
        expect(cheap.required).toBe(15000001)
    })

    it("agrees with the second highest ceiling among everyone who can pay", () => {
        // The game plan writes the bid as one euro over the runner up among all affordable
        // managers, the user included. That differs from beating the top rival only where
        // the user is not the richest of them - and there both formulas run past the user's
        // own ceiling, so the placeable bid comes out the same. Checked rather than argued.
        const secondHighestFormula = (listing, balances) => {
            const affordable = balances
                .filter((m) => m.userId !== listing.sellerId && m.maxBidWithBonuses >= listing.price)
                .map((m) => m.maxBidWithBonuses)
                .sort((a, b) => b - a)

            const required = affordable.length < 2 ? listing.price : Math.max(listing.price, affordable[1] + 1)
            const own = balances.find((m) => m.isSelf)?.maxBidWithBonuses ?? Infinity

            return Math.min(own, required)
        }

        // Once with the user outgunned, once with the user holding the biggest stack
        const richSelf = BALANCES.map((m) => m.isSelf ? { ...m, maxBid: 60000000, maxBidWithBonuses: 60000000 } : m)

        for (const balances of [BALANCES, richSelf]) {
            for (const price of [1000000, 15000000, 21000000, 26000000, 31000000, 45000000]) {
                const listing = { ...LISTING, price }
                expect(minWinningBid(listing, balances, OWN_ID).bid)
                    .toBe(secondHighestFormula(listing, balances))
            }
        }
    })

    it("caps the bid at the user's own ceiling and says that it did", () => {
        // Anna's 30.000.000 is out of reach for a user capped at 25.000.000
        const solved = minWinningBid(LISTING, BALANCES, OWN_ID)
        expect(solved.exceedsBudget).toBe(true)
        expect(solved.bid).toBe(25000000)
        expect(solved.ownMaxBid).toBe(25000000)
    })

    it("leaves the bid uncapped when the user's ceiling covers it", () => {
        const solved = minWinningBid({ ...LISTING, price: 5000000 }, [
            manager("1", "Anna", 8000000),
            manager("5", "shirazzi", 25000000, { isSelf: true })
        ], OWN_ID)
        expect(solved.exceedsBudget).toBe(false)
        expect(solved.bid).toBe(8000001)
    })

    it("caps nothing when no manager is flagged as the user", () => {
        // Without a known own ceiling, inventing one would either hide a bid the user can
        // place or offer one they cannot
        const anonymous = BALANCES.map(({ isSelf, ...rest }) => rest)
        const solved = minWinningBid(LISTING, anonymous, null)
        expect(solved.ownMaxBid).toBeNull()
        expect(solved.exceedsBudget).toBe(false)
        expect(solved.bid).toBe(solved.required)
    })

    it("has no answer without a price", () => {
        const solved = minWinningBid({ ...LISTING, price: null }, BALANCES, OWN_ID)
        expect(solved.bid).toBeNull()
        expect(solved.required).toBeNull()
    })

    it("hands the rival set back, so the table solves each listing once", () => {
        expect(minWinningBid(LISTING, BALANCES, OWN_ID).rivals.map((r) => r.username))
            .toEqual(["Anna", "Bernd"])
    })
})

describe("forcedSaleRisk", () => {
    const now = Date.parse("2026-08-13T12:00:00Z")

    // Out of room: 8.000.000 in the red with nothing left to bid, so the whole overdraft
    // the rules allow is used up
    const maxedOut = manager("4", "Verkäufer", 0, { balance: -8000000, balanceWithBonuses: -8000000 })

    // In the red but with plenty of room left - the normal state of half this league in
    // August. Three quarters of the allowance still free.
    const stretched = manager("4", "Verkäufer", 24000000, { balance: -8000000, balanceWithBonuses: -8000000 })

    // Up for a full day, no bids
    const stale = { ...LISTING, listedSince: "2026-08-12T12:00:00Z", offerCount: 0 }

    it("scores a seller who cannot bid at all on an old, quiet listing at the top", () => {
        const risk = forcedSaleRisk(stale, [maxedOut], now)
        expect(risk.score).toBeCloseTo(1)
        expect(risk.level).toBe("high")
        expect(risk.deficit).toBe(8000000)
        expect(risk.overdraftUsed).toBeCloseTo(1)
    })

    it("measures the deficit against the room the rules still leave, not against the balance", () => {
        // Same 8.000.000 in the red, but 24.000.000 left to bid: a quarter of the allowance
        // is used, so this is pressure and not a forced sale
        const risk = forcedSaleRisk(stale, [stretched], now)
        expect(risk.overdraftUsed).toBeCloseTo(0.25)
        expect(risk.score).toBeCloseTo(0.25)
        expect(risk.level).toBe("watch")
    })

    it("keeps growing with the listing age up to a day", () => {
        const fresh = forcedSaleRisk({ ...stale, listedSince: "2026-08-13T09:00:00Z" }, [maxedOut], now)
        const old = forcedSaleRisk({ ...stale, listedSince: "2026-08-11T00:00:00Z" }, [maxedOut], now)
        expect(fresh.score).toBeCloseTo(0.125)
        // Two days in, the age no longer adds anything
        expect(old.score).toBeCloseTo(1)
    })

    it("fades as the bids come in", () => {
        const quiet = forcedSaleRisk({ ...stale, offerCount: 0 }, [maxedOut], now)
        const busy = forcedSaleRisk({ ...stale, offerCount: 3 }, [maxedOut], now)
        expect(busy.score).toBeCloseTo(quiet.score / 4)
        expect(busy.level).toBe("watch")
    })

    it("is zero for a seller who is not in the red", () => {
        expect(forcedSaleRisk(stale, [manager("4", "Verkäufer", 40000000)], now).score).toBe(0)
    })

    it("is zero for a Kickbase listing, which has no seller under pressure", () => {
        const freeAgent = { ...stale, isFreeAgent: true, seller: "Kickbase", sellerId: null }
        expect(forcedSaleRisk(freeAgent, [maxedOut], now).score).toBe(0)
    })

    it("is zero for the user's own listing", () => {
        const own = { ...maxedOut, isSelf: true }
        expect(forcedSaleRisk(stale, [own], now).score).toBe(0)
    })

    it("is zero for a seller who is not in the league table", () => {
        expect(forcedSaleRisk(stale, [manager("1", "Anna", 30000000)], now).score).toBe(0)
    })

    it("has no score without a listing age, which the factors hinge on", () => {
        const risk = forcedSaleRisk({ ...stale, listedSince: null }, [maxedOut], now)
        expect(risk.score).toBeNull()
        expect(risk.level).toBe("none")
    })

    it("calls a score above the alert threshold a forced sale and the rest pressure", () => {
        const high = forcedSaleRisk(stale, [maxedOut], now)
        expect(high.score).toBeGreaterThanOrEqual(DISTRESS_ALERT_SCORE)
        expect(high.level).toBe("high")

        // Six hours old, so a quarter of the score of the same listing a day in
        const watch = forcedSaleRisk({ ...stale, listedSince: "2026-08-13T06:00:00Z" }, [maxedOut], now)
        expect(watch.score).toBeGreaterThanOrEqual(DISTRESS_WATCH_SCORE)
        expect(watch.score).toBeLessThan(DISTRESS_ALERT_SCORE)
        expect(watch.level).toBe("watch")
    })

    it("says nothing at all below the watch band", () => {
        // A listing an hour old scores 0,04: real pressure, but not worth a badge on a
        // table where half the league is in the red anyway
        const quiet = forcedSaleRisk({ ...stale, listedSince: "2026-08-13T11:00:00Z" }, [maxedOut], now)
        expect(quiet.score).toBeLessThan(DISTRESS_WATCH_SCORE)
        expect(quiet.score).toBeGreaterThan(0)
        expect(quiet.level).toBe("none")
    })
})

describe("managerStacks", () => {
    it("sorts every manager by their ceiling, richest first", () => {
        expect(managerStacks(BALANCES).map((stack) => stack.username))
            .toEqual(["Verkäufer", "Anna", "shirazzi", "Bernd", "Clara"])
    })

    it("expresses each stack as a share of the largest, so a bar can be drawn", () => {
        const stacks = managerStacks(BALANCES)
        expect(stacks[0].share).toBe(1)
        expect(stacks[1].share).toBeCloseTo(30 / 40)
    })

    it("marks the user's own stack", () => {
        expect(managerStacks(BALANCES).filter((stack) => stack.isSelf).map((s) => s.username))
            .toEqual(["shirazzi"])
    })

    it("drops a manager without a ceiling instead of sorting them in as broke", () => {
        const unknown = manager("6", "Neu", null, { maxBidWithBonuses: null })
        expect(managerStacks([...BALANCES, unknown]).map((stack) => stack.username))
            .not.toContain("Neu")
    })

    it("has nothing to show without balances", () => {
        expect(managerStacks(null)).toEqual([])
        expect(managerStacks([])).toEqual([])
    })
})
