import { Box, Tooltip, Typography } from "@mui/material"
import { currencyFormatter, percentFormatter } from "./SharedConstants"

// What the grey suggestion means, spelled out. Both horizons are named because they are
// configurable: at BEP_GROWTH_DAYS=7 a text about three days would be wrong.
function suggestionTooltip(row, growthDays, targetDays) {
    if (row.suggestedBid === null || row.suggestedBid === undefined)
        return `Kein Vorschlag: der Marktwert steigt über die letzten ${growthDays} Tage `
            + "nicht, oder die Historie ist zu kurz. Klicken, um trotzdem ein Gebot abzugeben."

    let text = `Gebot, das beim durchschnittlichen Zuwachs der letzten ${growthDays} Tage `
        + `in ${targetDays} Tagen Break-Even erreicht. Klicken, um zu bieten.`

    // A bid under the asking price is valid; it just tends not to be accepted
    if (row.price !== null && row.price !== undefined && row.suggestedBid < row.price)
        text += ` Liegt unter dem Preis von ${currencyFormatter.format(row.price)} – `
            + "gültig, aber der Verkäufer nimmt es kaum an."

    return text
}

// The cell in the "Dein Gebot" column at rest: a placed bid, or the greyed-out bid that
// would break even at the target horizon, or a dash when there is nothing to suggest.
function BidCell({ row, growthDays, targetDays, onEdit }) {
    // You cannot bid on a player you listed yourself - there you receive offers
    if (row.isOwnListing)
        return (
            <Tooltip title="Dein eigenes Angebot – hier bieten andere." arrow describeChild>
                <Box aria-label="Eigenes Angebot" sx={{ width: "100%", height: "100%" }} />
            </Tooltip>
        )

    const hasBid = row.ownBid !== null && row.ownBid !== undefined

    if (hasBid) {
        // How far the bid sits above (or below) the current market value
        const surcharge = row.marketValue
            ? percentFormatter.format(row.ownBid / row.marketValue - 1)
            : null

        return (
            <Tooltip title="Dein laufendes Gebot. Klicken, um es zu ändern oder zurückzuziehen." arrow describeChild>
                <Box onClick={onEdit} sx={{ cursor: "pointer", width: "100%", textAlign: "right" }}>
                    {currencyFormatter.format(Number(row.ownBid))}
                    {surcharge && (
                        <Typography component="span" variant="body2" sx={{ opacity: 0.6, marginLeft: "6px" }}>
                            ({surcharge})
                        </Typography>
                    )}
                </Box>
            </Tooltip>
        )
    }

    const suggestion = row.suggestedBid === null || row.suggestedBid === undefined
        ? "–"
        : currencyFormatter.format(Number(row.suggestedBid))

    return (
        <Tooltip title={suggestionTooltip(row, growthDays, targetDays)} arrow describeChild>
            {/* Greyed out to read as a proposal rather than as a fact, but in the same
                tabular figures as a real bid so the column still lines up */}
            <Box onClick={onEdit} sx={{ cursor: "pointer", opacity: 0.6, width: "100%", textAlign: "right" }}>
                {suggestion}
            </Box>
        </Tooltip>
    )
}

export default BidCell
