// How fresh a dataset is, judged against the run manifest.
//
// The header used to render one green timestamp for everything, taken from ts_main.json,
// which the scraper stamped fresh whether or not the run had done any work. Now every
// dataset carries the id of the run that wrote it, and the manifest says how that run's
// stages ended - so "the market table is current, the balances are a run behind" is a
// sentence the UI can actually say.
//
// Kept apart from the components so the awkward cases (no manifest yet, a dataset from
// before this existed, a stage that was skipped) can be tested.

export const CURRENT = "current"
export const STALE = "stale"
export const FAILED = "failed"
export const UNKNOWN = "unknown"

// Which datasets a stage is responsible for. A stage that fails leaves every one of them
// holding whatever the last successful run wrote.
export const STAGE_BY_DATASET = {
    ts_market: "market",
    ts_market_value_changes: "market_value_changes",
    ts_taken_players: "taken_free_players",
    ts_free_players: "taken_free_players",
    ts_balances: "balances",
    ts_turnovers: "turnovers",
    ts_revenue_sum: "turnovers",
    ts_team_values: "team_values",
    ts_league_user_stats: "league_user_stats",
    ts_manager_profiles: "manager_profiles",
    ts_live_points: "live_points"
}

/**
 * How a dataset stands relative to the latest run.
 *
 * - CURRENT: this run wrote it.
 * - STALE: an earlier run wrote it and this one did not, so it is at least one run behind.
 * - FAILED: the stage that owns it did not succeed in this run. Strictly a reason for
 *   STALE, but worth its own answer: it is the difference between "nothing has changed"
 *   and "we could not find out".
 * - UNKNOWN: there is nothing to judge against — no manifest, or a timestamp from before
 *   run ids existed. Never guessed as CURRENT: claiming freshness without evidence is
 *   the habit this replaces.
 *
 * @param {object} timestamp the ts_*.json contents, or null
 * @param {object} manifest the ts_run_manifest.json contents, or null
 * @param {string} datasetName the timestamp file's name without ts_ and .json
 */
export function datasetStatus(timestamp, manifest, datasetName) {
    if (!timestamp || !manifest || !manifest.runId)
        return UNKNOWN

    const stageName = STAGE_BY_DATASET[`ts_${datasetName}`]
    const stage = (manifest.stages || []).find((s) => s.name === stageName)

    if (timestamp.runId === manifest.runId)
        return CURRENT

    // No run id at all: written before this existed, or by something that is not a run
    if (!timestamp.runId)
        return UNKNOWN

    if (stage && stage.status !== "ok")
        return FAILED

    return STALE
}

// One place for the colours, so the header badge and the Dev list cannot drift apart
const STATUS_COLOURS = {
    [CURRENT]: "green",
    [STALE]: "orange",
    [FAILED]: "red",
    [UNKNOWN]: "gray"
}

export function statusColour(status) {
    return STATUS_COLOURS[status] || STATUS_COLOURS[UNKNOWN]
}

const STATUS_LABELS = {
    [CURRENT]: "aktuell",
    [STALE]: "veraltet",
    [FAILED]: "Fehler",
    [UNKNOWN]: "unbekannt"
}

export function statusLabel(status) {
    return STATUS_LABELS[status] || STATUS_LABELS[UNKNOWN]
}

/**
 * The verdict for the whole run, for the badge in the header.
 *
 * An absent or unreadable manifest is UNKNOWN rather than CURRENT. The old header showed
 * green unconditionally, which is precisely what made a dead scraper invisible.
 *
 * @param {object} manifest the ts_run_manifest.json contents, or null
 */
export function runStatus(manifest) {
    if (!manifest || typeof manifest.allOk !== "boolean")
        return UNKNOWN

    return manifest.allOk ? CURRENT : FAILED
}

/**
 * A short German summary of the run, for the tooltip behind the header badge.
 *
 * @param {object} manifest the ts_run_manifest.json contents, or null
 */
export function runSummary(manifest) {
    if (!manifest || !manifest.stages)
        return "Kein Lauf-Protokoll vorhanden."

    const stages = manifest.stages
    const failed = stages.filter((s) => s.status !== "ok")

    if (failed.length === 0)
        return `Alle ${stages.length} Schritte erfolgreich.`

    const names = failed.map((s) => `${s.name} (${s.status === "skipped" ? "übersprungen" : "fehlgeschlagen"})`)

    return `${stages.length - failed.length}/${stages.length} Schritte erfolgreich. Nicht in Ordnung: ${names.join(", ")}.`
}
