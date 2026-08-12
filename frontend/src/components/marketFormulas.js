// The arithmetic behind the derived transfer market columns, kept apart from the table
// so the edge cases (no history, falling market value, free agents) can be tested.

const isMissing = (value) => value === null || value === undefined

/**
 * A market value move as a share of the current market value.
 *
 * A missing move stays missing: treating it as a zero would claim the value held still
 * when the history is simply too short to say.
 */
export function relativeChange(delta, marketValue) {
    if (isMissing(delta) || !marketValue)
        return null

    return delta / marketValue
}

/**
 * How many days the market value needs to grow into the asking price, at the pace of
 * the last three days.
 *
 * Null means the question has no answer rather than a large one: a market value that is
 * flat or falling never catches up, and three days of growth cannot be averaged from a
 * history that does not cover them.
 */
export function daysToBreakEven({ marketValue, price, today, yesterday, twoDays }) {
    if (!marketValue || isMissing(price))
        return null

    const markup = price - marketValue

    // Free agents are listed at exactly the market value, and a listing below it is
    // already worth more than it costs
    if (markup <= 0)
        return 0

    if ([today, yesterday, twoDays].some(isMissing))
        return null

    const dailyGrowth = (today + yesterday + twoDays) / 3
    if (dailyGrowth <= 0)
        return null

    return markup / dailyGrowth
}
