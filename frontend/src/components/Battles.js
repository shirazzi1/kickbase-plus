import React from "react";
import Box from "@mui/material/Box";
import Accordion from "@mui/material/Accordion";
import AccordionSummary from "@mui/material/AccordionSummary";
import AccordionDetails from "@mui/material/AccordionDetails";
import Typography from "@mui/material/Typography";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import { DataGrid } from "@mui/x-data-grid";
import Avatar from "@mui/material/Avatar";

import { useJsonData } from "../hooks/useJsonData";
import { dataGate } from "./DataState";

const DATASET = "league_user_stats.json";

function Battles() {
    const { status, data, missing, error, reload } = useJsonData(DATASET);

    // Define the battles (accordion)
    const battles = [
        { type: "Spieltagsdominator", valueField: "mdWins", label: "Siege", explanation: "Die meisten Spieltagssiege" },
        { type: "Punktejäger", valueField: "maxPoints", label: "Punkte", explanation: "Die meisten Punkte an einem Spieltag" },
        { type: "Transferkönig", valueField: "trades", label: "Transfers", explanation: "Die meisten Transfers der Saison" },
        { type: "Angriffslustig", valueField: "pointsForwards", label: "Punkte", explanation: "Die meisten Punkte mit Angreifern" },
        { type: "Saubermann", valueField: "pointsGoalKeeper", label: "Punkte", explanation: "Die meisten Punkte mit dem Towart" },
        { type: "Abwehrbollwerk", valueField: "pointsDefenders", label: "Punkte", explanation: "Die meisten Punkte mit Abwehrspielern" },
        { type: "Fädenzieher", valueField: "pointsMidFielders", label: "Punkte", explanation: "Die meisten Punkte mit Mittelfeldspielern" },
    ];

    // Function to calculate the placement of the users, based on the value (Wins, points or transfers) in the given category
    const calculatePlacement = (sortedUsers, valueField) => {
        return sortedUsers.map((user, index) => ({
            ...user,
            placement: index + 1,
            value: user[valueField],
        }));
    };

    // Every hook above this line: the gate returns early, and the rules of hooks do
    // not allow that before the last of them has run.
    // rows: every accordion names the leader, so there is nothing to render without one
    const gate = dataGate({
        name: DATASET, status, error, missing, reload, rows: data,
        missingText: "Die Battles vergleichen die Manager der Liga miteinander. Die Zahlen dafür "
            + "schreibt der Schritt 'league_user_stats' eines Scrape-Laufs."
    });

    if (gate)
        return gate;

    // Function to render an accordion for each battle
    const battleAccordions = battles.map(({ type, valueField, label, explanation }) => {
        // Sort the users based on the valueField in descending order
        const sortedUsers = [...data].sort((a, b) => b[valueField] - a[valueField]);
        const usersWithPlacement = calculatePlacement(sortedUsers, valueField);

        return (
            <Accordion key={type}>
                {/* Display Battles summary/header */}
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                    <Typography variant="h6">
                        {/* Category name */}
                        {type}
                    </Typography>
                    {/* Display the 1st user with name and profile pic */}
                    <Box display="flex" alignItems="center" sx={{ marginLeft: "13px" }}>
                        {/* Profile picture */}
                        {usersWithPlacement[0].placement === 1 && (
                            <Avatar src={usersWithPlacement[0].profilePic} alt={usersWithPlacement[0].userName} sx={{ marginLeft: "13px" }} />
                        )}
                        {/* Username and valueField */}
                        <Typography component="span" style={{ fontStyle: "italic", fontSize: "15px", marginLeft: "5px"}}>
                            {usersWithPlacement[0].userName} ({usersWithPlacement[0][valueField].toLocaleString("de-DE")} {label})
                        </Typography>
                    </Box>
                </AccordionSummary>
                
                {/* Explanation of Battle category */}
                <Typography style={{ margin: "15px"}}>
                    {explanation}
                </Typography>

                {/* Display table with all users and their valueField */}
                <AccordionDetails>
                    <DataGrid
                        rows={usersWithPlacement}
                        columns={[
                            {
                                field: "placement",
                                headerName: "Platz",
                                type: "number",
                                width: 100,
                                headerAlign: "center",
                                align: "center"
                            },
                            {
                                field: "userName",
                                headerName: "Manager",
                                headerAlign: "center",
                                align: "left",
                                flex: 1,
                                renderCell: (params) => 
                                    <div style={{ display: "flex", alignItems: "center" }}>
                                        <Avatar src={params.row.profilePic} alt={params.row.userName} sx={{ marginRight: 1 }} />
                                        {params.value}
                                    </div>
                            },
                            {
                                field: valueField,
                                headerName: label,
                                headerAlign: "center",
                                align: "center",
                                flex: 1,
                                type: "number",
                                valueFormatter: (params) => params.value.toLocaleString("de-DE")
                            },
                        ]}
                        pageSize={usersWithPlacement.length}
                        autoHeight
                        disableColumnSelector
                        disableColumnFilter
                        disableColumnMenu
                        getRowId={(row) => row.userId}
                        sortModel={[
                            {
                            field: valueField,
                            sort: "desc",
                            },
                        ]}
                    />
                </AccordionDetails>
            </Accordion>
        );
    });

  return <Box>{battleAccordions}</Box>;
}

export default Battles;
