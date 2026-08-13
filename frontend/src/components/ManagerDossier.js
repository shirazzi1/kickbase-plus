import Alert from "@mui/material/Alert"
import Chip from "@mui/material/Chip"
import Grid from "@mui/material/Grid"
import Paper from "@mui/material/Paper"
import Stack from "@mui/material/Stack"
import Tooltip from "@mui/material/Tooltip"
import Typography from "@mui/material/Typography"
import { Box, alpha, useTheme } from "@mui/material"

import { percentFormatter, unsignedPercentFormatter } from "./SharedConstants"
import { formatDuration } from "./marketFormulas"
import { PROFILES_DATASET, coverageNote, managerProfileList } from "./managerProfiles"
import { ERROR, LOADING, useJsonData } from "../hooks/useJsonData"
import { DataError, DataLoading } from "./DataState"

// What the whole tab rests on, in one sentence. Kickbase's feed shows completed bookings
// only - never a lost bid, never a counter-bid - so every number here is a habit read off
// what a manager actually did, not a prediction of what they will do.
export const DOSSIER_NOTE = "Alles hier ist aus abgeschlossenen Transfers abgeleitet. Kickbase "
    + "zeigt keine verlorenen Gebote, also steht hier nur, was beobachtbar ist - und zu jeder "
    + "Kennzahl, auf wie vielen Transfers sie beruht."

// The words for "there is no data", used wherever a metric's n is zero. A zero in its place
// would read as a measurement: "0 % Aufschlag" means the manager pays exactly the market
// value, which is a very different claim from "no purchase could be checked".
const NO_DATA = "keine Datenlage"

/**
 * A median hold duration, in the unit it is readable in.
 *
 * The durations in this data span five orders of magnitude - from a fourteen second round
 * trip through the market to half a season - so the sub-minute end gets seconds. Without it
 * the busiest managers all read "0 Min.", which looks like a missing value next to an n that
 * says there were six sales.
 */
export function formatHold(seconds) {
    if (seconds === null || seconds === undefined)
        return null

    if (seconds < 60)
        return `${seconds} Sek.`

    return formatDuration(seconds * 1000)
}

/**
 * One metric: its name, its value, and what it is based on.
 *
 * The basis line is not decoration. With ten to fourteen managers and a few dozen transfers
 * each, most of these metrics are thin, and the n is the only thing that says how far to
 * trust the number above it.
 */
const Metric = ({ label, value, basis, hint }) => {
    const missing = value === null || value === undefined

    const heading = (
        <Typography variant="caption" sx={{ opacity: 0.6, display: "block" }}>
            {label}
        </Typography>
    )

    return (
        <Box sx={{ marginBottom: "12px" }}>
            {hint ? <Tooltip title={hint} arrow><Box sx={{ cursor: "help" }}>{heading}</Box></Tooltip> : heading}

            {/* A div, not the default paragraph: one of these values is a row of chips, and
                a div inside a p is invalid markup React complains about */}
            <Typography
                variant="body1"
                component="div"
                sx={{ fontWeight: missing ? "normal" : "bold", opacity: missing ? 0.6 : 1 }}
            >
                {missing ? NO_DATA : value}
            </Typography>

            {basis && (
                <Typography variant="caption" sx={{ opacity: 0.6, display: "block" }}>
                    {basis}
                </Typography>
            )}
        </Box>
    )
}

/**
 * When a manager trades, as 24 bars - one per hour of the day.
 *
 * The peak hour is the one worth finding at a glance: it is when this manager is at the app,
 * and therefore when a listing of theirs gets cut and a listing of yours gets seen.
 */
const ActivityBar = ({ window: activity }) => {
    const theme = useTheme()

    const counts = activity?.hourCounts ?? []
    const busiest = Math.max(1, ...counts)

    return (
        <Box>
            <Box sx={{ display: "flex", alignItems: "flex-end", gap: "2px", height: "44px" }}>
                {counts.map((count, hour) => {
                    const isPeak = hour === activity.peakHour

                    return (
                        <Tooltip
                            key={hour}
                            arrow
                            title={`${hour}:00 - ${count} ${count === 1 ? "Buchung" : "Buchungen"}`}
                        >
                            <Box sx={{ flex: 1, display: "flex", alignItems: "flex-end", height: "100%" }}>
                                <Box
                                    sx={{
                                        width: "100%",
                                        // An hour without a booking keeps a hairline, so the
                                        // 24 hour grid stays readable as a grid
                                        height: count === 0 ? "2px" : `${Math.max(8, (count / busiest) * 100)}%`,
                                        borderRadius: "2px",
                                        backgroundColor: count === 0
                                            ? alpha(theme.palette.text.primary, 0.15)
                                            : isPeak
                                                ? theme.palette.warning.main
                                                : alpha(theme.palette.primary.main, 0.6)
                                    }}
                                />
                            </Box>
                        </Tooltip>
                    )
                })}
            </Box>

            <Box sx={{ display: "flex", justifyContent: "space-between", marginTop: "2px" }}>
                {[0, 6, 12, 18, 23].map((hour) => (
                    <Typography key={hour} variant="caption" sx={{ opacity: 0.5 }}>
                        {hour}
                    </Typography>
                ))}
            </Box>
        </Box>
    )
}

/**
 * One manager's fingerprint: the four metrics, the round trips, and the activity window.
 */
const ManagerCard = ({ profile }) => {
    const hold = profile.holdDuration ?? {}
    const markup = profile.purchaseMarkup ?? {}
    const momentum = profile.momentumBuys ?? {}
    const clubs = profile.topClubs ?? {}
    const activity = profile.activityWindow ?? {}

    const roundTrips = hold.roundTripsWithinAnHour ?? 0

    return (
        <Paper variant="outlined" sx={{ padding: "12px 16px", height: "100%" }}>
            <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ marginBottom: "8px" }}>
                <Typography variant="h6">{profile.managerName ?? `Manager ${profile.managerId}`}</Typography>

                {/* Buying a player off the market and selling them straight back earns a
                    trade towards Kickbase's transfer bonus and costs nothing. It is the one
                    behaviour in this file that is a tactic rather than a taste, so it is
                    named as one instead of being hidden inside the median it drags down. */}
                {roundTrips > 0 && (
                    <Tooltip
                        arrow
                        title={"Spieler vom Markt gekauft und binnen einer Stunde zurück an den "
                            + "Markt verkauft. Das zählt Transfers für den Trade-Bonus, ist also "
                            + "Bonus-Farming - und zieht die Haltedauer links nach unten, denn "
                            + "diese Verkäufe stecken in ihr."}
                    >
                        <Chip
                            label={`${roundTrips} ${roundTrips === 1 ? "Rundlauf" : "Rundläufe"} - Bonus-Farming`}
                            size="small"
                            color="warning"
                            variant="outlined"
                        />
                    </Tooltip>
                )}
            </Stack>

            <Grid container spacing={2}>
                <Grid item xs={6}>
                    <Metric
                        label="Haltedauer (Median)"
                        value={formatHold(hold.medianSeconds)}
                        basis={hold.n ? `aus ${hold.n} ${hold.n === 1 ? "Verkauf" : "Verkäufen"}` : "kein Verkauf erfasst"}
                        hint={"Median über alle Käufe, die auch wieder verkauft wurden. Spieler, die "
                            + "zum Saisonstart zugeteilt wurden, zählen nicht mit - ihr Kaufdatum ist "
                            + "der Saisonstart, nicht eine Entscheidung."}
                    />
                </Grid>

                <Grid item xs={6}>
                    <Metric
                        label="Aufschlag beim Kauf (Ø)"
                        value={markup.meanPercent === null || markup.meanPercent === undefined
                            ? null
                            : percentFormatter.format(markup.meanPercent / 100)}
                        basis={markup.n
                            ? `aus ${markup.n} von ${markup.buysConsidered ?? markup.n} Käufen`
                            + (markup.medianPercent === null || markup.medianPercent === undefined
                                ? ""
                                : `, Median ${percentFormatter.format(markup.medianPercent / 100)}`)
                            : `0 von ${markup.buysConsidered ?? 0} Käufen prüfbar`}
                        hint={"Gezahlter Preis gegen den Marktwert des Spielers am Kauftag. Nur Käufe, "
                            + "für die die Marktwert-Kurve den Tag hergibt - der Rest bleibt "
                            + "ungezählt statt geschätzt."}
                    />
                </Grid>

                <Grid item xs={6}>
                    <Metric
                        label="Käufe in den Aufwärtstrend"
                        value={momentum.share === null || momentum.share === undefined
                            ? null
                            : unsignedPercentFormatter.format(momentum.share)}
                        basis={momentum.n
                            ? `${momentum.risingBuys} von ${momentum.n} Käufen, `
                            + `${momentum.windowDays ?? 7}-Tage-Fenster`
                            : "kein Kauf mit Kurve prüfbar"}
                        hint={"Anteil der Käufe, bei denen der Marktwert am Kauftag über dem Wert "
                            + "eine Woche davor lag. Braucht beide Tage in der Kurve, sonst zählt der "
                            + "Kauf weder als Trend noch als Gegenteil."}
                    />
                </Grid>

                <Grid item xs={6}>
                    <Metric
                        label="Lieblingsklubs"
                        value={(clubs.clubs ?? []).length === 0
                            ? null
                            : (
                                <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                                    {clubs.clubs.map((club) => (
                                        <Chip
                                            key={club.teamId}
                                            size="small"
                                            variant="outlined"
                                            label={`${club.teamName ?? `Team ${club.teamId}`} (${club.buys})`}
                                        />
                                    ))}
                                </Stack>
                            )}
                        basis={clubs.n ? `aus ${clubs.n} Käufen mit Klub-Angabe` : "kein Kauf mit Klub-Angabe"}
                        hint="Die Klubs, aus denen dieser Manager am häufigsten gekauft hat - höchstens drei."
                    />
                </Grid>

                <Grid item xs={12}>
                    <Metric
                        label="Aktivitätsfenster"
                        value={activity.n
                            ? `aktivste Stunde ${activity.peakHour}:00`
                            : null}
                        basis={activity.n
                            ? `aus ${activity.n} Buchungen, Zeitzone ${activity.timezone ?? "unbekannt"}`
                            : "keine Buchung erfasst"}
                        hint={"Kauf- und Verkaufszeitpunkte über den Tag verteilt. Sagt, wann dieser "
                            + "Manager an der App ist - und damit, wann ein Gebot von dir vermutlich "
                            + "unbeantwortet bleibt."}
                    />

                    {activity.n > 0 && <ActivityBar window={activity} />}
                </Grid>
            </Grid>
        </Paper>
    )
}

/**
 * The dossier tab: one card per manager in the league.
 *
 * Fetches manager_profiles.json, which the last stage of a scrape run writes. Until that stage
 * has run once the file is not there at all, which the backend answers as a 404 and the hook
 * turns into an empty document - the info box below is what that looks like.
 *
 * `profiles` stays a prop so the tests can hand in fixtures; nothing is fetched then.
 */
function ManagerDossier({ profiles }) {
    const fetched = useJsonData(profiles === undefined ? PROFILES_DATASET : null)
    const document = profiles === undefined ? fetched.data : profiles

    const managers = managerProfileList(document)
    const note = coverageNote(document)

    if (profiles === undefined) {
        if (fetched.status === LOADING)
            return <DataLoading name={PROFILES_DATASET} />

        if (fetched.status === ERROR)
            return <DataError name={PROFILES_DATASET} error={fetched.error} onRetry={fetched.reload} />
    }

    // The backend writes an entry per manager even for one who has never traded, so an empty
    // list means the file is missing or unreadable - never a quiet league
    if (managers.length === 0)
        return (
            <Alert severity="info" sx={{ margin: "0 15px 15px" }}>
                Noch keine Manager-Profile vorhanden. Die schreibt die Stage 'manager_profiles'
                am Ende eines Scrape-Laufs nach manager_profiles.json - bis dahin bleibt dieser
                Tab leer. Der Rest der App ist davon nicht betroffen.
            </Alert>
        )

    return (
        <Box sx={{ padding: "0 15px 15px" }}>
            <Typography variant="body2" sx={{ opacity: 0.7, marginBottom: "10px" }}>
                {DOSSIER_NOTE}
            </Typography>

            {/* Two metrics per card need a market value curve that this data does not fetch
                itself. When the stage that does failed, they are empty for everyone - and
                without this line that looks like a league that never buys anything. */}
            {note && (
                <Alert severity={note.severity} sx={{ marginBottom: "12px" }}>
                    {note.text}
                </Alert>
            )}

            <Grid container spacing={2}>
                {managers.map((profile) => (
                    <Grid item xs={12} md={6} key={profile.managerId ?? profile.managerName}>
                        <ManagerCard profile={profile} />
                    </Grid>
                ))}
            </Grid>
        </Box>
    )
}

export default ManagerDossier
