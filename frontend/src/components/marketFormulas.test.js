import { relativeChange, daysToBreakEven } from "./marketFormulas"

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
