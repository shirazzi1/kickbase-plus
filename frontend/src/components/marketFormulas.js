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
