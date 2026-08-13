import PagedDataGrid from "./PagedDataGrid"
import { currencyFormatter } from "./SharedConstants"
import Avatar from "@mui/material/Avatar"

import { useJsonData } from "../hooks/useJsonData"
import { dataGate } from "./DataState"

const DATASET = "league_user_stats.json"

/**
 * The gap to the manager above, per manager.
 *
 * This used to run at module scope, sorting the imported array in place and then writing
 * `data[0].pointsDiff` - which threw on an empty file and mutated a module other components
 * import. Fetching makes the empty case the normal first state, so it is a function over a
 * copy now.
 */
export function withPointsDiff(rows) {
    const sorted = [...(rows || [])].sort((a, b) => b.points - a.points)

    return sorted.map((row, i) => ({
        ...row,
        pointsDiff: i === 0 ? 0 : sorted[i - 1].points - row.points
    }))
}

function LeagueUserTable() {
    const { status, data, missing, error, reload } = useJsonData(DATASET)
    // Define the columns of the table
    const columns = [
        {
            field: "placement",
            headerName: "Platz",
            type: "number",
            width: 50,
            headerAlign: "center",
            align: "center",
        },
        {
            field: "user",
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
            field: "points",
            headerName: "Punkte",
            type: "number",
            flex: 1,
            headerAlign: "center",
            align: "center",
            // Format the number with thousand separators (.)
            valueFormatter: (params) => params.value.toLocaleString('de-DE'),
        },
        {
            field: "pointsDiff",
            headerName: "Differenz",
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
            field: "teamValue",
            headerName: "Teamwert",
            type: "number",
            flex: 1,
            headerAlign: "center",
            align: "center",
            valueFormatter: ({ value }) => currencyFormatter.format(Number(value)),
        },
    //     {
    //         field: "maxBuy",
    //         headerName: "Max. Kauf",
    //         flex: 1,
    //         headerAlign: "center",
    //         align: "center",
    //         valueFormatter: ({ value }) => currencyFormatter.format(Number(value)),
    //     },
    //     {
    //         field: "maxSell",
    //         headerName: "Max. Verkauf",
    //         flex: 1,
    //         headerAlign: "center",
    //         align: "center",
    //         valueFormatter: ({ value }) => currencyFormatter.format(Number(value)),
    //     },
    ]

    // Every hook above this line: the gate returns early, and the rules of hooks do
    // not allow that before the last of them has run.
    const gate = dataGate({ name: DATASET, status, error, missing, reload })

    if (gate)
        return gate

    // Fill the rows with the players attributes from the JSON file
    const rows = withPointsDiff(data).map((row, i) => (
        {
            id: i,
            user: row.userName,
            profilePic: row.profilePic,
            placement: row.placement,
            points: row.points,
            pointsDiff: row.pointsDiff, // Calculated in frontend
            teamValue: row.teamValue,
            // maxBuy: row.maxBuyPrice,
            // maxSell: row.maxSellPrice,
        }
    ))

    // Populate the table
    return (
        <PagedDataGrid
            rows={rows}
            columns={columns}
            initialState={{ sorting: { sortModel: [{ field: "points", sort: "desc" }] } }}
        />
    )
}

export default LeagueUserTable