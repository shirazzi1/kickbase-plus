// The arithmetic behind the live swing meter, kept apart from the rendering so the cases
// that decide whether a number is honest can be tested: a match day that has not started,
// a player who has played for zero points, a rival who cannot field any more players.
//
// What this module deliberately does NOT do: infer the rival's starting eleven. Ownership
// is observable, the fielded eleven is not. Every open player is therefore reported as a
// possibility ("falls aufgestellt"), never as a forecast.

// A manager fields eleven players, so a squad of fourteen cannot score fourteen times.
export const LINEUP_SIZE = 11

// Match day phases. The live points of a match day only move while it runs.
export const BEFORE = "vor"
export const RUNNING = "laufend"
export const DONE = "beendet"

const MINUTE = 60 * 1000
const HOUR = 60 * MINUTE

// match_days.json stores the kickoff of the last match, not its final whistle. Two hours
// covers 90 minutes plus half time and stoppage, after which nothing can move any more.
export const MATCH_DURATION = 2 * HOUR

// An average over three players is not an average, it is three players. Below this the
// match day is too young to derive a per-player reference from.
const MIN_PLAYERS_FOR_AVERAGE = 5

const parse = (isoTimestamp) => {
    const parsed = Date.parse(isoTimestamp)
    return Number.isNaN(parsed) ? null : parsed
}

/**
 * The match day the live points belong to, and how far it has come.
 *
 * A match day counts as running from its first kickoff until two hours after its last one.
 * Outside any such window the most recently finished match day is the one on display,
 * because that is what the live endpoint keeps showing until the next kickoff. Only a
 * point in time before the very first kickoff of the season has nothing behind it.
 *
 * Null when there is no schedule to look at.
 */
export function currentMatchDay(matchDays, now) {
    const scheduled = (matchDays || [])
        .map((matchDay) => ({
            day: matchDay.day,
            firstMatch: matchDay.firstMatch,
            lastMatch: matchDay.lastMatch,
            start: parse(matchDay.firstMatch),
            end: parse(matchDay.lastMatch),
        }))
        .filter((matchDay) => matchDay.start !== null && matchDay.end !== null)
        .sort((a, b) => a.start - b.start)

    if (scheduled.length === 0)
        return null

    const running = scheduled.find(
        (matchDay) => now >= matchDay.start && now <= matchDay.end + MATCH_DURATION)
    if (running)
        return { day: running.day, firstMatch: running.firstMatch, lastMatch: running.lastMatch, phase: RUNNING }

    const finished = scheduled.filter((matchDay) => now > matchDay.end + MATCH_DURATION)
    if (finished.length === 0) {
        const next = scheduled[0]
        return { day: next.day, firstMatch: next.firstMatch, lastMatch: next.lastMatch, phase: BEFORE }
    }

    const last = finished[finished.length - 1]
    return { day: last.day, firstMatch: last.firstMatch, lastMatch: last.lastMatch, phase: DONE }
}

/**
 * One manager's squad, merged from the live points and the ownership snapshot.
 *
 * Two sources are needed because neither is enough alone: the live endpoint carries the
 * points but is only written when someone asks for it, and `taken_players.json` carries
 * the full roster including everyone who has not played yet. Ownership is joined on the
 * manager's *name*, since `taken_players.json` has no user id — a renamed manager loses
 * their roster here, which is why the live entry's own players are kept as a fallback.
 *
 * Points default to zero rather than null: a player the live endpoint does not mention
 * has not been credited with anything.
 */
export function buildRoster({ userName, livePlayers, takenPlayers }) {
    const pointsById = new Map()
    const nameById = new Map()

    for (const player of livePlayers || []) {
        pointsById.set(String(player.playerId), player.points || 0)
        nameById.set(String(player.playerId), player.fullName
            || `${player.firstName || ""} ${player.lastName || ""}`.trim())
    }

    const roster = []
    const seen = new Set()

    for (const player of takenPlayers || []) {
        if (player.owner !== userName)
            continue

        const playerId = String(player.playerId)
        seen.add(playerId)
        roster.push({
            playerId,
            name: `${player.firstName || ""} ${player.lastName || ""}`.trim() || nameById.get(playerId) || playerId,
            position: player.position ?? null,
            status: player.status ?? null,
            points: pointsById.get(playerId) ?? 0,
        })
    }

    // A player the live endpoint credits to this manager but the roster snapshot does not
    // know: the two files are written at different times, and dropping him would drop his
    // points out of the gap.
    for (const player of livePlayers || []) {
        const playerId = String(player.playerId)
        if (seen.has(playerId))
            continue

        roster.push({
            playerId,
            name: nameById.get(playerId) || playerId,
            position: null,
            status: null,
            points: player.points || 0,
        })
    }

    return roster
}

/**
 * Split a squad into the players whose points are final and those still to come.
 *
 * Before the first kickoff nothing has been played; two hours after the last one
 * everything has. In between the only signal on disk is the point total, so a player
 * without points counts as still open.
 *
 * That is the one assumption in this module: a player who was fielded and earned exactly
 * zero points is indistinguishable from one whose match has not started, and is counted
 * as still to come. It overstates what is left rather than promising a gap is safe.
 */
export function classifyRoster(players, phase) {
    if (phase === BEFORE)
        return { played: [], open: [...(players || [])] }

    if (phase === DONE)
        return { played: [...(players || [])], open: [] }

    const played = []
    const open = []

    for (const player of players || []) {
        if (player.points)
            played.push(player)
        else
            open.push(player)
    }

    return { played, open }
}

const sumPoints = (players) => players.reduce((total, player) => total + (player.points || 0), 0)

/**
 * The gap to a rival, split into the part that cannot move any more and the part that can.
 *
 * Three buckets, from the user's perspective:
 *
 *   - `gap`: both sides' finished players. Positive means ahead. Since an open player has
 *     no points yet, this is the whole of the current difference — the other two buckets
 *     are what is still *at stake*, not parts of the number on the board.
 *   - `shared`: players open on both rosters. They cancel out, so they move nothing.
 *     Kickbase gives a player to exactly one manager per league, so this is normally
 *     empty; it is computed anyway because the two source files are written at different
 *     times, and a player transferred in between appears on both rosters. Counting him as
 *     a differential for both sides would invent swing in both directions at once.
 *   - `ownOpen` / `rivalOpen`: the differentials. Every point they score moves the gap by
 *     its full amount — up for own players, down for the rival's.
 *
 * `startersLeft` is how many players each side may still field. A manager fields eleven,
 * so nine finished players leave room for two more, however large the squad is.
 */
export function decomposeSwing({ ownPlayers, rivalPlayers, phase }) {
    const own = classifyRoster(ownPlayers, phase)
    const rival = classifyRoster(rivalPlayers, phase)

    const rivalOpenIds = new Set(rival.open.map((player) => player.playerId))
    const ownOpenIds = new Set(own.open.map((player) => player.playerId))

    const shared = own.open.filter((player) => rivalOpenIds.has(player.playerId))
    const ownOpen = own.open.filter((player) => !rivalOpenIds.has(player.playerId))
    const rivalOpen = rival.open.filter((player) => !ownOpenIds.has(player.playerId))

    const ownPoints = sumPoints(own.played)
    const rivalPoints = sumPoints(rival.played)

    return {
        gap: ownPoints - rivalPoints,
        ownPlayed: { count: own.played.length, points: ownPoints },
        rivalPlayed: { count: rival.played.length, points: rivalPoints },
        shared,
        ownOpen,
        rivalOpen,
        ownStartersLeft: Math.max(0, LINEUP_SIZE - own.played.length),
        rivalStartersLeft: Math.max(0, LINEUP_SIZE - rival.played.length),
    }
}

/**
 * What one played player is worth on this match day, averaged over the whole league.
 *
 * A reference taken from the match day in progress rather than from a model: no
 * distribution, no simulation, just what the players who have already played scored.
 *
 * Null while too few players have played, because the ceiling and floor built on top of
 * it would then be one lucky goalkeeper wide.
 */
export function averagePointsPerPlayer(entries, phase, minPlayers = MIN_PLAYERS_FOR_AVERAGE) {
    let points = 0
    let count = 0

    for (const entry of entries || []) {
        const { played } = classifyRoster(entry.players || [], phase)
        points += sumPoints(played)
        count += played.length
    }

    if (count < minPlayers)
        return null

    return points / count
}

/**
 * The gap's range if every player who may still be fielded scores the match day average.
 *
 * Not a probability and not a forecast: the arithmetic of "all my open players deliver
 * and none of his do" against the reverse. Open players beyond the eleven a manager may
 * field are left out, so a bench cannot inflate the swing.
 *
 * Ceiling and floor are null without a reference value — a range of "0 to 0" would read
 * as a settled match day.
 */
export function swingBounds(decomposition, pointsPerPlayer) {
    const ownCount = Math.min(decomposition.ownOpen.length, decomposition.ownStartersLeft)
    const rivalCount = Math.min(decomposition.rivalOpen.length, decomposition.rivalStartersLeft)

    if (pointsPerPlayer === null || pointsPerPlayer === undefined) {
        return { ownCount, rivalCount, ownSwing: null, rivalSwing: null, ceiling: null, floor: null }
    }

    const ownSwing = ownCount * pointsPerPlayer
    const rivalSwing = rivalCount * pointsPerPlayer

    return {
        ownCount,
        rivalCount,
        ownSwing,
        rivalSwing,
        ceiling: decomposition.gap + ownSwing,
        floor: decomposition.gap - rivalSwing,
    }
}

/**
 * The banner sentence, e.g. "Du liegst 18 Punkte hinter Max – 3 deiner Spieler spielen
 * noch, 2 bei Max".
 *
 * German, because it is user facing, and built here rather than in the component so the
 * singular, the direction and the "nobody is playing any more" case are testable.
 */
export function swingHeadline(decomposition, { rivalName }) {
    const { gap, ownOpen, rivalOpen } = decomposition

    let standing
    if (gap === 0)
        standing = `Gleichstand mit ${rivalName}`
    else if (gap > 0)
        standing = `Du liegst ${gap} ${gap === 1 ? "Punkt" : "Punkte"} vor ${rivalName}`
    else
        standing = `Du liegst ${-gap} ${gap === -1 ? "Punkt" : "Punkte"} hinter ${rivalName}`

    const parts = []
    if (ownOpen.length === 1)
        parts.push("einer deiner Spieler spielt noch")
    else if (ownOpen.length > 1)
        parts.push(`${ownOpen.length} deiner Spieler spielen noch`)
    if (rivalOpen.length > 0)
        parts.push(`${rivalOpen.length} bei ${rivalName}`)

    if (parts.length === 0)
        return `${standing} – kein Spieler spielt noch`

    return `${standing} – ${parts.join(", ")}`
}
