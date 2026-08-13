import Accordion from "@mui/material/Accordion"
import AccordionDetails from "@mui/material/AccordionDetails"
import AccordionSummary from "@mui/material/AccordionSummary"
import Alert from "@mui/material/Alert"
import Avatar from "@mui/material/Avatar"
import ExpandMoreIcon from "@mui/icons-material/ExpandMore"
import LinearProgress from "@mui/material/LinearProgress"
import Tooltip from "@mui/material/Tooltip"
import Typography from "@mui/material/Typography"
import { Box, alpha, useTheme } from "@mui/material"

import { currencyFormatter, currencyOrDash } from "./SharedConstants"
import { managerStacks, ownManager } from "./marketFormulas"

// The one sentence that has to travel with every number the auction solver produces. The
// budgets behind them are reconstructed from the transfer feed plus an assumed daily login
// bonus and derived achievements - never read from Kickbase.
export const ESTIMATE_NOTE = "Budgets sind Schätzungen aus der Transferhistorie, keine Angaben von Kickbase."

// What the calibration measured, with the date it was measured on: the number ages, and a
// note that hides that would be worse than none. Nachrechnen mit
// ./venv/bin/python tests/calibrate_min_bid.py
export const CALIBRATION_NOTE = "Stand 13.08.2026 gegen 204 echte Käufe geprüft: in 2 Fällen "
    + "(1,0 %) hat ein Manager mehr bezahlt, als die Schätzung zuließ - im schlimmsten Fall "
    + "20 % über der geschätzten Decke. Ein Mindestgebot ist also eher zu niedrig als zu hoch."

/**
 * The stack sizes at the table: every manager's estimated bidding ceiling, richest first.
 *
 * The poker view of the league. It answers the question the market table asks per row -
 * "who can still afford this?" - once for the whole league, so the numbers in the
 * "Verdeckte Bieter" and "Mindestgebot" columns have somewhere to be checked against.
 *
 * Collapsed by default: it is context, and the table below it is the work.
 */
function ManagerStacks({ balances }) {
    const theme = useTheme()

    const stacks = managerStacks(balances)
    const me = ownManager(balances)

    if (stacks.length === 0)
        return null

    const rank = me ? stacks.findIndex((stack) => stack.isSelf) + 1 : null

    const summary = rank
        ? `Bieter-Übersicht - du liegst mit ${currencyFormatter.format(stacks[rank - 1].maxBid)} `
            + `auf Platz ${rank} von ${stacks.length}`
        : `Bieter-Übersicht - ${stacks.length} Manager nach geschätztem Maximalgebot`

    return (
        <>
        {/* balances.json written before this feature carries no "isSelf", and guessing
            which manager is the user would be worse than saying so. Outside the accordion
            on purpose: it is collapsed by default, and a warning nobody opens is no
            warning - three columns quietly say something else than they mean until the
            next scrape has run. */}
        {!me && (
            <Alert severity="warning" sx={{ margin: "0 15px 10px" }}>
                Noch ist kein Manager als 'du' markiert - das schreibt erst der nächste
                Scrape-Lauf. Bis dahin zählst du bei 'Verdeckte Bieter' mit, obwohl der
                Spaltenkopf dich ausnimmt; das Mindestgebot ist nicht auf dein eigenes Budget
                begrenzt und erscheint auch auf deinen eigenen Listungen; und 'Zwangsverkauf
                droht' kann auch deine eigenen Listungen markieren.
            </Alert>
        )}
        <Accordion disableGutters sx={{ margin: "0 15px 10px", backgroundColor: "transparent" }}>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Typography>{summary}</Typography>
            </AccordionSummary>
            <AccordionDetails>
                <Typography variant="body2" sx={{ opacity: 0.7, marginBottom: "12px" }}>
                    {ESTIMATE_NOTE} {CALIBRATION_NOTE}
                </Typography>

                <Box sx={{ display: "flex", alignItems: "center", gap: "10px", padding: "0 8px 4px" }}>
                    <Box sx={{ width: "28px", flexShrink: 0 }} />
                    <Typography variant="caption" sx={{ width: "160px", flexShrink: 0, opacity: 0.6 }}>
                        Manager
                    </Typography>
                    <Box sx={{ flex: 1, minWidth: "80px" }} />
                    <Typography variant="caption" sx={{ width: "130px", textAlign: "right", flexShrink: 0, opacity: 0.6 }}>
                        Max. Gebot
                    </Typography>
                    <Typography variant="caption" sx={{ width: "130px", textAlign: "right", flexShrink: 0, opacity: 0.6 }}>
                        Kontostand
                    </Typography>
                </Box>

                {stacks.map((stack) => (
                    <Box
                        key={stack.userId ?? stack.username}
                        sx={{
                            display: "flex",
                            alignItems: "center",
                            gap: "10px",
                            padding: "4px 8px",
                            borderRadius: "4px",
                            // The user's own row is the one to find at a glance
                            backgroundColor: stack.isSelf
                                ? alpha(theme.palette.info.main, theme.palette.mode === "dark" ? 0.2 : 0.12)
                                : undefined
                        }}
                    >
                        <Avatar src={stack.profilePic} alt={stack.username} sx={{ width: 28, height: 28 }} />
                        <Typography variant="body2" sx={{ width: "160px", flexShrink: 0 }}>
                            {stack.username}
                        </Typography>

                        <Tooltip
                            arrow
                            title={`Kontostand ${currencyOrDash({ value: stack.balance })}, `
                                + `Teamwert ${currencyOrDash({ value: stack.teamValue })}. ${ESTIMATE_NOTE}`}
                        >
                            <Box sx={{ flex: 1, minWidth: "80px" }}>
                                <LinearProgress
                                    variant="determinate"
                                    value={stack.share * 100}
                                    color={stack.isSelf ? "info" : "primary"}
                                    sx={{ height: "10px", borderRadius: "5px" }}
                                />
                            </Box>
                        </Tooltip>

                        <Typography
                            variant="body2"
                            sx={{
                                width: "130px",
                                textAlign: "right",
                                flexShrink: 0,
                                fontVariantNumeric: "tabular-nums"
                            }}
                        >
                            {currencyOrDash({ value: stack.maxBid })}
                        </Typography>

                        {/* A manager in the red has to sell, which is what the distress
                            column in the table below picks up per listing */}
                        <Typography
                            variant="body2"
                            sx={{
                                width: "130px",
                                textAlign: "right",
                                flexShrink: 0,
                                fontVariantNumeric: "tabular-nums",
                                color: stack.balance < 0 ? theme.palette.error.main : undefined,
                                opacity: stack.balance < 0 ? 1 : 0.6
                            }}
                        >
                            {currencyOrDash({ value: stack.balance })}
                        </Typography>
                    </Box>
                ))}
            </AccordionDetails>
        </Accordion>
        </>
    )
}

export default ManagerStacks
