import React, { useState } from "react"

import PagedDataGrid from "./PagedDataGrid"
import BalanceEventsDialog from "./BalanceEventsDialog"
import { currencyFormatter } from "./SharedConstants"
import Avatar from "@mui/material/Avatar"

// Import data
import data from "../data/balances.json"

function Balances() {
    // The manager whose balance events are on screen, or null while the dialog is closed
    const [selectedManager, setSelectedManager] = useState(null)

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
            valueFormatter: ({ value }) => currencyFormatter.format(Number(value)),
        },
        {
            field: "maxBid",
            headerName: "Max. Gebot",
            headerAlign: "center",
            align: "center",
            flex: 1,
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
        }
    ))

    // Populate the table
    return (
        <>
            <PagedDataGrid
                rows={rows}
                columns={columns}
                initialState={{ sorting: { sortModel: [{ field: "teamValue", sort: "desc" }] } }}
                onRowClick={(params) => setSelectedManager(params.row)}
                sx={{ "& .MuiDataGrid-row": { cursor: "pointer" } }}
            />
            <BalanceEventsDialog manager={selectedManager} onClose={() => setSelectedManager(null)} />
        </>
    )
}

export default Balances