import { useState } from "react"
import Tooltip from "@mui/material/Tooltip"
import {
    Box, Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle,
    Snackbar, Alert, alpha, useTheme
} from "@mui/material"
import PagedDataGrid from "./PagedDataGrid"
import BidCell from "./BidCell"
import {
    percentFormatter,
    currencyFormatter,
    currencyOrDash,
    percentOrDash,
    deltaCellClassName,
    deltaColumnStyles,
    getStatusIcon
} from "./SharedConstants"
import { relativeChange, daysToBreakEven, breakEvenBid } from "./marketFormulas"

// Import data
import data from "../data/market.json"
import config from "../data/config.json"

const daysFormatter = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 })

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

function MarketTable() {
    const theme = useTheme()

    // The editing state lives here rather than in the cell: renderCell re-runs on every
    // scroll and every sort, and state held inside a cell would not survive either.
    const [edit, setEdit] = useState(null)          // { playerId, draft }
    const [pendingId, setPendingId] = useState(null)
    // Confirmed bids, keyed by player. market.json is imported at build time, so this is
    // what shows a bid before the patched file has been picked up.
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
                headers: { "Content-Type": "application/json" },
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
            field: "seller",
            headerName: "Verkäufer",
            flex: 1,
            minWidth: 110,
            headerAlign: "center",
            align: "center"
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

    // Fill the rows with the players attributes from the JSON file
    const rows = data.map((row, i) => (
        {
            id: i,
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
            // Addresses the row for the bid endpoints
            playerId: row.playerId,
            // Nobody bids on their own listing
            isOwnListing: row.isOwnListing,
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
            seller: row.seller,
            isFreeAgent: row.isFreeAgent,
            // A Date, so the column sorts chronologically instead of by string
            expiration: row.expiration ? new Date(row.expiration) : null,
        }
    ))

    // Populate the table
    return (
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
    )
}

export default MarketTable
