import React, { useState } from "react"

import PagedDataGrid from "./PagedDataGrid"
import BalanceEventsDialog from "./BalanceEventsDialog"
import { currencyFormatter } from "./SharedConstants"
import Avatar from "@mui/material/Avatar"
import Box from "@mui/material/Box"
import FormControlLabel from "@mui/material/FormControlLabel"
import Switch from "@mui/material/Switch"

// Import data
import data from "../data/balances.json"

function Balances() {
    // The manager whose balance events are on screen, or null while the dialog is closed
    const [selectedManager, setSelectedManager] = useState(null)

    // One setting for the table and the dialog, so the two cannot show different
    // assumptions about the same manager
    const [withBonuses, setWithBonuses] = useState(false)

    // Define the columns of the table
    const columns = [
        {
            field: "username",
            headerName: "Manager",
            headerAlign: "center",
            flex: 1,
            align: "left",
            // Display profile picture and username
            renderCell: (params) => (
                <div style={{ display: "flex", alignItems: "center" }}>
                    <Avatar src={params.row.profilePic} alt={params.row.userName} sx={{ marginRight: 1 }} />
                    {params.value}
                </div>
            ),  
        },
        {
            field: "teamValue",
            headerName: "Teamwert",
            type: "number",
            flex: 1,
            headerAlign: "center",
            align: "center",
            valueFormatter: ({ value }) => currencyFormatter.format(Number(value)),
        },
        {
            field: "balance",
            headerName: "Kontostand",
            headerAlign: "center",
            flex: 1,
            align: "center",
            type: "number",
            valueGetter: ({ row }) => withBonuses ? row.balanceWithBonuses : row.balance,
            valueFormatter: ({ value }) => currencyFormatter.format(Number(value)),
        },
        {
            field: "maxBid",
            headerName: "Max. Gebot",
            headerAlign: "center",
            align: "center",
            flex: 1,
            type: "number",
            valueGetter: ({ row }) => withBonuses ? row.maxBidWithBonuses : row.maxBid,
            valueFormatter: ({ value }) => currencyFormatter.format(Number(value)),
        },
    ]

    // Fill the rows with the attributes from the JSON file
    const rows = data.map((row, i) => (
        {
            id: i,
            username: row.username,
            profilePic: row.profilePic,
            teamValue: row.teamValue,
            balance: row.balance,
            maxBid: row.maxBid,
            events: row.events,
            balanceWithBonuses: row.balanceWithBonuses,
            maxBidWithBonuses: row.maxBidWithBonuses,
            eventsWithBonuses: row.eventsWithBonuses,
        }
    ))

    // Populate the table
    return (
        <>
            <Box sx={{ padding: "0 15px 10px" }}>
                <FormControlLabel
                    control={
                        <Switch
                            checked={withBonuses}
                            onChange={(event) => setWithBonuses(event.target.checked)}
                        />
                    }
                    label="Boni & Erfolge einrechnen (geschätzt)"
                />
            </Box>
            <PagedDataGrid
                rows={rows}
                columns={columns}
                initialState={{ sorting: { sortModel: [{ field: "teamValue", sort: "desc" }] } }}
                onRowClick={(params) => setSelectedManager(params.row)}
                sx={{ "& .MuiDataGrid-row": { cursor: "pointer" } }}
            />
            <BalanceEventsDialog
                manager={selectedManager}
                withBonuses={withBonuses}
                onClose={() => setSelectedManager(null)}
            />
        </>
    )
}

export default Balances