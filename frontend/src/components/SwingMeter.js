import { useEffect, useMemo, useState } from "react"
import Alert from "@mui/material/Alert"
import AlertTitle from "@mui/material/AlertTitle"
import Box from "@mui/material/Box"
import Chip from "@mui/material/Chip"
import Divider from "@mui/material/Divider"
import FormControl from "@mui/material/FormControl"
import InputLabel from "@mui/material/InputLabel"
import MenuItem from "@mui/material/MenuItem"
import Paper from "@mui/material/Paper"
import Select from "@mui/material/Select"
import Stack from "@mui/material/Stack"
import Tooltip from "@mui/material/Tooltip"
import Typography from "@mui/material/Typography"

import { getStatusIcon } from "./SharedConstants"
import { elapsedSince, formatDuration } from "./marketFormulas"
import {
    BEFORE,
    DONE,
    RUNNING,
    averagePointsPerPlayer,
    buildRoster,
    currentMatchDay,
    decomposeSwing,
    swingBounds,
    swingHeadline,
} from "./swingFormulas"

import { useDataRefresh, useJsonFiles } from "../hooks/useJsonData"
import { dataGate } from "./DataState"

// The squads to compare against the live points, and the kickoff windows the match day phase
// is read from. The age of the live data itself comes from the timestamp index, which the
// refresh context already holds - one document rather than a fourteenth request.
const TAKEN_PLAYERS = "taken_players.json"
const MATCH_DAYS = "match_days.json"
const DATASETS = [TAKEN_PLAYERS, MATCH_DAYS]

// The bars are static between scrapes, but the data age next to them is not: a "vor 3 Min."
// that stands still is worse than none.
const TICK_INTERVAL_MS = 30 * 1000

// How stale the live points may be before the meter stops presenting them as live. During
// a match day a quarter of an hour is a substitution and two goals.
const FRESH_MS = 15 * 60 * 1000
const STALE_MS = 60 * 60 * 1000

// The chosen own manager survives a reload, since it is the same one every match day
const OWN_MANAGER_KEY = "swingMeterOwnManager"

const pointsFormatter = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1, signDisplay: "exceptZero" })
const averageFormatter = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 })

const readStored = () => {
    try {
        return window.localStorage.getItem(OWN_MANAGER_KEY)
    } catch (error) {
        return null
    }
}

const store = (value) => {
    try {
        window.localStorage.setItem(OWN_MANAGER_KEY, value)
    } catch (error) {
        // A blocked storage is not worth a broken meter
    }
}

const phaseLabels = {
    [BEFORE]: "steht aus",
    [RUNNING]: "läuft",
    [DONE]: "beendet",
}

// One bar of the decomposition. The width is a share of the largest of the three parts, so
// the bars compare against each other rather than against an arbitrary maximum.
const SwingBar = ({ label, value, hint, color, scale }) => (
    <Box sx={{ marginBottom: "10px" }}>
        <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <Typography variant="body2">{label}</Typography>
            <Typography variant="body2" sx={{ fontWeight: "bold", whiteSpace: "nowrap", marginLeft: "12px" }}>
                {value === 0 ? "0 Punkte" : `${pointsFormatter.format(value)} Punkte`}
            </Typography>
        </Box>
        <Box sx={{ height: "10px", borderRadius: "5px", backgroundColor: "action.hover", overflow: "hidden" }}>
            <Box sx={{
                height: "100%",
                width: `${Math.min(100, (Math.abs(value) / scale) * 100)}%`,
                backgroundColor: color,
                borderRadius: "5px",
            }} />
        </Box>
        {hint && <Typography variant="caption" sx={{ color: "text.secondary" }}>{hint}</Typography>}
    </Box>
)

// Which of the open players are on the bench is not observable, only how many of them can
// still be fielded. The list therefore stays complete and says so, rather than dropping
// players it cannot name or counting ones that cannot play.
const benchNote = (open, fieldable) => {
    if (fieldable === open.length)
        return null

    if (fieldable === 0)
        return `Elf Spieler haben gepunktet – die ${open.length} offenen sitzen auf der Bank und bewegen nichts mehr.`

    return `Höchstens ${fieldable} von ${open.length} können noch aufgestellt sein – welche, steht nicht in den Daten.`
}

// One open player as a chip: name, plus the status icon where Kickbase says something about
// him. An injured player counts in the arithmetic like any other, because whether he is
// fielded is not observable either way — but it is worth seeing.
const PlayerChips = ({ players, fieldable, emptyLabel }) => {
    if (players.length === 0)
        return <Typography variant="caption" sx={{ color: "text.secondary" }}>{emptyLabel}</Typography>

    const note = benchNote(players, fieldable)

    return (
        <>
            <Stack direction="row" spacing={0.5} useFlexGap sx={{ flexWrap: "wrap" }}>
                {players.map((player) => {
                    const status = player.status === null || player.status === 0 ? null : getStatusIcon(player.status)

                    return (
                        <Chip
                            key={player.playerId}
                            size="small"
                            variant="outlined"
                            label={player.name}
                            sx={fieldable === 0 ? { opacity: 0.5 } : undefined}
                            icon={status
                                ? <Tooltip title={status.tooltip}><Box sx={{ display: "flex" }}>{status.icon}</Box></Tooltip>
                                : undefined}
                        />
                    )
                })}
            </Stack>
            {note && <Typography variant="caption" sx={{ color: "text.secondary" }}>{note}</Typography>}
        </>
    )
}

// How many of the open players can still matter, in the words the bars use. "Höchstens"
// wherever the lineup has less room than the squad has open players.
const differentialHint = (fieldable, open, subject, reference) => {
    if (fieldable === 0) {
        return open.length > 0
            ? `Keiner ${subject} kann noch punkten – ${open.length} offene sitzen auf der Bank.`
            : `Keiner ${subject} kann noch punkten.`
    }

    const who = fieldable === open.length
        ? `${fieldable} ${subject}`
        : `Höchstens ${fieldable} von ${open.length} ${subject}`
    const verb = fieldable === 1 ? "kann" : "können"

    return reference
        ? `${who} ${verb} noch punkten, hochgerechnet mit dem Spieltags-Ø.`
        : `${who} ${verb} noch punkten. Ein Richtwert je Spieler fehlt noch.`
}

/**
 * The live swing meter: what of the gap to a chosen rival is settled, and what is still on
 * the pitch.
 *
 * Deliberately without a starting eleven model. Which of a rival's owned players are
 * actually fielded is not in the API, so every open player is reported as a possibility —
 * hence "falls aufgestellt" on every part that has not been played yet.
 */
function SwingMeter({ entries, takenPlayers: takenProp, matchDays: matchDaysProp, livePointsStamp }) {
    const [now, setNow] = useState(() => Date.now())

    useEffect(() => {
        const timer = setInterval(() => setNow(Date.now()), TICK_INTERVAL_MS)
        return () => clearInterval(timer)
    }, [])

    // Fetched unless the caller brought both, which is what the tests do
    const brought = takenProp !== undefined && matchDaysProp !== undefined
    const fetched = useJsonFiles(useMemo(() => (brought ? [] : DATASETS), [brought]))
    const refresh = useDataRefresh()

    const takenPlayers = brought ? takenProp : fetched.data[TAKEN_PLAYERS]
    const matchDays = brought ? matchDaysProp : fetched.data[MATCH_DAYS]

    // The age of the live points, out of the timestamp index rather than out of a fourteenth
    // fetch of its own. `livePointsStamp` is the test seam.
    const timestampLivePoints = livePointsStamp ?? refresh.timestamps?.live_points ?? {}

    const managers = useMemo(
        () => [...new Set((entries || []).map((entry) => entry.userName))].sort((a, b) => a.localeCompare(b)),
        [entries])

    const [ownName, setOwnName] = useState(() => {
        const stored = readStored()
        return stored && managers.includes(stored) ? stored : managers[0]
    })
    const [rivalName, setRivalName] = useState(
        () => managers.find((manager) => manager !== ownName) ?? managers[0])

    // matchDays and takenPlayers arrive after the first render now, so both belong in the
    // dependency lists. Without them the meter would keep the empty first result forever.
    const matchDay = useMemo(() => currentMatchDay(matchDays, now), [matchDays, now])
    const phase = matchDay ? matchDay.phase : RUNNING

    const ownEntry = (entries || []).find((entry) => entry.userName === ownName)
    const rivalEntry = (entries || []).find((entry) => entry.userName === rivalName)

    const decomposition = useMemo(() => decomposeSwing({
        ownPlayers: buildRoster({ userName: ownName, livePlayers: ownEntry?.players, takenPlayers }),
        rivalPlayers: buildRoster({ userName: rivalName, livePlayers: rivalEntry?.players, takenPlayers }),
        phase,
    }), [ownName, rivalName, ownEntry, rivalEntry, phase, takenPlayers])

    const pointsPerPlayer = useMemo(() => averagePointsPerPlayer(entries, phase), [entries, phase])
    const bounds = swingBounds(decomposition, pointsPerPlayer)

    // Every hook above this line. The squads are what the decomposition needs; without them
    // every live player would count as a differential of his own.
    const gate = brought ? null : dataGate({
        name: TAKEN_PLAYERS,
        status: fetched.status,
        error: fetched.error,
        missing: fetched.missing.includes(TAKEN_PLAYERS),
        reload: fetched.reload,
        missingText: "Der Swing-Meter vergleicht die Kader zweier Manager. Die schreibt der "
            + "Schritt 'taken_free_players' eines Scrape-Laufs."
    })

    if (gate)
        return gate

    if (managers.length < 2) {
        return (
            <Alert severity="info" sx={{ marginBottom: "15px" }}>
                Für den Swing-Meter braucht es mindestens zwei Manager in den Live-Daten. Aktualisiere die
                Live-Punkte und lade die Seite neu.
            </Alert>
        )
    }

    const age = elapsedSince(timestampLivePoints.time, now)
    const ageLabel = formatDuration(age)
    const ageColor = age === null ? "default" : age <= FRESH_MS ? "success" : age <= STALE_MS ? "warning" : "error"

    const handleOwnChange = (event) => {
        const chosen = event.target.value
        setOwnName(chosen)
        store(chosen)
        if (chosen === rivalName)
            setRivalName(managers.find((manager) => manager !== chosen) ?? chosen)
    }

    // The parts share a scale so their bars can be read against each other
    const scale = Math.max(Math.abs(decomposition.gap), bounds.ownSwing ?? 0, bounds.rivalSwing ?? 0, 1)

    const fetchedAt = timestampLivePoints.time
        ? new Date(timestampLivePoints.time).toLocaleString("de-DE")
        : "unbekannt"

    const sharedNames = decomposition.shared
        .map((player) => player.points ? `${player.name} (${player.points})` : player.name)
        .join(", ")

    return (
        <Paper variant="outlined" sx={{ padding: "15px", marginBottom: "20px" }}>
            <Stack direction="row" spacing={2} sx={{ flexWrap: "wrap", alignItems: "center", marginBottom: "15px" }}>
                <FormControl size="small" sx={{ minWidth: "180px" }}>
                    <InputLabel id="swing-own-label">Dein Team</InputLabel>
                    <Select
                        labelId="swing-own-label"
                        label="Dein Team"
                        value={ownName}
                        onChange={handleOwnChange}
                    >
                        {managers.map((manager) => (
                            <MenuItem key={manager} value={manager}>{manager}</MenuItem>
                        ))}
                    </Select>
                </FormControl>

                <FormControl size="small" sx={{ minWidth: "180px" }}>
                    <InputLabel id="swing-rival-label">Rivale</InputLabel>
                    <Select
                        labelId="swing-rival-label"
                        label="Rivale"
                        value={rivalName}
                        onChange={(event) => setRivalName(event.target.value)}
                    >
                        {managers.filter((manager) => manager !== ownName).map((manager) => (
                            <MenuItem key={manager} value={manager}>{manager}</MenuItem>
                        ))}
                    </Select>
                </FormControl>

                {matchDay && (
                    <Chip
                        size="small"
                        variant="outlined"
                        label={`Spieltag ${matchDay.day} – ${phaseLabels[matchDay.phase]}`}
                    />
                )}

                <Tooltip title={`Letzter Live-Abruf: ${fetchedAt}. Die Live-Punkte werden nur beim Abruf aktualisiert, nicht laufend – der reguläre Scrape-Lauf holt sie nicht.`}>
                    <Chip
                        size="small"
                        color={ageColor}
                        variant="outlined"
                        label={ageLabel ? `Live-Daten: vor ${ageLabel}` : "Live-Daten: Alter unbekannt"}
                    />
                </Tooltip>
            </Stack>

            <Alert severity={decomposition.gap < 0 ? "warning" : decomposition.gap > 0 ? "success" : "info"}>
                <AlertTitle sx={{ marginBottom: 0 }}>{swingHeadline(decomposition, { rivalName })}</AlertTitle>
            </Alert>

            <Box sx={{ marginTop: "15px" }}>
                <SwingBar
                    label="Fix – beide Spieler haben gespielt"
                    value={decomposition.gap}
                    hint={`${decomposition.ownPlayed.points} Punkte aus ${decomposition.ownPlayed.count} Spielern gegen ${decomposition.rivalPlayed.points} aus ${decomposition.rivalPlayed.count}. Dieser Teil kann sich nicht mehr ändern.`}
                    color={decomposition.gap < 0 ? "error.main" : "success.main"}
                    scale={scale}
                />

                <SwingBar
                    label={`Geteilt, hebt sich auf (falls aufgestellt) – ${decomposition.shared.length} Spieler`}
                    value={0}
                    hint={decomposition.shared.length === 0
                        ? "In Kickbase gehört ein Spieler nur einem Manager, deshalb ist dieser Teil normalerweise leer."
                        : `Auf beiden Kadern: ${sharedNames}. Weder Punkte noch Potenzial zählen, weil nicht feststeht, wem sie gehören.`}
                    color="text.disabled"
                    scale={scale}
                />

                <SwingBar
                    label={`Differential, läuft noch (falls aufgestellt) – ${decomposition.ownFieldable} für dich`}
                    value={bounds.ownSwing ?? 0}
                    hint={differentialHint(
                        decomposition.ownFieldable, decomposition.ownOpen, "deiner Spieler", bounds.ownSwing !== null)}
                    color="success.main"
                    scale={scale}
                />

                <SwingBar
                    label={`Differential, läuft noch (falls aufgestellt) – ${decomposition.rivalFieldable} für ${rivalName}`}
                    value={bounds.rivalSwing === null ? 0 : -bounds.rivalSwing}
                    hint={differentialHint(
                        decomposition.rivalFieldable, decomposition.rivalOpen, `der Spieler von ${rivalName}`, bounds.rivalSwing !== null)}
                    color="error.main"
                    scale={scale}
                />
            </Box>

            <Divider sx={{ margin: "10px 0" }} />

            <Typography variant="body2">
                {bounds.ceiling === null
                    ? "Für eine Spanne haben noch zu wenige Spieler gepunktet."
                    : bounds.ceiling === bounds.floor
                        // Both sides have their eleven behind them: a "von +25 bis +25" would
                        // read as a range where there is none
                        ? "Der Abstand steht fest – es kann sich nichts mehr bewegen."
                        : `Spanne: von ${pointsFormatter.format(bounds.floor)} bis ${pointsFormatter.format(bounds.ceiling)} Punkten, wenn jeder noch offene Spieler den Spieltags-Ø von ${averageFormatter.format(pointsPerPlayer)} Punkten holt.`}
            </Typography>

            <Box sx={{ marginTop: "12px" }}>
                <Typography variant="subtitle2">Deine offenen Spieler</Typography>
                <PlayerChips
                    players={decomposition.ownOpen}
                    fieldable={decomposition.ownFieldable}
                    emptyLabel="Keiner deiner Spieler spielt noch."
                />
            </Box>

            <Box sx={{ marginTop: "12px" }}>
                <Typography variant="subtitle2">{rivalName}: offene Spieler (falls aufgestellt)</Typography>
                <PlayerChips
                    players={decomposition.rivalOpen}
                    fieldable={decomposition.rivalFieldable}
                    emptyLabel={`Kein Spieler von ${rivalName} spielt noch.`}
                />
            </Box>

            <Typography variant="caption" component="div" sx={{ color: "text.secondary", marginTop: "12px" }}>
                Ehrlichkeitshinweise: Die Aufstellung des Rivalen steht nicht in der API – offene Spieler zählen nur,
                falls sie aufgestellt sind, und ein Manager stellt höchstens elf auf. Haben schon elf gepunktet, sitzt
                der Rest auf der Bank und bewegt nichts mehr – welche Spieler das sind, ist nicht zu erkennen. Ein
                Spieler ohne Punkte gilt als „spielt noch“; wer gespielt und 0 Punkte geholt hat, ist in den Daten nicht
                davon zu unterscheiden. Spanne und Balken rechnen mit dem Spieltags-Durchschnitt, nicht mit
                Wahrscheinlichkeiten.
            </Typography>
        </Paper>
    )
}

export default SwingMeter
