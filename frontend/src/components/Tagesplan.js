// What has changed since the last runs, grouped by day.
//
// The backend (backend/events.py) diffs consecutive snapshots of the history store and
// writes events.json: the last 48 hours, newest first, each event with a severity, a German
// sentence and - where it is about a player - a playerId. This component only groups and
// renders; every judgement about what matters was already made where the data is.
//
// No deep link into the Kickbase app. There is no route to link to: play.kickbase.com serves
// the marketing site (checked 2026-08-13), api.kickbase.com is the API this project reads and
// not a user interface, and nothing else in this repository links to a player anywhere. A
// guessed URL would look like a feature and behave like a dead end, so the playerId stays in
// events.json for the day a real route is known and the row shows the player's name instead.

import Box from "@mui/material/Box"
import Chip from "@mui/material/Chip"
import Divider from "@mui/material/Divider"
import Typography from "@mui/material/Typography"

import { useJsonData } from "../hooks/useJsonData"
import { DataError, DataLoading } from "./DataState"
import { ERROR, LOADING } from "../hooks/useJsonData"

const DATASET = "events.json"

// The severity scale from backend/events.py, in the words the tab uses for it. Kept as a
// lookup rather than as a comparison chain so an unknown number degrades to a plain badge
// instead of to a blank one.
const SEVERITIES = {
    3: { label: "Jetzt", colour: "error" },
    2: { label: "Beachten", colour: "warning" },
    1: { label: "Notiz", colour: "default" }
}

const FALLBACK_SEVERITY = { label: "Ereignis", colour: "default" }

// The event types, in German. An unknown type keeps its raw name: a new type shipped by the
// backend should still show up here rather than disappear.
const TYPE_LABELS = {
    neue_listung: "Neue Listung",
    preissenkung: "Preissenkung",
    mv_sprung: "Marktwertsprung",
    laeuft_ab: "Läuft ab",
    zwangsverkauf: "Zwangsverkauf",
    cash_hortung: "Geld gehortet"
}

/**
 * Group events into "Heute", "Gestern" and, if the window reaches that far, dated days.
 *
 * The 48 hour window can straddle three calendar days - a run just after midnight sees the
 * day before yesterday - so the third group is real and gets its date as a heading rather
 * than being lumped in with "Gestern".
 *
 * @param {Array} events events.json, newest first
 * @param {Date} now the day to count "Heute" from
 */
export function groupByDay(events, now = new Date()) {
    const today = now.toDateString()
    const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000).toDateString()

    const groups = []

    events.forEach((event) => {
        const moment = new Date(event.ts)
        const day = moment.toDateString()

        let label = moment.toLocaleDateString("de-DE", { weekday: "long", day: "2-digit", month: "2-digit" })

        if (day === today)
            label = "Heute"
        else if (day === yesterday)
            label = "Gestern"

        const existing = groups.find((group) => group.key === day)

        if (existing)
            existing.events.push(event)
        else
            groups.push({ key: day, label: label, events: [event] })
    })

    return groups
}

// The first state in production, and the one that has to look deliberate: the history store
// is appended to at the end of a run, so the very first run has nothing to diff against.
function EmptyState() {
    return (
        <Box sx={{ padding: "40px 20px", textAlign: "center" }}>
            <Typography variant="h6" sx={{ opacity: 0.8 }}>
                Noch keine Ereignisse — der Verlauf baut sich ab dem nächsten Lauf auf
            </Typography>
            <Typography variant="body2" sx={{ opacity: 0.6, marginTop: "10px", maxWidth: "640px", marginX: "auto" }}>
                Der Tagesplan vergleicht zwei aufeinanderfolgende Läufe. Nach dem ersten Lauf
                gibt es noch nichts zu vergleichen; danach erscheinen hier neue Listungen,
                Preissenkungen, Marktwertsprünge, ablaufende Angebote und Manager, denen das
                Geld ausgeht.
            </Typography>
        </Box>
    )
}

function EventRow({ event }) {
    const severity = SEVERITIES[event.severity] || FALLBACK_SEVERITY
    const time = new Date(event.ts).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })

    return (
        <Box sx={{ display: "flex", alignItems: "baseline", gap: "10px", padding: "8px 0", flexWrap: "wrap" }}>
            <Chip label={severity.label} color={severity.colour} size="small" variant="filled" />
            <Typography variant="button" sx={{ opacity: 0.6, minWidth: "48px" }}>{time}</Typography>
            <Typography variant="body2" sx={{ opacity: 0.6, minWidth: "150px" }}>
                {TYPE_LABELS[event.type] || event.type}
            </Typography>
            <Typography variant="body1" sx={{ flex: "1 1 320px" }}>{event.text}</Typography>
        </Box>
    )
}

/**
 * The tab, fetching its own events.
 *
 * `events` stays a prop with the fetch as the default, so the tests hand in fixtures. A file
 * the scrape has not written yet needs no special case here: the hook hands back an empty list
 * for it, and EmptyState below already says exactly the right thing about that - the history
 * store needs two runs before there is anything to diff.
 */
export default function Tagesplan({ events, now = new Date() }) {
    // Nothing is fetched when the caller brought its own events
    const fetched = useJsonData(events === undefined ? DATASET : null)
    const rows = Array.isArray(events) ? events : (Array.isArray(fetched.data) ? fetched.data : [])

    if (events === undefined) {
        if (fetched.status === LOADING)
            return <DataLoading name={DATASET} />

        if (fetched.status === ERROR)
            return <DataError name={DATASET} error={fetched.error} onRetry={fetched.reload} />
    }

    if (rows.length === 0)
        return <EmptyState />

    return (
        <Box sx={{ padding: "0px 15px 15px 15px" }}>
            {groupByDay(rows, now).map((group) => (
                <Box key={group.key} sx={{ marginTop: "15px" }}>
                    <Typography variant="overline" sx={{ opacity: 0.7 }}>
                        {group.label} — {group.events.length === 1 ? "1 Ereignis" : `${group.events.length} Ereignisse`}
                    </Typography>
                    <Divider />
                    {group.events.map((event) => (
                        <EventRow key={event.key} event={event} />
                    ))}
                </Box>
            ))}
        </Box>
    )
}
