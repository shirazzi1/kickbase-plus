import React from 'react'
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Accordion from '@mui/material/Accordion'
import AccordionSummary from '@mui/material/AccordionSummary'
import AccordionDetails from '@mui/material/AccordionDetails'
import Typography from '@mui/material/Typography'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'

import { DataGrid } from '@mui/x-data-grid'

import SwingMeter from "./SwingMeter"

import { useJsonData } from "../hooks/useJsonData"
import { dataGate } from "./DataState"

const DATASET = "live_points.json"

// Why the tab is reachable again, and what it can and cannot show.
//
// The Live tab was commented out in App.js because live_points.json is written by
// /api/livepoints and not by a scheduled run - so on a fresh deployment the file does not
// exist, and a compile-time import of it failed the build with "Module not found". That reason
// is gone: a missing dataset is a 404 and an empty state now.
//
// What has not changed is that nothing refreshes it. main.py does not run the live stage, and
// the one endpoint that would performs a full Kickbase login per request, which is why no
// button here calls it (see app.py::get_live_points). So this tab shows the last live snapshot
// that was ever taken, with the swing meter's own chip saying how old it is - red beyond an
// hour - and "Neu laden" re-reads the file rather than fetching from Kickbase.
export const STALE_BY_DESIGN = "Die Live-Punkte holt kein Scrape-Lauf. Angezeigt wird der "
    + "letzte Abruf; das Alter steht im Chip über den Balken. 'Neu laden' liest die Datei neu, "
    + "es fragt nicht bei Kickbase nach."

function LivePoints() {
    const { status, data, missing, error, reload } = useJsonData(DATASET)

    const gate = dataGate({
        name: DATASET, status, error, missing, reload, rows: data,
        missingText: "Für diesen Tab muss die Live-Punkte-Stage einmal gelaufen sein. Der "
            + "reguläre Scrape-Lauf führt sie nicht aus, deshalb ist hier auf einem frischen "
            + "Deployment nichts zu sehen."
    })

    if (gate)
        return gate

    // Step 1: Sort user data based on live points in descending order
    const sortedUserData = [...data].sort((a, b) => b.livePoints - a.livePoints);

    // Step 2: Assign placements to users based on their sorted order
    const rankedUserData = sortedUserData.map((user, index) => ({
      ...user,
      placement: index + 1,
    }));

  // Columns for the player table
  const columns = [
    { field: 'fullName', headerName: 'Name', flex: 2 },
    { field: 'points', headerName: 'Points', flex: 1 },
    { field: 'goals', headerName: 'Goals', flex: 1 },
    { field: 'assists', headerName: 'Assists', flex: 1 },
    { field: 'yellowCards', headerName: 'Yellow Card', flex: 1 },
    { field: 'yellowRedCards', headerName: 'Yellow/Red Card', flex: 1 },
    { field: 'redCards', headerName: 'Red Card', flex: 1 },
  ];

  // Function to render an accordion for each user
  const renderUserAccordion = (user) => (
    <Accordion key={user.userId}>
      {/* Display user header with placement, username, and live points */}
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Typography variant="h6">
            {user.placement}. {user.userName}
            <span style={{ fontSize: '13px', fontStyle: 'italic', marginLeft: '13px' }}>
                {user.livePoints} / {user.totalPoints} (with {user.players.filter(player => player.points > 0).length} players, who have scored)
            </span>
        </Typography>
      </AccordionSummary>

      {/* Display table with player stats */}
      <AccordionDetails>
        <DataGrid
            rows={user.players}
            columns={columns}
            pageSize={11}
            autoHeight
            disableColumnSelector
            disableColumnFilter
            disableColumnMenu
            // Step 3: Provide a custom getRowId function
            getRowId={(row) => row.playerId}
            // Step 4: Add the sortModel prop for initial sorting
            sortModel={[
                {
                    field: 'points',
                    sort: 'desc', // 'desc' for descending order
                },
            ]}
        />
      </AccordionDetails>
    </Accordion>
  );

  // Step 3: Render an accordion for each user
  const userAccordions = rankedUserData.map(renderUserAccordion);

  // Step 4: Display the accordions and tables
    return (
        <Box>
            {/* The live data is a snapshot nothing refreshes, so the tab says that before it
                shows numbers that look live */}
            <Alert severity="info" sx={{ marginBottom: "15px" }}>
                {STALE_BY_DESIGN}
                <Button color="inherit" size="small" onClick={reload} sx={{ marginLeft: "8px" }}>
                    Neu laden
                </Button>
            </Alert>

            {/* Above the per-manager tables: how much of the gap to one chosen rival is
                already settled and how much is still on the pitch */}
            <SwingMeter entries={data} />
            {userAccordions}
        </Box>
    );
}

export default LivePoints
