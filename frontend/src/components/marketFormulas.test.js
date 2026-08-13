import { relativeChange, daysToBreakEven, formatDuration, elapsedSince } from "./marketFormulas"

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
