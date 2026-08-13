// What each dataset has to look like, and what to call it in front of a user.
//
// This table is the replacement for something that was lost, not an addition. Until this
// version every component did `import data from "../data/market.json"`, and webpack resolved
// that at build time - so a dataset that was missing or unparsable failed the build, loudly,
// before anyone saw a page. That was free schema validation, and fetching at runtime gives
// it up: the bundle now builds against no data at all, and a component that does
// `data.map(...)` over an object crashes the tab instead.
//
// So the hook checks the one property every component depends on before it hands anything
// over: is this a list or a keyed object. That is deliberately shallow. Per-field validation
// would be a schema language, and the fields are already exercised by the component tests;
// what this catches is the class of failure that used to be impossible - a dataset that is
// there but is not the kind of thing the caller is about to iterate.
//
// `label` is the German name the loading, empty and error states use, so a failure says which
// table failed rather than which file did.
//
// backend/datasets.py holds the same list of names as the /api/data allowlist, and
// tests/test_data_plane.py fails if the two ever disagree.

export const ARRAY = "array"
export const OBJECT = "object"

export const DATA_CONTRACTS = {
    "market.json": { shape: ARRAY, label: "Transfermarkt" },
    "market_value_changes.json": { shape: ARRAY, label: "Marktwertveränderungen" },
    "taken_players.json": { shape: ARRAY, label: "Gebundene Spieler" },
    "free_players.json": { shape: ARRAY, label: "Freie Spieler" },
    "turnovers.json": { shape: ARRAY, label: "Transfererlöse" },
    "league_user_stats.json": { shape: ARRAY, label: "Liga-Tabelle" },
    "balances.json": { shape: ARRAY, label: "Kontostände" },
    "events.json": { shape: ARRAY, label: "Tagesplan" },
    "match_days.json": { shape: ARRAY, label: "Spieltage" },
    "live_points.json": { shape: ARRAY, label: "Live-Punkte" },
    // Keyed by manager name, one series per manager
    "revenue_sum.json": { shape: OBJECT, label: "Summe der Transfererlöse" },
    "team_values.json": { shape: OBJECT, label: "Teamwert" },
    // Keyed by manager id, under a "managers" property, plus a coverage header
    "manager_profiles.json": { shape: OBJECT, label: "Manager-Dossier" }
}

/**
 * The German name of a dataset, or the file name when there is no entry for it.
 */
export function datasetLabel(name) {
    return DATA_CONTRACTS[name]?.label ?? name
}

/**
 * Whether a payload is the kind of thing the caller is about to iterate.
 *
 * Null passes: a dataset the scrape has not written yet is a legitimate empty state, and
 * telling that apart from a broken one is the hook's job, not this function's.
 */
export function matchesContract(name, payload) {
    const contract = DATA_CONTRACTS[name]

    if (!contract || payload === null || payload === undefined)
        return true

    return contract.shape === ARRAY
        ? Array.isArray(payload)
        : typeof payload === "object" && !Array.isArray(payload)
}

/**
 * The empty value of a dataset's shape, so a caller can render a table with no rows in it
 * rather than guard every access.
 */
export function emptyValue(name) {
    return DATA_CONTRACTS[name]?.shape === OBJECT ? {} : []
}
