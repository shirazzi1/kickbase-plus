// Reading manager_profiles.json without making it a build dependency, plus the derivations
// the dossier tab and the market table's bidder chip need from it.
//
// Two things shape this module:
//
//   1. The file is written by the last stage of a scrape run, and that stage has never run
//      on any deployment yet. A static `import` of a file that is not there does not degrade
//      gracefully - webpack fails the whole build with "Module not found", which takes every
//      other tab down with it. See loadManagerProfiles().
//   2. Every metric in the file carries its own `n`. Nothing in here fills a missing value
//      in: a metric without data returns null, and the callers render "keine Datenlage"
//      instead of a zero that reads like a measurement.

import { affordableRivals } from "./marketFormulas"

const isMissing = (value) => value === null || value === undefined

// The one file this module reads, as require.context keys it
const PROFILES_KEY = "./manager_profiles.json"

/**
 * The profiles document, or null when the scrape has not written it yet.
 *
 * `require.context` is what keeps a missing file from being a build error: it asks webpack
 * for *whatever matches* in a directory instead of for one specific module, so an absent
 * manager_profiles.json leaves an empty map rather than an unresolved import. The directory
 * itself is safe to ask for - the market table's static imports already make the build fail
 * without it.
 *
 * Under Jest there is no webpack and therefore no context to ask, so this returns null
 * there. That is why both consumers take their profiles as a prop with this as the default:
 * the tests hand in fixtures, the app hands in the file.
 */
export function loadManagerProfiles() {
    try {
        const context = require.context("../data", false, /^\.\/manager_profiles\.json$/)

        if (!context.keys().includes(PROFILES_KEY))
            return null

        const document = context(PROFILES_KEY)

        // Webpack hands JSON back as a module namespace under some settings and as the plain
        // object under others
        return document?.managers ? document : document?.default ?? null
    } catch (error) {
        // Two situations end up here, and neither may cost more than the dossier's content:
        // a file that exists but does not parse, and a runtime without require.context -
        // which is Jest, where the components take their profiles as a prop instead.
        return null
    }
}

/**
 * The managers in the document, alphabetically, or an empty list when there is no document.
 *
 * The backend writes an entry for every manager in the league, including one who has never
 * traded - so an empty list here means the file is missing or unusable, never that the
 * league is quiet.
 */
export function managerProfileList(profiles) {
    const managers = profiles?.managers

    if (!managers || typeof managers !== "object")
        return []

    return Object.values(managers)
        .filter(Boolean)
        .sort((a, b) => String(a.managerName ?? "").localeCompare(String(b.managerName ?? ""), "de"))
}

/**
 * What the market value coverage header means for the two metrics that depend on it.
 *
 * The markup and the momentum share are both read off market value curves that the
 * profiles stage does not fetch itself - it reads the cache the market value stage leaves
 * behind. When that stage failed, every manager's markup and momentum sit at n = 0, which
 * from the profiles alone looks exactly like a league that never buys anything.
 *
 * Returns null when there is nothing to warn about, otherwise the level and the sentence.
 */
export function coverageNote(profiles) {
    const coverage = profiles?.marketValueCoverage

    if (!coverage || isMissing(coverage.of) || isMissing(coverage.players))
        return null

    // Nobody bought anything, so there was no curve to look up in the first place
    if (coverage.of === 0)
        return null

    if (coverage.players === 0)
        return {
            severity: "warning",
            text: "Marktwert-Vorstufe lieferte diesmal nichts - Aufschlag und Momentum ohne "
                + `Datenlage. Für keinen der ${coverage.of} gekauften Spieler lag eine `
                + "Marktwert-Kurve vor."
        }

    if (coverage.players < coverage.of)
        return {
            severity: "info",
            text: `Marktwert-Kurven lagen für ${coverage.players} von ${coverage.of} gekauften `
                + "Spielern vor. Aufschlag und Momentum zählen nur diese Käufe - das jeweilige "
                + "n sagt, wie viele es pro Manager waren."
        }

    return {
        severity: "success",
        text: `Marktwert-Kurven lagen für alle ${coverage.of} gekauften Spieler vor.`
    }
}

// ---------------------------------------------------------------------------
// The likely-bidder chip: which of the managers who *could* pay for a listing also buy
// players like this one.
//
// Affordability comes from the auction solver (affordableRivals), the pattern from the
// profiles. Both halves are estimates of different kinds - a reconstructed budget and a
// habit measured over a few dozen transfers - so the chip names managers and its tooltip
// names the reason, never a probability.
// ---------------------------------------------------------------------------

// How many buys from a club make it part of a manager's pattern.
//
// Measured, not picked. Two weeks into the season, the 39 club entries in this league's
// profiles sit at 1, 2, 3 or 4 buys - and 24 of them at exactly 2, which makes two the noise
// floor rather than a preference. At two buys 67 of 82 listings get a chip naming two
// managers on average, which is a column that fires on four rows in five and therefore says
// nothing. At three it fires on 23 of 82 and names 1.8 managers - a claim worth reading.
// Worth revisiting once a full season of transfers has accumulated.
export const CLUB_PATTERN_MIN_BUYS = 3

// What counts as "buys momentum". Both bounds are needed: a share of 1.0 out of two buys is
// noise, and the league average sits near half, since roughly half of all market values rise
// on any given day.
export const MOMENTUM_PATTERN_SHARE = 0.6
export const MOMENTUM_PATTERN_MIN_N = 5

const percentOf = (share) => `${Math.round(share * 100)} %`

/**
 * Whether a listing's market value is currently on the way up.
 *
 * The seven day average is the window the momentum metric itself uses, so it is the one the
 * comparison is made in. Today's move is the fallback for a player the history does not
 * reach back seven days for. Null means the row carries no trend at all - which is the
 * normal case right after a scrape that could not fetch the curves.
 */
export function isRising(listing) {
    if (!isMissing(listing?.sevenDaysAvg))
        return listing.sevenDaysAvg > 0

    if (!isMissing(listing?.today))
        return listing.today > 0

    return null
}

/**
 * Why one manager fits a listing, or an empty list when they do not.
 *
 * Two observable patterns, both from the manager's own purchases:
 *
 *   - they buy from this player's club (topClubs, from CLUB_PATTERN_MIN_BUYS buys on),
 *   - they buy into rising market values and this market value is rising.
 */
export function bidderReasons(profile, listing) {
    if (!profile || !listing)
        return []

    const reasons = []

    const club = (profile.topClubs?.clubs ?? []).find((entry) =>
        !isMissing(entry?.teamId)
        && !isMissing(listing.teamId)
        && String(entry.teamId) === String(listing.teamId)
        && (entry.buys ?? 0) >= CLUB_PATTERN_MIN_BUYS
    )

    if (club)
        reasons.push({
            kind: "club",
            text: `${club.buys} Käufe bei ${club.teamName ?? `Team ${club.teamId}`}`
        })

    const momentum = profile.momentumBuys

    if (momentum
        && !isMissing(momentum.share)
        && momentum.share >= MOMENTUM_PATTERN_SHARE
        && (momentum.n ?? 0) >= MOMENTUM_PATTERN_MIN_N
        && isRising(listing) === true)
        reasons.push({
            kind: "momentum",
            text: `${percentOf(momentum.share)} der Käufe in einen steigenden Marktwert `
                + `(aus ${momentum.n} Käufen), und dieser Marktwert steigt`
        })

    return reasons
}

/**
 * The managers who could pay for a listing *and* whose buying pattern fits it.
 *
 * `rivals` is the affordable set from the auction solver - the market table already derives
 * it per row, so it is passed in rather than derived twice. Without it, affordableRivals()
 * is called here, which is the same function the solver columns use.
 *
 * An empty list is the answer whenever there is nothing to say: no profiles on disk, no
 * budget data, or nobody affordable whose pattern matches. The caller then renders no chip
 * at all - an empty chip would claim that nobody wants this player, which is not something
 * the transfer feed can show.
 */
export function likelyBidders(listing, profiles, { rivals, balances, ownManagerId } = {}) {
    const managers = profiles?.managers

    if (!managers || !listing)
        return []

    const affordable = rivals ?? affordableRivals(listing, balances, ownManagerId)

    return affordable
        .map((rival) => {
            const profile = managers[String(rival.userId)]
            const reasons = bidderReasons(profile, listing)

            return { ...rival, reasons }
        })
        .filter((rival) => rival.reasons.length > 0)
}

/**
 * The chip's own text: up to `visible` names, and how many are left over.
 *
 * Two names is what fits a table cell. The rest is a count rather than a truncated third
 * name, so the cell never suggests that the list ended where it was cut off.
 */
export function bidderChipLabel(bidders, visible = 2) {
    if (!bidders || bidders.length === 0)
        return null

    const names = bidders.slice(0, visible).map((bidder) => bidder.username ?? "?")
    const hidden = bidders.length - names.length

    return hidden > 0 ? `${names.join(", ")} +${hidden}` : names.join(", ")
}
