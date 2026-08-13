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

const MINUTE = 60 * 1000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/**
 * A span of milliseconds as a short German duration, e.g. "2 Tage 3 Std." or "45 Min.".
 *
 * Only the two largest units that carry information are shown: on a listing that runs for
 * days the minutes are noise, and on one about to expire the days are zero anyway.
 *
 * Null means there is nothing to say. A negative span is clamped to zero rather than
 * rendered as "-3 Std.", which on a countdown reads as a listing running backwards.
 */
export function formatDuration(milliseconds) {
    if (milliseconds === null || milliseconds === undefined || Number.isNaN(milliseconds))
        return null

    const total = Math.max(0, milliseconds)

    const days = Math.floor(total / DAY)
    const hours = Math.floor((total % DAY) / HOUR)
    const minutes = Math.floor((total % HOUR) / MINUTE)

    if (days > 0)
        return hours > 0 ? `${days} ${days === 1 ? "Tag" : "Tage"} ${hours} Std.` : `${days} ${days === 1 ? "Tag" : "Tage"}`

    if (hours > 0)
        return minutes > 0 ? `${hours} Std. ${minutes} Min.` : `${hours} Std.`

    return `${minutes} Min.`
}

/**
 * How long ago an ISO timestamp was, in milliseconds.
 *
 * Null for a missing or unparseable timestamp: not every listing carries one, and a NaN
 * would sort ahead of every real value.
 */
export function elapsedSince(isoTimestamp, now) {
    if (!isoTimestamp)
        return null

    const listed = new Date(isoTimestamp).getTime()
    if (Number.isNaN(listed))
        return null

    return now - listed
}
