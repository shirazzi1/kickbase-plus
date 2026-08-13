import { DataGrid, GRID_CHECKBOX_SELECTION_COL_DEF } from '@mui/x-data-grid'
import React, { useState, useEffect } from 'react'
import InputLabel from '@mui/material/InputLabel'
import MenuItem from '@mui/material/MenuItem'
import FormControl from '@mui/material/FormControl'
import Select from '@mui/material/Select'
import TextField from '@mui/material/TextField'
import Grid from '@mui/material/Grid'
import { NumericFormat } from 'react-number-format'
import Paper from '@mui/material/Paper'
import Tooltip from '@mui/material/Tooltip'

import { trendIcons, currencyFormatter, getStatusIcon } from './SharedConstants'

import { useJsonData } from "../hooks/useJsonData"
import { dataGate } from "./DataState"

const DATASET = "taken_players.json"

// Split in two because the planner seeds its state from the data: the selected manager is the
// first one in the squads. With a compile-time import that was available before the first
// render; with a fetch it is not, and a useState default that runs once against an empty array
// stays empty forever. The inner component is therefore only mounted once the squads are
// there, which lets every piece of state below keep working exactly as it did.
function LineupPlanner() {
    const { status, data, missing, error, reload } = useJsonData(DATASET)

    const gate = dataGate({
        name: DATASET, status, error, missing, reload, rows: data,
        missingText: "Der Aufstellungsplaner arbeitet mit den Kadern der Liga. Die schreibt der "
            + "Schritt 'taken_free_players' eines Scrape-Laufs."
    })

    if (gate)
        return gate

    return <Planner data={data} />
}

function Planner({ data }) {
    const managers = [...new Set(data.map(item => item.owner))]

    const [manager, setManager] = useState(managers[0])
    const [balance, setBalance] = useState(0)
    const [selectedValue, setSelectedValue] = useState(0)
    const [selection, setSelection] = useState([])
    const [playersOnPositions, setPlayersOnPositions] = useState({ 0: 0, 1: 0, 2: 0 })

    const filteredData = data.filter(e => e.owner === manager)

    const possibleFormations = [[3, 4, 3], [4, 4, 2], [3, 5, 2], [4, 5, 1], [3, 6, 1], [5, 2, 3], [4, 2, 4], [5, 3, 2], [4, 3, 3], [5, 4, 1]].sort()

    const playableFormations = possibleFormations.filter((formation) => {
        for (var i = 0; i < 3; i++) {
            if (!(formation[i] <= playersOnPositions[i]))
                return false
        }
        return formation
    })

    useEffect(() => {
        const sum = filteredData.filter(x => selection.includes(x.playerId)).reduce((a, x) => a + x.marketValue, 0)
        setSelectedValue(sum)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selection])

    useEffect(() => {
        var updatedPlayersOnPositions = { 0: 0, 1: 0, 2: 0 }
        filteredData.forEach(player => {
            if (selection.includes(player.playerId))
                return
            if (player.position === 'ABW')
                updatedPlayersOnPositions[0]++
            else if (player.position === 'MF')
                updatedPlayersOnPositions[1]++
            else if (player.position === 'ANG')
                updatedPlayersOnPositions[2]++
        })
        setPlayersOnPositions(updatedPlayersOnPositions)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selection, manager])

    const columns = [
        {
            field: 'teamLogo',
            headerName: 'Team',
            width: 50,
            headerAlign: 'center',
            align: 'center',
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
            field: 'position',
            headerName: 'Position',
            headerAlign: 'center',
            align: 'center',
            flex: 1
        },
        {
            field: 'firstName',
            headerName: 'Vorname',
            headerAlign: 'center',
            align: 'center',
            flex: 2
        },
        {
            field: 'lastName',
            headerName: 'Nachname',
            headerAlign: 'center',
            align: 'center',
            flex: 2
        },
        {
            field: "status",
            headerName: "Status",
            headerAlign: 'center',
            align: 'center',
            flex: 1,
            renderCell: (params) => (
                <Tooltip title={getStatusIcon(params.value).tooltip} arrow>
                    {getStatusIcon(params.value).icon}
                </Tooltip>
            )
        },
        {
            field: 'buyPrice',
            headerName: 'Kaufpreis',
            type: 'number',
            flex: 2,
            valueFormatter: ({ value }) => currencyFormatter.format(Number(value)),
            headerAlign: 'center',
            cellClassName: 'font-tabular-nums'
        },
        {
            field: 'marketValue',
            headerName: 'Marktwert',
            type: 'number',
            flex: 2,
            valueFormatter: ({ value }) => currencyFormatter.format(Number(value)),
            headerAlign: 'center',
            cellClassName: 'font-tabular-nums'
        },
        {
            field: 'trend',
            headerName: 'Trend',
            flex: 1,
            headerAlign: 'center',
            align: 'center',
            renderCell: (params) => trendIcons[params.value]
        },
        {
            ...GRID_CHECKBOX_SELECTION_COL_DEF
        }
    ]

    const rows = filteredData.map((row, i) => (
        {
            id: row.playerId,
            manager: row.owner,
            teamLogo: process.env.PUBLIC_URL + "/images/" + row.teamId + ".png",
            position: row.position,
            firstName: row.firstName,
            lastName: row.lastName,
            status: row.status,
            buyPrice: row.buyPrice, // row.buyPrice === 0 ? row.market_value : row.buy_price,
            marketValue: row.marketValue,
            turnover: row.buyPrice === 0 ? 0 : row.marketValue - row.buyPrice,
            trend: row.trend
        }
    ))

    return (
        <>
            <Grid container justifyContent="center">
                <Grid item>
                    <FormControl sx={{ margin: 1 }}>
                        <InputLabel>Manager</InputLabel>
                        <Select value={manager} label="Manager" onChange={e => setManager(e.target.value)}>
                            {managers.map(e => <MenuItem key={e} value={e}>{e}</MenuItem>)}
                        </Select>
                    </FormControl>
                </Grid>
                <Grid item sx={{ margin: 1 }}>
                    <NumericFormat
                        value={balance}
                        label="Kontostand"
                        thousandSeparator="."
                        decimalSeparator=','
                        allowNegative
                        customInput={TextField}
                        decimalScale={0}
                        onValueChange={(e) => { setBalance(Number(e.value)) }}
                        suffix={" €"} />
                </Grid>
                <Grid item sx={{ margin: 1 }}>
                    <TextField
                        label="Nach Verkauf"
                        value={currencyFormatter.format(balance + selectedValue)}
                        InputProps={{
                            readOnly: true
                        }} />
                </Grid>
            </Grid>
            <Grid container justifyContent="center">
                <Grid item key={0} xs={1}>
                    <Paper elevation={3} sx={{ background: 'lightblue', margin: 1, padding: '3px 0px', textAlign: 'center' }}>
                        {playersOnPositions[0]}-{playersOnPositions[1]}-{playersOnPositions[2]}
                    </Paper>
                </Grid>
                {possibleFormations.map((formation, i) => {
                    return (
                        <Grid item key={i} xs={1}>
                            <Paper elevation={3} sx={{
                                background: playableFormations.includes(formation) ? 'lightgreen' : 'indianred',
                                margin: 1, padding: '3px 0px', textAlign: 'center'
                            }}>
                                {formation[0]}-{formation[1]}-{formation[2]}
                            </Paper>
                        </Grid>
                    )
                })}
            </Grid>
            <DataGrid
                sx={{ marginTop: 1 }}
                width={window.innerWidth}
                checkboxSelection
                autoHeight
                rows={rows}
                columns={columns}
                pageSize={18}
                rowsPerPageOptions={[18]}
                initialState={{ sorting: { sortModel: [{ field: 'marketValue', sort: 'desc' }] } }}
                onSelectionModelChange={(s) => { setSelection(s) }}
            />
        </>
    )
}

export default LineupPlanner