import PagedDataGrid from "./PagedDataGrid"
import Avatar from "@mui/material/Avatar"

import { useJsonData } from "../hooks/useJsonData"
import { dataGate } from "./DataState"

const DATASET = "league_user_stats.json"

function SeasonStatsTable() {
    const { status, data, missing, error, reload } = useJsonData(DATASET)

    // Define the columns of the table
    const columns = [
        {
            field: "user",
            headerName: "Manager",
            headerAlign: "center",
            flex: 2,
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
            field: "avgPoints",
            headerName: "⌀ Punkte",
            type: "number",
            flex: 1,
            headerAlign: "center",
            align: "center",
            // Format the number with thousand separators (.)
            valueFormatter: (params) => {
                return params.value.toLocaleString('de-DE');
            },
        },
        {
            field: "maxPoints",
            headerName: "Höchste Punkte",
            type: "number",
            flex: 1,
            headerAlign: "center",
            align: "center",
            // Format the number with thousand separators (.)
            valueFormatter: (params) => {
                return params.value.toLocaleString('de-DE');
            },
        },
        // {
        //     field: "minPoints",
        //     headerName: "Wenigste Punkte",
        //     type: "number",
        //     flex: 1,
        //     headerAlign: "center",
        //     align: "center",
        //     // Format the number with thousand separators (.)
        //     valueFormatter: (params) => {
        //         return params.value.toLocaleString('de-DE');
        //     },
        // },
        {
            field: "mdWins",
            headerName: "Spieltagssiege",
            type: "number",
            flex: 1,
            headerAlign: "center",
            align: "center",
            // Format the number with thousand separators (.)
            valueFormatter: (params) => {
                return params.value.toLocaleString('de-DE');
            },
        },
        // {
        //     field: "bought",
        //     headerName: "Gekauft",
        //     type: "number",
        //     flex: 1,
        //     headerAlign: "center",
        //     align: "center",
        //     // Format the number with thousand separators (.)
        //     valueFormatter: (params) => {
        //         return params.value.toLocaleString('de-DE');
        //     },
        // },
        // {
        //     field: "sold",
        //     headerName: "Verkauft",
        //     type: "number",
        //     flex: 1,
        //     headerAlign: "center",
        //     align: "center",
        //     // Format the number with thousand separators (.)
        //     valueFormatter: (params) => {
        //         return params.value.toLocaleString('de-DE');
        //     },
        // },
        {
            field: "trades",
            headerName: "Trades",
            type: "number",
            flex: 1,
            headerAlign: "center",
            align: "center",
            // Format the number with thousand separators (.)
            valueFormatter: (params) => {
                return params.value.toLocaleString('de-DE');
            }
        }
    ]

    // Every hook above this line: the gate returns early, and the rules of hooks do
    // not allow that before the last of them has run.
    const gate = dataGate({ name: DATASET, status, error, missing, reload })

    if (gate)
        return gate

    // Fill the rows with the players attributes from the JSON file
    const rows = data.map((row, i) => (
        {
            id: i,
            user: row.userName,
            profilePic: row.profilePic,
            avgPoints: row.avgPoints,
            maxPoints: row.maxPoints,
            // minPoints: row.minPoints,
            mdWins: row.mdWins,
            // bought: row.bought,
            // sold: row.sold,
            trades: row.trades
        }
    ))

    // Populate the table
    return (
        <PagedDataGrid
            rows={rows}
            columns={columns}
            // initialState={{ sorting: { sortModel: [{ field: "points", sort: "desc" }] } }}
        />
    )
}

export default SeasonStatsTable