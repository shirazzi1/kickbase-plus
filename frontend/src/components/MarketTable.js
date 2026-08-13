import { useEffect, useState } from "react"
import Tooltip from "@mui/material/Tooltip"
import Typography from "@mui/material/Typography"
import Chip from "@mui/material/Chip"
import {
    Box, Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle,
    Snackbar, Alert, alpha, useTheme
} from "@mui/material"
import PagedDataGrid from "./PagedDataGrid"
import BidCell from "./BidCell"
import {
    percentFormatter,
    currencyFormatter,
    unsignedPercentFormatter,
    currencyOrDash,
    percentOrDash,
    deltaCellClassName,
    deltaColumnStyles,
    getStatusIcon
} from "./SharedConstants"
import {
    BEP_DEFAULTS,
    CONFIG_DATASET,
    relativeChange,
    daysToBreakEven,
    breakEvenBid,
    formatDuration,
    elapsedSince,
    ownManager,
    minWinningBid,
    forcedSaleRisk
} from "./marketFormulas"
import ManagerStacks, { ESTIMATE_NOTE } from "./ManagerStacks"
import {
    PROFILES_DATASET,
    bidderChipLabel,
    likelyBidders,
    managerProfileList
} from "./managerProfiles"
import { useJsonFiles } from "../hooks/useJsonData"
import { bidTokenHeader } from "./bidToken"
import { dataGate } from "./DataState"

// The four datasets this table joins. The listings are the table; the balances feed the
// auction solver's three columns; the profiles feed the bidder chip; config carries the
// break-even horizons a run was configured with. One hook call, so the table renders once
// rather than four times.
const MARKET = "market.json"
const BALANCES = "balances.json"
const DATASETS = [MARKET, BALANCES, PROFILES_DATASET, CONFIG_DATASET]

const daysFormatter = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 })

// How often the listing age and the remaining time are recalculated. The data itself only
// changes when the scraper runs, but a countdown that stands still is worse than none.
const TICK_INTERVAL_MS = 30 * 1000

// A span in milliseconds, or "–" when there is nothing to say
const durationOrDash = ({ value }) => formatDuration(value) ?? "–"

// Rows without a span sort last rather than first, so ascending order puts the listings
// that actually run out on top instead of burying them
const missingSortsLast = (a, b) => {
    const rank = (value) => value === null || value === undefined ? Infinity : value
    return rank(a) - rank(b)
}

// Every market value move gets two columns side by side: the euro amount and the same
// move as a share of the current market value, so a 100.000 € day on a cheap player can
// be told apart from one on an expensive player, and sorted for.
const changeColumns = (field, label, width) => [
    {
        field,
        headerName: `${label} €`,
        type: "number",
        width,
        valueFormatter: currencyOrDash,
        headerAlign: "center",
        cellClassName: deltaCellClassName
    },
    {
        field: `${field}Percent`,
        headerName: `${label} %`,
        type: "number",
        width: 100,
        valueFormatter: percentOrDash,
        headerAlign: "center",
        cellClassName: deltaCellClassName
    }
]

// The three solver columns all rest on the reconstructed budgets, so each header says so
const SOLVER_NOTE = "Beruht auf den geschätzten Budgets aus balances.json, nicht auf Daten von Kickbase."

// How the forced sale score is rendered. The wording is the claim, and the claim is only
// as strong as the score: "droht" for the top band, plain pressure below it.
const DISTRESS_LABELS = {
    high: { label: "Zwangsverkauf droht", color: "error" },
    watch: { label: "unter Druck", color: "warning" }
}

function MarketTable() {
    const theme = useTheme()

    const { status, data, missing, error: fetchError, reload } = useJsonFiles(DATASETS)

    const listings = data[MARKET]
    const balances = data[BALANCES]

    // The manager fingerprints behind the "Wahrscheinliche Mitbieter" column. Empty until a
    // scrape has written manager_profiles.json, and the column is then left out entirely
    // rather than shown empty - an empty cell there would claim nobody wants the player.
    //
    // A document with no managers in it counts as no document, the same way the dossier tab
    // reads it: the backend writes an entry per league member, so an empty one is a file that
    // cannot answer anything rather than a league that has not traded.
    const profiles = data[PROFILES_DATASET]
    const hasProfiles = managerProfileList(profiles).length > 0

    // The break-even horizons a run was configured with. Merged over the backend's own
    // defaults, so the suggested bid is computed against 3 days rather than against
    // `undefined` in the moment before the file lands.
    const config = { ...BEP_DEFAULTS, ...(data[CONFIG_DATASET] ?? {}) }

    // The editing state lives here rather than in the cell: renderCell re-runs on every
    // scroll and every sort, and state held inside a cell would not survive either.
    const [edit, setEdit] = useState(null)          // { playerId, draft }
    const [pendingId, setPendingId] = useState(null)
    // Confirmed bids, keyed by player. The backend patches market.json after a confirmed
    // bid, but this page will not refetch it until the next run changes the run id - so this
    // is what shows a bid in the meantime.
    const [bids, setBids] = useState({})
    const [error, setError] = useState(null)
    const [confirming, setConfirming] = useState(null)   // { playerId, price, usedSuggestion }

    const closeEdit = () => setEdit(null)

    // The suggestion is the honest yardstick for a typo: one digit too many is always a
    // factor of ten, so twice the suggestion catches it on a cheap player as well as on
    // an expensive one, which a fixed euro threshold does not.
    const needsConfirmation = (row, price) => {
        const reference = row.suggestedBid || row.marketValue
        return Boolean(reference) && price >= 2 * reference
    }

    // Shown whenever the failure did not come from our own Flask API - a thrown fetch, or
    // a non-OK response with no parseable JSON "error" (the dev-server proxy answers a
    // plain-text 500 when Flask is not running at all, which is the common local setup)
    const apiUnreachable = "Die Flask-API ist nicht erreichbar. Läuft app.py?"

    const send = async (playerId, price) => {
        setPendingId(playerId)
        setConfirming(null)

        try {
            const response = await fetch(`/api/market/${playerId}/bid`, {
                method: price === null ? "DELETE" : "POST",
                headers: { "Content-Type": "application/json", ...bidTokenHeader() },
                body: price === null ? undefined : JSON.stringify({ price })
            })
            const body = await response.json().catch(() => ({}))

            if (!response.ok) {
                // Our own endpoints always answer a failure as JSON carrying "error" - a
                // finished German sentence, shown verbatim. Anything else (no body.error)
                // did not come from Flask at all, so it gets the same message as a thrown
                // fetch rather than a raw HTTP status nobody asked for.
                setError(body.error || apiUnreachable)
                return
            }

            // What Kickbase confirmed, not what was typed
            setBids((current) => ({ ...current, [playerId]: body.ownBid }))
            // Scoped to this row: a response for A must never discard a draft the user has
            // since started on B. Without this check, a late A closing the shared `edit`
            // state would silently wipe out whatever the user is now typing on B.
            setEdit((current) => current?.playerId === playerId ? null : current)
        } catch (e) {
            // A network failure rather than an HTTP status: naming the cause beats
            // "Gebot fehlgeschlagen", which would send you looking at Kickbase
            setError(apiUnreachable)
        } finally {
            // Same scoping as above, so a late response for A cannot clear a pending
            // indicator that by now belongs to a different row
            setPendingId((current) => current === playerId ? null : current)
        }
    }

    const submit = (row) => {
        const price = Number(edit.draft)
        if (!price)
            return

        if (needsConfirmation(row, price))
            // Recorded here rather than re-derived in the dialog: by the time it renders,
            // row.suggestedBid says nothing about which reference the check just used.
            setConfirming({ playerId: row.playerId, price, usedSuggestion: Boolean(row.suggestedBid) })
        else
            send(row.playerId, price)
    }

    // Who "you" are, so the user is left out of their own rival set and a suggested bid is
    // capped at their own budget. Null until a scrape has written the flag.
    const me = ownManager(balances)

    // The listing age and the remaining time are the only columns that move on their own,
    // so the clock they are measured against is the component's state
    const [now, setNow] = useState(() => Date.now())

    useEffect(() => {
        const timer = setInterval(() => setNow(Date.now()), TICK_INTERVAL_MS)
        return () => clearInterval(timer)
    }, [])

    // Define the columns of the table
    const columns = [
        {
            field: "teamLogo",
            headerName: "Team",
            width: 60,
            headerAlign: "center",
            align: "center",
            sortable: false,
            renderCell: (params) => (
                <img
                    src={params.value}
                    alt={params.value}
                    width='40'
                    onError={(e) => {
                        e.target.onerror = null // Prevent infinite loop if default.png is also missing
                        e.target.src = process.env.PUBLIC_URL + '/images/default.png'
                    }}
                />
            )
        },
        {
            field: "position",
            headerName: "Position",
            width: 80,
            headerAlign: "center",
            align: "center"
        },
        {
            field: "player",
            headerName: "Spieler",
            width: 200,
            headerAlign: "center",
            align: "center"
        },
        {
            field: "status",
            headerName: "Status",
            width: 70,
            headerAlign: "center",
            align: "center",
            renderCell: (params) => {
                const { icon, tooltip } = getStatusIcon(params.value)

                // The note from the player profile, e.g. "Muscle problems - out for weeks".
                // Only injured and doubtful players have one.
                const title = params.row.statusText
                    ? <>{tooltip}<br />{params.row.statusText}</>
                    : tooltip

                return <Tooltip title={title} arrow>{icon}</Tooltip>
            }
        },
        {
            field: "marketValue",
            headerName: "Marktwert",
            type: "number",
            width: 120,
            valueFormatter: currencyOrDash,
            headerAlign: "center",
            cellClassName: "font-tabular-nums"
        },
        {
            field: "price",
            headerName: "Preis",
            type: "number",
            width: 120,
            valueFormatter: currencyOrDash,
            headerAlign: "center",
            cellClassName: "font-tabular-nums"
        },
        {
            field: "minBid",
            headerName: "Mindestgebot",
            type: "number",
            width: 165,
            headerAlign: "center",
            align: "right",
            cellClassName: "font-tabular-nums",
            description: "Das kleinste Gebot, das jeden Manager sicher überbietet, der sich den "
                + "Preis leisten könnte - also einen Euro über dem höchsten fremden Maximalgebot, "
                + "mindestens aber der Preis. Eine Obergrenze, keine Preisprognose: wenn alle "
                + "mitbieten können, ist das das ganze Budget des reichsten Rivalen. "
                + SOLVER_NOTE,
            renderCell: (params) => {
                const solved = params.row.solved

                // On your own listing there is no bid to place, so there is no number to
                // show either. The 'Verdeckte Bieter' column still names who could buy.
                if (solved.isOwnListing)
                    return (
                        <Tooltip title="Deine eigene Listung - auf eigene Spieler kann nicht geboten werden." arrow>
                            <span style={{ opacity: 0.6 }}>eigene Listung</span>
                        </Tooltip>
                    )

                if (params.value === null || params.value === undefined)
                    return "–"

                // Who the bid has to beat, so the number reads as what it is instead of as
                // a forecast. Kickbase never says who is bidding, so this is the set that
                // could - not the set that does.
                //
                // Three reasons for an empty rival set, and they say different things. The
                // third one used to be unreachable: balances.json was a compile-time import,
                // so an absent one failed the build. It is fetched now, and a deployment whose
                // balances stage has not run yet reaches exactly this line - which read
                // rivals[0] unconditionally and took the whole tab down with it.
                const others = solved.rivals.length - 1
                const beats = solved.isPhantom
                    ? "Kein anderer Manager kann den Preis zahlen, der Preis selbst genügt."
                    : solved.rivals.length === 0
                        ? "Keine Budgetdaten, also ist das hier nur der Preis selbst."
                        : `Übertrifft ${solved.rivals[0].username} `
                            + `(max. ${currencyFormatter.format(solved.rivals[0].maxBid)})`
                            + (others > 0 ? ` und ${others} weitere.` : ".")

                const capped = solved.exceedsBudget
                    ? ` Nötig wären ${currencyFormatter.format(solved.required)} - `
                        + `dein geschätztes Maximum sind ${currencyFormatter.format(solved.ownMaxBid)}.`
                    : ""

                // Deliberately not red. On the real market 79 of 86 rows sit above the own
                // ceiling, because outbidding the richest manager is out of reach almost
                // everywhere - and a column that is red nine rows in ten reads as "you can
                // buy nothing", which is the opposite of true. The marker says what the
                // number is instead: your own limit, not the bid that wins.
                return (
                    <Tooltip title={`${beats}${capped} ${ESTIMATE_NOTE}`} arrow>
                        <span>
                            {currencyFormatter.format(params.value)}
                            {(solved.exceedsBudget || solved.isPhantom) && (
                                <Typography component="span" variant="body2" sx={{ opacity: 0.6, marginLeft: "6px" }}>
                                    {solved.exceedsBudget ? "dein Max." : "Phantom"}
                                </Typography>
                            )}
                        </span>
                    </Tooltip>
                )
            },
            sortComparator: missingSortsLast
        },
        {
            field: "markup",
            headerName: "Aufpreis",
            type: "number",
            width: 100,
            headerAlign: "center",
            // Red for paying above the market value, green for below. The formatter
            // supplies the sign, so these classes must not be the delta ones, whose CSS
            // prepends a "+" of its own.
            cellClassName: ({ value }) => {
                if (value === null || value === undefined)
                    return "font-tabular-nums"
                else if (value > 0)
                    return ["font-tabular-nums", "markup-over"]
                else if (value < 0)
                    return ["font-tabular-nums", "markup-under"]
                else
                    return "font-tabular-nums"
            },
            valueFormatter: ({ value }) =>
                value === null || value === undefined ? "–" : percentFormatter.format(value)
        },
        {
            field: "daysToBep",
            headerName: "Tage bis BEP",
            type: "number",
            // Wide enough for the header to survive the sort arrow next to it
            width: 155,
            headerAlign: "center",
            cellClassName: "font-tabular-nums",
            valueFormatter: ({ value }) =>
                value === null || value === undefined ? "–" : daysFormatter.format(value),
            // Players who never break even sort last rather than first, so ascending order
            // puts the ones that pay for themselves quickest on top
            sortComparator: (a, b) => {
                const rank = (value) => value === null || value === undefined ? Infinity : value
                const [first, second] = [rank(a), rank(b)]
                return first === second ? 0 : first - second
            }
        },
        {
            field: "ownBid",
            headerName: "Dein Gebot",
            type: "number",
            width: 175,
            headerAlign: "center",
            align: "right",
            cellClassName: "font-tabular-nums",
            renderCell: (params) => (
                <BidCell
                    row={params.row}
                    growthDays={config.bepGrowthDays}
                    targetDays={config.bepTargetDays}
                    editing={edit?.playerId === params.row.playerId}
                    draft={edit?.playerId === params.row.playerId ? edit.draft : ""}
                    pending={pendingId === params.row.playerId}
                    onEdit={() => {
                        // One bid in flight at a time: starting a different edit while a
                        // request is pending is what let a late response for row A land on
                        // whatever row B had become in the meantime
                        if (pendingId !== null)
                            return

                        setEdit({
                            playerId: params.row.playerId,
                            // The running bid if there is one, else the suggestion, else empty
                            draft: String(params.row.ownBid ?? params.row.suggestedBid ?? "")
                        })
                    }}
                    onDraftChange={(draft) => setEdit((current) => ({ ...current, draft }))}
                    onSubmit={() => submit(params.row)}
                    onWithdraw={() => send(params.row.playerId, null)}
                    onCancel={closeEdit}
                />
            )
        },
        ...changeColumns("today", "Heute", 110),
        ...changeColumns("yesterday", "Gestern", 110),
        ...changeColumns("twoDays", "Vorgestern", 120),
        ...changeColumns("sevenDays", "7 Tage", 110),
        ...changeColumns("thirtyDays", "30 Tage", 120),
        {
            field: "offerCount",
            headerName: "Gebote",
            type: "number",
            width: 90,
            headerAlign: "center",
            align: "right",
            cellClassName: "font-tabular-nums",
            // Kickbase only says how many bids there are, never whose
            valueFormatter: ({ value }) =>
                value === null || value === undefined ? "–" : value
        },
        {
            field: "hiddenBidders",
            headerName: "Verdeckte Bieter",
            type: "number",
            width: 165,
            headerAlign: "center",
            align: "right",
            cellClassName: "font-tabular-nums",
            description: "Kickbase verrät nur, wie viele Gebote es gibt, nie von wem. Das hier "
                + "sind die Manager, die den Preis überhaupt zahlen könnten - der Verkäufer und "
                + "du selbst ausgenommen. " + SOLVER_NOTE,
            renderCell: (params) => {
                const solved = params.row.solved
                const rivals = solved.rivals

                if (rivals.length === 0) {
                    // Three different reasons for a zero, and only one of them is a claim
                    // about the league
                    const why = solved.isOwnListing
                        ? "Niemand in der Liga kann deinen Preis zahlen."
                        : solved.isPhantom
                            ? "Niemand außer dir kann den Preis zahlen."
                            : "Keine Budgetdaten, also keine Aussage."

                    return (
                        <Tooltip title={`${why} ${ESTIMATE_NOTE}`} arrow>
                            <span>0</span>
                        </Tooltip>
                    )
                }

                const who = rivals
                    .map((rival) => `${rival.username} (max. ${currencyFormatter.format(rival.maxBid)})`)
                    .join(", ")

                // On your own listing the same set is the list of possible buyers
                const lead = solved.isOwnListing ? "Mögliche Käufer: " : ""

                return (
                    <Tooltip title={`${lead}${who}. ${ESTIMATE_NOTE}`} arrow>
                        <span>{rivals.length}</span>
                    </Tooltip>
                )
            }
        },
        // Only when there are fingerprints to match against. Without them the column would
        // be a row of dashes that reads as "nobody is interested in these players".
        ...(hasProfiles ? [{
            field: "likelyBidders",
            headerName: "Wahrscheinliche Mitbieter",
            type: "number",
            width: 230,
            headerAlign: "center",
            align: "center",
            description: "Die Manager, die den Preis zahlen könnten und deren bisherige Käufe zu "
                + "diesem Spieler passen: sie kaufen öfter bei seinem Klub, oder sie kaufen "
                + "überwiegend in steigende Marktwerte und dieser steigt gerade. Beobachtetes "
                + "Verhalten, keine Gebote - Kickbase zeigt nie, wer bietet. " + SOLVER_NOTE,
            renderCell: (params) => {
                const bidders = params.row.likelyBidderDetail

                // No match is not the same as no data, and neither is worth a chip: the
                // 'Verdeckte Bieter' column next door already says who could pay.
                if (bidders.length === 0)
                    return <span style={{ opacity: 0.4 }}>–</span>

                const lead = params.row.solved.isOwnListing
                    ? "Passt zum Beuteschema möglicher Käufer: "
                    : "Passt zum Beuteschema von: "

                const why = bidders
                    .map((bidder) => `${bidder.username} (${bidder.reasons.map((reason) => reason.text).join("; ")})`)
                    .join(" — ")

                return (
                    <Tooltip title={`${lead}${why}. ${ESTIMATE_NOTE}`} arrow>
                        <Chip
                            label={bidderChipLabel(bidders)}
                            size="small"
                            color="info"
                            variant="outlined"
                        />
                    </Tooltip>
                )
            },
            sortComparator: missingSortsLast
        }] : []),
        {
            field: "seller",
            headerName: "Verkäufer",
            flex: 1,
            minWidth: 110,
            headerAlign: "center",
            align: "center"
        },
        {
            field: "distress",
            headerName: "Zwangsverkauf droht",
            type: "number",
            width: 200,
            headerAlign: "center",
            align: "center",
            description: "Wie sehr der Verkäufer verkaufen muss: wie viel von seinem erlaubten "
                + "Dispo schon verbraucht ist, wie lange die Listung schon steht und wie wenige "
                + "Gebote sie hat - multipliziert. Ein Minus allein ist noch kein Zwang; erst "
                + "wenn kein Gebot mehr möglich ist, ist der Dispo ausgereizt. Eine Heuristik, "
                + "keine Wahrscheinlichkeit. " + SOLVER_NOTE,
            renderCell: (params) => {
                const risk = params.row.distressDetail
                const tier = DISTRESS_LABELS[risk.level]

                if (!tier)
                    return "–"

                const title = `${risk.seller} steht ${currencyFormatter.format(risk.deficit)} im Minus `
                    + `und hat davon ${unsignedPercentFormatter.format(risk.overdraftUsed)} seines erlaubten `
                    + `Dispos verbraucht - es bleiben ${currencyFormatter.format(risk.headroom)}. `
                    + `Die Listung läuft seit ${formatDuration(risk.ageHours * 60 * 60 * 1000)} `
                    + `bei ${risk.offerCount} Gebot${risk.offerCount === 1 ? "" : "en"}. `
                    + `Ein Tiefstgebot kann sich lohnen. ${ESTIMATE_NOTE}`

                return (
                    <Tooltip title={title} arrow>
                        <Chip label={tier.label} color={tier.color} size="small" variant="outlined" />
                    </Tooltip>
                )
            },
            sortComparator: missingSortsLast
        },
        {
            field: "listedFor",
            headerName: "Gelistet seit",
            type: "number",
            width: 140,
            headerAlign: "center",
            align: "right",
            // The one age signal that exists for user listings too, where "Ablaufdatum"
            // is empty. A Tooltip carries the exact timestamp behind the rounded span.
            renderCell: (params) => {
                const label = formatDuration(params.value) ?? "–"

                if (!params.row.listedSince)
                    return label

                return (
                    <Tooltip title={new Date(params.row.listedSince).toLocaleString("de-DE")} arrow>
                        <span>{label}</span>
                    </Tooltip>
                )
            },
            sortComparator: missingSortsLast
        },
        {
            field: "remaining",
            headerName: "Restzeit",
            type: "number",
            width: 130,
            headerAlign: "center",
            align: "right",
            // Derived from the real expiry only. Kickbase sends one for its own listings
            // alone, and guessing a deadline for the user listings from a listing window
            // nobody has measured would put a wrong number where an empty cell belongs.
            valueFormatter: durationOrDash,
            sortComparator: missingSortsLast
        },
        {
            field: "expiration",
            headerName: "Ablaufdatum",
            type: "dateTime",
            width: 150,
            headerAlign: "center",
            align: "right",
            // Kickbase sends an expiry for its own listings only, so this stays empty for
            // players listed by league members
            valueFormatter: ({ value }) => value ? value.toLocaleString("de-DE") : "",
            // Rows without a deadline sort last rather than first, so ascending order puts
            // the listings that actually run out on top instead of burying them
            sortComparator: (a, b) => (a ? a.getTime() : Infinity) - (b ? b.getTime() : Infinity),
        },
    ]

    // Every hook above this line: the gate returns early, and the rules of hooks do not
    // allow that before the last of them has run.
    //
    // Named after the listings, because that is the dataset without which there is no table.
    // Missing balances or profiles cost columns their meaning, not the table - and both of
    // those already say so themselves: ManagerStacks warns about absent budget data, and the
    // bidder column is left out entirely without profiles.
    const gate = dataGate({
        name: MARKET, status, error: fetchError, missing: missing.includes(MARKET), reload
    })

    if (gate)
        return gate

    // Fill the rows with the players attributes from the JSON file
    const rows = listings.map((row, i) => {
        // A Date, so the column sorts chronologically instead of by string
        const expiration = row.expiration ? new Date(row.expiration) : null

        // One call answers both solver columns, so the rival set is built once per listing
        const solved = minWinningBid(row, balances, me?.userId)
        const distressDetail = forcedSaleRisk(row, balances, now)

        // Which of the affordable managers buy players like this one. The affordable set is
        // the solver's own (affordableRivals, via minWinningBid), so the budget half of the
        // heuristic is derived once per listing rather than twice.
        const likelyBidderDetail = likelyBidders(row, profiles, { rivals: solved.rivals })

        return {
            // The player, not their position in the file. Keyed by index, every sale
            // shifted the rows below it onto a different player, which took the selection
            // and the row state with it. Also what addresses the row for the bid endpoints.
            id: row.playerId ?? `row-${i}`,
            playerId: row.playerId,
            teamLogo: process.env.PUBLIC_URL + "/images/" + row.teamId + ".png",
            position: row.position,
            // Some players have no first name in the API, so a plain join would leave a
            // leading space
            player: [row.firstName, row.lastName].filter(Boolean).join(" "),
            status: row.status,
            statusText: row.statusText,
            marketValue: row.marketValue,
            price: row.price,
            // What the asking price adds on top of the current market value. Always 0 for
            // free agents, where Kickbase asks exactly the market value.
            markup: row.marketValue ? row.price / row.marketValue - 1 : null,
            // Nobody bids on their own listing. Read off the solver rather than computed
            // again here - it already resolves the same question (via sellerId and the
            // logged-in manager's own id in balances.json) for the "Mindestgebot" and
            // "Verdeckte Bieter" columns, and a second computation is a second place to
            // drift from the first. The backend no longer ships an "isOwnListing" field of
            // its own for exactly that reason.
            isOwnListing: solved.isOwnListing,
            // The pace both break-even figures come from, averaged in the backend over
            // BEP_GROWTH_DAYS
            avgDailyGrowth: row.avgDailyGrowth,
            // Days for the market value to grow into the asking price at that pace
            daysToBep: daysToBreakEven(row),
            // The bid that would break even after BEP_TARGET_DAYS days. Kept apart from
            // ownBid so the column still sorts by the real bid.
            suggestedBid: breakEvenBid(row, config.bepTargetDays),
            // A bid confirmed this session overrides what market.json was built with
            ownBid: row.playerId in bids ? bids[row.playerId] : row.ownBid,
            today: row.today,
            todayPercent: relativeChange(row.today, row.marketValue),
            yesterday: row.yesterday,
            yesterdayPercent: relativeChange(row.yesterday, row.marketValue),
            twoDays: row.twoDays,
            twoDaysPercent: relativeChange(row.twoDays, row.marketValue),
            sevenDays: row.sevenDaysAvg,
            sevenDaysPercent: relativeChange(row.sevenDaysAvg, row.marketValue),
            thirtyDays: row.thirtyDaysAvg,
            thirtyDaysPercent: relativeChange(row.thirtyDaysAvg, row.marketValue),
            offerCount: row.offerCount,
            seller: row.seller,
            isFreeAgent: row.isFreeAgent,
            // The auction solver. The columns sort on the two numbers and read the detail
            // off the object, so the set of affordable rivals is derived once per listing.
            solved,
            minBid: solved.bid,
            hiddenBidders: solved.rivals.length,
            likelyBidderDetail,
            likelyBidders: likelyBidderDetail.length,
            distressDetail,
            distress: distressDetail.score,
            expiration,
            listedSince: row.listedSince,
            // How long the listing has been up, and how long it still has to run. Both are
            // spans in milliseconds so they sort by length rather than by their label.
            listedFor: elapsedSince(row.listedSince, now),
            remaining: expiration ? expiration.getTime() - now : null,
        }
    })

    // Populate the table
    return (
        <>
        <ManagerStacks balances={balances} />
        <Box sx={{
            ...deltaColumnStyles,
            // Paying over the market value is the expensive direction
            "& .markup-over": { color: "red" },
            "& .markup-under": { color: "green" },
            // Free agents are the rows worth spotting at a glance. Tinted through the theme
            // so it holds up in dark mode too, where a light tint needs more weight to read
            // against the dark surface.
            "& .free-agent-row": {
                backgroundColor: alpha(theme.palette.info.main, theme.palette.mode === "dark" ? 0.2 : 0.12),
                "&:hover": { backgroundColor: alpha(theme.palette.info.main, theme.palette.mode === "dark" ? 0.3 : 0.22) }
            }
        }}>
            <PagedDataGrid
                rows={rows}
                columns={columns}
                getRowClassName={(params) => params.row.isFreeAgent ? "free-agent-row" : ""}
                initialState={{ sorting: { sortModel: [{ field: "expiration", sort: "asc" }] } }}
            />

            <Dialog open={Boolean(confirming)} onClose={() => setConfirming(null)}>
                <DialogTitle>Gebot bestätigen</DialogTitle>
                <DialogContent>
                    <DialogContentText>
                        {confirming && `Wirklich ${currencyFormatter.format(confirming.price)} bieten? `
                            + `Das ist mindestens das Doppelte des ${confirming.usedSuggestion ? "Vorschlags" : "Marktwerts"}.`}
                    </DialogContentText>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setConfirming(null)}>Abbrechen</Button>
                    <Button onClick={() => send(confirming.playerId, confirming.price)} autoFocus>
                        Gebot abgeben
                    </Button>
                </DialogActions>
            </Dialog>

            <Snackbar open={Boolean(error)} autoHideDuration={8000} onClose={() => setError(null)}>
                <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>
            </Snackbar>
        </Box>
        </>
    )
}

export default MarketTable
