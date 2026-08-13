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
 * The average daily growth a row can be judged by, or null when it cannot.
 *
 * Shared by both break-even columns so the "no answer" rule exists once. A missing
 * figure means the history is too short for the configured window; a figure at or below
 * zero means the market value never catches up. Neither is a number of days.
 */
function usableGrowth({ avgDailyGrowth }) {
    if (isMissing(avgDailyGrowth) || avgDailyGrowth <= 0)
        return null

    return avgDailyGrowth
}

/**
 * How many days the market value needs to grow into the asking price.
 *
 * Null means the question has no answer rather than a large one. The averaging itself
 * happens in the backend, which is the only place that holds the full history - this
 * used to average the three deltas that happened to be in market.json, and could
 * therefore express no other window.
 */
export function daysToBreakEven({ marketValue, price, avgDailyGrowth }) {
    if (!marketValue || isMissing(price))
        return null

    const markup = price - marketValue

    // Free agents are listed at exactly the market value, and a listing below it is
    // already worth more than it costs
    if (markup <= 0)
        return 0

    const growth = usableGrowth({ avgDailyGrowth })
    if (growth === null)
        return null

    return markup / growth
}

/**
 * The bid that breaks even exactly at the target horizon: what the market value will be
 * worth in targetDays days, at the pace measured over the growth window.
 *
 * The same line as daysToBreakEven, solved for the price instead of for the days. The
 * asking price does not enter it - break even is a statement about market value and
 * growth, and the price is what you compare the result against.
 *
 * Whole euros, because that is what Kickbase accepts. Null when there is nothing to
 * recommend: declining to suggest a bid is an answer, a bid on a falling market value
 * is not.
 */
export function breakEvenBid(row, targetDays) {
    const growth = usableGrowth(row)

    if (!row.marketValue || growth === null)
        return null

    return Math.round(row.marketValue + targetDays * growth)
}
