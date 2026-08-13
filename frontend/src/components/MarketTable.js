import { useEffect, useState } from "react"
import Tooltip from "@mui/material/Tooltip"
import Typography from "@mui/material/Typography"
import Chip from "@mui/material/Chip"
import { Box, alpha, useTheme } from "@mui/material"
import PagedDataGrid from "./PagedDataGrid"
import {
    currencyFormatter,
    percentFormatter,
    unsignedPercentFormatter,
    currencyOrDash,
    percentOrDash,
    deltaCellClassName,
    deltaColumnStyles,
    getStatusIcon
} from "./SharedConstants"
import {
    relativeChange,
    daysToBreakEven,
    formatDuration,
    elapsedSince,
    ownManager,
    minWinningBid,
    forcedSaleRisk
} from "./marketFormulas"
import ManagerStacks, { ESTIMATE_NOTE } from "./ManagerStacks"

// Import data
import data from "../data/market.json"
import balances from "../data/balances.json"

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

                if (params.value === null || params.value === undefined)
                    return "–"

                // Who the bid has to beat, so the number reads as what it is instead of as
                // a forecast. Kickbase never says who is bidding, so this is the set that
                // could - not the set that does.
                const beats = solved.isPhantom
                    ? "Kein anderer Manager kann den Preis zahlen, der Preis selbst genügt."
                    : `Übertrifft ${solved.rivals[0].username} `
                        + `(max. ${currencyFormatter.format(solved.rivals[0].maxBid)})`
                        + ` und ${solved.rivals.length - 1} weitere.`

                const capped = solved.exceedsBudget
                    ? ` Nötig wären ${currencyFormatter.format(solved.required)} - `
                        + `dein geschätztes Maximum sind ${currencyFormatter.format(solved.ownMaxBid)}.`
                    : ""

                return (
                    <Tooltip title={`${beats}${capped} ${ESTIMATE_NOTE}`} arrow>
                        <span>
                            <span style={{ color: solved.exceedsBudget ? theme.palette.error.main : undefined }}>
                                {currencyFormatter.format(params.value)}
                            </span>
                            {solved.isPhantom && (
                                <Typography component="span" variant="body2" sx={{ opacity: 0.6, marginLeft: "6px" }}>
                                    Phantom
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
            renderCell: (params) => {
                if (params.value === null || params.value === undefined)
                    return ""

                // How far the bid sits above (or below) the current market value
                const marketValue = params.row.marketValue
                const surcharge = marketValue
                    ? percentFormatter.format(params.value / marketValue - 1)
                    : null

                return (
                    <span>
                        {currencyFormatter.format(Number(params.value))}
                        {surcharge && (
                            <Typography component="span" variant="body2" sx={{ opacity: 0.6, marginLeft: "6px" }}>
                                ({surcharge})
                            </Typography>
                        )}
                    </span>
                )
            }
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
                const rivals = params.row.solved.rivals

                if (rivals.length === 0)
                    return (
                        <Tooltip title={`Niemand außer dir kann den Preis zahlen. ${ESTIMATE_NOTE}`} arrow>
                            <span>0</span>
                        </Tooltip>
                    )

                const who = rivals
                    .map((rival) => `${rival.username} (max. ${currencyFormatter.format(rival.maxBid)})`)
                    .join(", ")

                return (
                    <Tooltip title={`${who}. ${ESTIMATE_NOTE}`} arrow>
                        <span>{rivals.length}</span>
                    </Tooltip>
                )
            }
        },
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

                const title = `${risk.seller} steht ${currencyFormatter.format(-risk.deficit)} im Minus `
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

    // Fill the rows with the players attributes from the JSON file
    const rows = data.map((row, i) => {
        // A Date, so the column sorts chronologically instead of by string
        const expiration = row.expiration ? new Date(row.expiration) : null

        // One call answers both solver columns, so the rival set is built once per listing
        const solved = minWinningBid(row, balances, me?.userId)
        const distressDetail = forcedSaleRisk(row, balances, now)

        return {
            // The player, not their position in the file. Keyed by index, every sale
            // shifted the rows below it onto a different player, which took the selection
            // and the row state with it.
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
            // Days for the market value to grow into the asking price at the pace of the
            // last three days
            daysToBep: daysToBreakEven(row),
            ownBid: row.ownBid,
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
        </Box>
        </>
    )
}

export default MarketTable
