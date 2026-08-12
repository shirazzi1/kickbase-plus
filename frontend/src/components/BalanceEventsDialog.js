import React from "react"

import Avatar from "@mui/material/Avatar"
import Box from "@mui/material/Box"
import Button from "@mui/material/Button"
import Dialog from "@mui/material/Dialog"
import DialogActions from "@mui/material/DialogActions"
import DialogContent from "@mui/material/DialogContent"
import DialogTitle from "@mui/material/DialogTitle"

import PagedDataGrid from "./PagedDataGrid"
import { currencyFormatter, deltaCellClassName, deltaColumnStyles } from "./SharedConstants"

const eventTypeLabels = { start: "Startbudget", buy: "Kauf", sell: "Verkauf" }

// The feed only names a counterpart when another manager was on the other side of the
// transfer. A market purchase, a market sale and the starting budget all come from
// Kickbase itself.
const tradePartnerLabel = (partner) => partner || "Kickbase"

function BalanceEventsDialog({ manager, onClose }) {
    // Not just an optimisation: PagedDataGrid reads rows.length once, on mount, to pick
    // its page size. Mounting fresh per manager is what keeps that number right.
    if (!manager)
        return null

    const events = manager.events || []

    const columns = [
        {
            field: "teamId",
            headerName: "Verein",
            width: 70,
            headerAlign: "center",
            align: "center",
            sortable: false,
            // The start event has no team, and "/images/null.png" would only 404
            renderCell: (params) => params.value ? (
                <img
                    src={process.env.PUBLIC_URL + "/images/" + params.value + ".png"}
                    alt=""
                    width="30"
                    onError={(e) => {
                        e.target.onerror = null // Prevent infinite loop if default.png is also missing
                        e.target.src = process.env.PUBLIC_URL + "/images/default.png"
                    }}
                />
            ) : null,
        },
        {
            field: "playerName",
            headerName: "Spieler",
            flex: 2,
            // The avatar eats into the cell, so long names need the floor
            minWidth: 170,
            headerAlign: "center",
            renderCell: (params) => params.value ? (
                <div style={{ display: "flex", alignItems: "center" }}>
                    <Avatar src={params.row.playerImage} alt="" sx={{ marginRight: 1, width: 30, height: 30 }} />
                    {params.value}
                </div>
            ) : null,
        },
        {
            field: "date",
            headerName: "Datum",
            type: "dateTime",
            flex: 2,
            minWidth: 150,
            headerAlign: "center",
            align: "center",
            // Sorting a mix of "…Z" and "…+00:00" strings would compare the offset
            // suffix, so hand the grid real dates
            valueGetter: ({ value }) => new Date(value),
            // No seconds: they cost the column the width it needs and say nothing
            valueFormatter: ({ value }) => value.toLocaleString("de-DE", {
                day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
            }),
        },
        {
            field: "type",
            headerName: "Event",
            flex: 1,
            // "Startbudget" is the longest label and gets truncated at the flex width
            minWidth: 120,
            headerAlign: "center",
            align: "center",
            valueFormatter: ({ value }) => eventTypeLabels[value] || value,
        },
        {
            field: "tradePartner",
            headerName: "Handelspartner",
            flex: 2,
            headerAlign: "center",
            align: "center",
            valueFormatter: ({ value }) => tradePartnerLabel(value),
        },
        {
            field: "amount",
            headerName: "Betrag",
            type: "number",
            flex: 2,
            headerAlign: "center",
            valueFormatter: ({ value }) => currencyFormatter.format(Number(value)),
            cellClassName: deltaCellClassName,
        },
        {
            field: "balance",
            headerName: "Saldo",
            type: "number",
            flex: 2,
            headerAlign: "center",
            valueFormatter: ({ value }) => currencyFormatter.format(Number(value)),
            cellClassName: "font-tabular-nums",
        },
    ]

    // The backend returns the events oldest first, which is the only order in which the
    // Saldo column reads from top to bottom. No sort model, so that order is the default.
    const rows = events.map((event, i) => ({ id: i, ...event }))

    // Seven columns, three of them full currency amounts: "md" truncates them
    return (
        <Dialog open onClose={onClose} maxWidth="lg" fullWidth>
            <DialogTitle>Kontostand-Verlauf: {manager.username}</DialogTitle>
            <DialogContent>
                <Box sx={deltaColumnStyles}>
                    <PagedDataGrid rows={rows} columns={columns} />
                </Box>
            </DialogContent>
            <DialogActions>
                <Button onClick={onClose}>Schließen</Button>
            </DialogActions>
        </Dialog>
    )
}

export default BalanceEventsDialog
