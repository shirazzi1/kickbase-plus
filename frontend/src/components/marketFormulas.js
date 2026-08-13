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

// ---------------------------------------------------------------------------
// The auction solver: who else could pay for this listing, and what it takes to win.
//
// Every number below rests on the reconstructed balances in balances.json, which are
// derived from the transfer feed plus assumed login bonuses and achievements - not read
// from the API. They are estimates, and the callers say so on screen.
// ---------------------------------------------------------------------------

// Ids arrive as strings from the API and as numbers from some call sites, so every
// comparison goes through this instead of comparing the raw values
const sameId = (a, b) => {
    if (isMissing(a) || isMissing(b))
        return false

    return String(a) === String(b)
}

/**
 * A manager's estimated bidding ceiling, in the chosen view.
 *
 * `maxBidWithBonuses` folds in the login bonuses and achievements, so it is the higher
 * and the more complete of the two - and the more speculative one. The default follows
 * the market table, which shows the wider estimate rather than the one that is knowingly
 * too low.
 */
export function maxBidOf(manager, withBonuses = true) {
    if (!manager)
        return null

    const value = withBonuses ? manager.maxBidWithBonuses : manager.maxBid

    return isMissing(value) ? null : value
}

/**
 * A manager's estimated balance, in the same two views as the ceiling.
 */
export function balanceOf(manager, withBonuses = true) {
    if (!manager)
        return null

    const value = withBonuses ? manager.balanceWithBonuses : manager.balance

    return isMissing(value) ? null : value
}

/**
 * The logged in user's own entry in balances.json, or null when nothing marks it.
 *
 * The backend flags exactly one manager with `isSelf`. Until a scrape has run with that
 * field in place the answer is null, and every caller then has to do without the own
 * budget rather than guess at it.
 */
export function ownManager(balances) {
    if (!Array.isArray(balances))
        return null

    return balances.find((manager) => manager?.isSelf === true) ?? null
}

/**
 * Whether a manager listed this player.
 *
 * The listing's `sellerId` is the reliable link. It only exists from the scrape that
 * introduced it onwards, so the display name serves as the fallback - the same name the
 * "Verkäufer" column already shows, and the only link the older rows carry.
 */
function isSeller(manager, listing) {
    if (!isMissing(listing.sellerId))
        return sameId(manager?.userId, listing.sellerId)

    return Boolean(listing.seller) && manager?.username === listing.seller
}

/**
 * The managers who could pay the asking price for a listing, richest first.
 *
 * Kickbase never reveals who is bidding, only how many bids there are. This is the set
 * that *could* be behind them: everyone whose estimated ceiling reaches the asking price,
 * minus the seller, who does not bid against themselves, and minus the user.
 *
 * A ceiling exactly at the asking price still counts - that manager can match the price
 * to the euro, which is all it takes to be in the auction.
 */
export function affordableRivals(listing, balances, ownManagerId, { withBonuses = true } = {}) {
    if (!listing || isMissing(listing.price) || !Array.isArray(balances))
        return []

    return balances
        .filter((manager) => {
            if (isMissing(manager?.userId))
                return false

            if (isSeller(manager, listing))
                return false

            if (sameId(manager.userId, ownManagerId))
                return false

            const maxBid = maxBidOf(manager, withBonuses)

            return maxBid !== null && maxBid >= listing.price
        })
        .map((manager) => ({
            userId: String(manager.userId),
            username: manager.username,
            maxBid: maxBidOf(manager, withBonuses)
        }))
        .sort((a, b) => b.maxBid - a.maxBid)
}

/**
 * The smallest bid that should win a listing, and why.
 *
 * Two bounds meet here:
 *
 *   - The asking price is the floor. Kickbase rejects anything below it outright
 *     ("UnderpayNotAllowed"), so a "minimum bid" under the asking price is not a bid.
 *   - One euro over the richest affordable rival's ceiling beats every bid they could
 *     possibly place. The game plan states this as one euro over the *second* highest
 *     ceiling among all affordable managers, the user included. The two differ only when
 *     the user is not the richest of them - and there both run past the user's own ceiling
 *     and are capped to it, so the bid that can actually be placed is the same either way.
 *
 * With no affordable rival there is no auction to win - the asking price alone buys the
 * player, and `isPhantom` says so.
 *
 * `bid` is capped at the user's own ceiling so it stays a bid that can actually be
 * placed; `required` keeps the uncapped truth, and `exceedsBudget` marks the gap between
 * them. Without a known own ceiling nothing is capped and `ownMaxBid` stays null.
 *
 * On the user's own listing there is nothing to solve - you cannot bid on your own player.
 * `isOwnListing` says so and every number stays null, rather than offering the richest
 * manager's whole budget as a bid and then marking it unaffordable.
 */
export function minWinningBid(listing, balances, ownManagerId, { withBonuses = true } = {}) {
    const rivals = affordableRivals(listing, balances, ownManagerId, { withBonuses })
    const price = isMissing(listing?.price) ? null : listing.price

    const own = Array.isArray(balances)
        ? balances.find((manager) => sameId(manager?.userId, ownManagerId)) ?? null
        : null
    const ownMaxBid = maxBidOf(own, withBonuses)

    const nothingToSolve = { bid: null, required: null, isPhantom: false, exceedsBudget: false }

    // Kickbase does not let anyone bid on their own listing, so the rival set is the list of
    // possible buyers and there is no winning bid to name
    if (own && listing && isSeller(own, listing))
        return { ...nothingToSolve, isOwnListing: true, ownMaxBid, rivals }

    if (price === null)
        return { ...nothingToSolve, isOwnListing: false, ownMaxBid, rivals }

    const required = rivals.length === 0 ? price : Math.max(price, rivals[0].maxBid + 1)
    const exceedsBudget = ownMaxBid !== null && required > ownMaxBid

    // "Nobody can pay" is a claim about the league. Without managers to check it against -
    // an empty balances.json, or a league of one - there is no claim to make.
    const contenders = (Array.isArray(balances) ? balances : []).filter((manager) =>
        !isMissing(manager?.userId)
        && !isSeller(manager, listing)
        && !sameId(manager.userId, ownManagerId)
    ).length

    return {
        bid: exceedsBudget ? ownMaxBid : required,
        required,
        isPhantom: rivals.length === 0 && contenders > 0,
        exceedsBudget,
        isOwnListing: false,
        ownMaxBid,
        rivals
    }
}

// From this listing age on, the age no longer adds to the score. A day is roughly four
// scrape cycles: long enough that the listing did not just go up, short enough to fire
// while the listing is still open.
export const DISTRESS_AGE_HOURS = 24

// Both bands are set from the observed distribution rather than picked: against the real
// 86 listings of 2026-08-13, 0.4 marks exactly the one listing at the top of the field
// (46% of the seller's overdraft used, a day old, no bids) and 0.2 catches the nine behind
// it. Scored on the balance alone the same 0.4 fired on 25 of 86 rows, because half this
// league runs a negative balance in August - which is why the score measures the overdraft
// and not the balance.
export const DISTRESS_ALERT_SCORE = 0.4
export const DISTRESS_WATCH_SCORE = 0.2

/**
 * How much the seller of a listing looks like they *have* to sell.
 *
 * Three observable factors, each in [0, 1], multiplied:
 *
 *   - how much of the overdraft the rules allow them the seller has already used,
 *   - how long the listing has been up without finding a buyer,
 *   - how quiet the listing is - one over one plus the bid count.
 *
 * The first factor needs no threshold of its own. Kickbase allows an overdraft of a third
 * of team value plus balance, and whatever is left of that allowance is exactly the
 * manager's remaining `maxBid` - so the share already used is `deficit / (deficit + maxBid)`
 * and it reaches 1 precisely when the manager cannot place a bid at all. A negative balance
 * on its own is not distress: half this league runs one in August with tens of millions of
 * room to spare.
 *
 * The product is a heuristic, not a probability. It is zero for a seller with money, for
 * Kickbase's own listings, and for the user's own; null when the listing age is unknown,
 * since a fresh listing and an old one score very differently.
 */
export function forcedSaleRisk(listing, balances, now, { withBonuses = true } = {}) {
    const none = {
        score: 0, level: "none", deficit: 0, headroom: null, overdraftUsed: 0,
        ageHours: null, offerCount: null, seller: null
    }

    if (!listing || !Array.isArray(balances))
        return none

    // Kickbase's own listings have no seller under pressure behind them
    if (listing.isFreeAgent)
        return none

    const seller = balances.find((manager) => isSeller(manager, listing)) ?? null
    if (!seller || seller.isSelf === true)
        return none

    const balance = balanceOf(seller, withBonuses)
    const headroom = maxBidOf(seller, withBonuses)

    if (balance === null || headroom === null || balance >= 0)
        return { ...none, headroom, seller: seller.username }

    const deficit = -balance
    const overdraftUsed = deficit / (deficit + headroom)

    const elapsed = elapsedSince(listing.listedSince, now)
    if (elapsed === null)
        return {
            score: null, level: "none", deficit, headroom, overdraftUsed,
            ageHours: null, offerCount: listing.offerCount, seller: seller.username
        }

    const ageHours = Math.max(0, elapsed) / HOUR
    const ageFactor = Math.min(1, ageHours / DISTRESS_AGE_HOURS)

    // A listing nobody bids on is the one the seller has to cut the price on
    const offerCount = isMissing(listing.offerCount) ? 0 : listing.offerCount
    const quietFactor = 1 / (1 + offerCount)

    const score = overdraftUsed * ageFactor * quietFactor

    return {
        score,
        level: score >= DISTRESS_ALERT_SCORE
            ? "high"
            : score >= DISTRESS_WATCH_SCORE ? "watch" : "none",
        deficit,
        headroom,
        overdraftUsed,
        ageHours,
        offerCount,
        seller: seller.username
    }
}

/**
 * Every manager's estimated bidding ceiling, richest first - the stack sizes at the table.
 *
 * `share` is the ceiling as a fraction of the largest one, so a bar can be drawn without
 * the caller working out the maximum again. Managers without a ceiling are dropped rather
 * than sorted in as zero, which would read as "broke" instead of "unknown".
 */
export function managerStacks(balances, { withBonuses = true } = {}) {
    if (!Array.isArray(balances))
        return []

    const stacks = balances
        .filter((manager) => maxBidOf(manager, withBonuses) !== null)
        .map((manager) => ({
            userId: isMissing(manager.userId) ? null : String(manager.userId),
            username: manager.username,
            profilePic: manager.profilePic,
            maxBid: maxBidOf(manager, withBonuses),
            balance: balanceOf(manager, withBonuses),
            teamValue: manager.teamValue,
            isSelf: manager.isSelf === true
        }))
        .sort((a, b) => b.maxBid - a.maxBid)

    const largest = stacks.length > 0 ? stacks[0].maxBid : 0

    return stacks.map((stack) => ({ ...stack, share: largest > 0 ? stack.maxBid / largest : 0 }))
}
