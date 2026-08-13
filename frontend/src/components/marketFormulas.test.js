import { relativeChange, daysToBreakEven, breakEvenBid } from "./marketFormulas"

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

// A row as market.json holds it, reduced to what the two formulas read
const row = (marketValue, avgDailyGrowth, price) => ({ marketValue, avgDailyGrowth, price })

describe("daysToBreakEven", () => {
    it("counts the days the market value needs to reach the price", () => {
        // 100.000 a day closing a 300.000 gap
        expect(daysToBreakEven(row(1000000, 100000, 1300000))).toBeCloseTo(3)
        expect(daysToBreakEven(row(1000000, 60000, 1300000))).toBeCloseTo(5)
    })

    it("is zero when the listing is already worth what it costs", () => {
        // Free agents are listed at exactly the market value
        expect(daysToBreakEven(row(1000000, 100000, 1000000))).toBe(0)
        expect(daysToBreakEven(row(1000000, 100000, 900000))).toBe(0)
    })

    it("has no answer when the market value is flat or falling", () => {
        expect(daysToBreakEven(row(1000000, -10000, 1300000))).toBeNull()
        expect(daysToBreakEven(row(1000000, 0, 1300000))).toBeNull()
    })

    it("has no answer without a growth figure", () => {
        // A history too short for the window, which is not the same as a growth of zero
        expect(daysToBreakEven(row(1000000, null, 1300000))).toBeNull()
        expect(daysToBreakEven(row(1000000, undefined, 1300000))).toBeNull()
    })

    it("has no answer without a market value or a price", () => {
        expect(daysToBreakEven(row(null, 100000, 1300000))).toBeNull()
        expect(daysToBreakEven(row(1000000, 100000, null))).toBeNull()
    })
})

describe("breakEvenBid", () => {
    it("projects the market value forward to the target horizon", () => {
        expect(breakEvenBid(row(1000000, 100000), 3)).toBe(1300000)
        expect(breakEvenBid(row(1000000, 100000), 7)).toBe(1700000)
        expect(breakEvenBid(row(1000000, 100000), 14)).toBe(2400000)
    })

    it("returns whole euros", () => {
        // Kickbase takes integers, and an averaged growth rarely is one
        expect(breakEvenBid(row(1000000, 33333.333), 3)).toBe(1100000)
        expect(Number.isInteger(breakEvenBid(row(1234567, 4321.7), 3))).toBe(true)
    })

    it("has no answer when the market value is flat or falling", () => {
        // Refusing to recommend is the point: such a player never pays for itself
        expect(breakEvenBid(row(1000000, 0), 3)).toBeNull()
        expect(breakEvenBid(row(1000000, -50000), 3)).toBeNull()
    })

    it("has no answer without a growth figure or a market value", () => {
        expect(breakEvenBid(row(1000000, null), 3)).toBeNull()
        expect(breakEvenBid(row(1000000, undefined), 3)).toBeNull()
        expect(breakEvenBid(row(null, 100000), 3)).toBeNull()
        expect(breakEvenBid(row(0, 100000), 3)).toBeNull()
    })

    it("is the inverse of daysToBreakEven", () => {
        // The property that ties the two columns together: bidding the suggestion means
        // breaking even exactly at the horizon
        for (const targetDays of [1, 3, 4, 7, 14, 30]) {
            for (const growth of [1, 12345, 100000, 987654.321]) {
                const base = row(1000000, growth)
                const bid = breakEvenBid(base, targetDays)
                expect(daysToBreakEven({ ...base, price: bid })).toBeCloseTo(targetDays, 4)
            }
        }
    })
})
