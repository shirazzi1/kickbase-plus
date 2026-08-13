// Import necessary dependencies from React
import React, { useState } from "react"

// Import Material-UI Components
import Box from "@mui/material/Box"
import Tab from "@mui/material/Tab"
import TabContext from "@mui/lab/TabContext"
import TabList from "@mui/lab/TabList"
import TabPanel from "@mui/lab/TabPanel"
import Paper from "@mui/material/Paper"
import Tooltip from "@mui/material/Tooltip"
import Typography from "@mui/material/Typography"
import { ThemeProvider, createTheme } from "@mui/material/styles"
import CssBaseline from "@mui/material/CssBaseline"
import Switch from "@mui/material/Switch"
import FormControlLabel from "@mui/material/FormControlLabel"
import Grid from "@mui/material/Grid"
import IconButton from "@mui/material/IconButton"
import CloseIcon from "@mui/icons-material/Close"
// import Button from "@mui/material/Button";

// Import custom components from the project
import Tagesplan from "./components/Tagesplan"
import MarketTable from "./components/MarketTable"
import TurnoversTable from "./components/TurnoversTable"
import TakenPlayersTable from "./components/TakenPlayersTable"
import FreePlayersTable from "./components/FreePlayersTable"
import TransferRevenueLineChart from "./components/TransferRevenueLineChart"
import LineupPlanner from "./components/LineupPlanner"
import HelpIcon from "./components/HelpIcon"
import MarketValueChangesTable from "./components/MarketValueChangesTable"
import TeamValueLineChart from "./components/TeamValueLineChart"
import Changelog from "./components/Changelog"
import LeagueUserTable from "./components/LeagueUserTable"
import SeasonStatsTable from "./components/SeasonStatsTable"
// import LivePoints from "./components/LivePoints"
import Balances from "./components/Balances"
import Battles from "./components/Battles"

// Import timestamps
import timestamp_main from "./data/timestamps/ts_main.json"
import timestamp_market from "./data/timestamps/ts_market.json"
import timestamp_market_value_changes from "./data/timestamps/ts_market_value_changes.json"
import timestamp_taken_players from "./data/timestamps/ts_taken_players.json"
import timestamp_free_players from "./data/timestamps/ts_free_players.json"
import timestamp_turnovers from "./data/timestamps/ts_turnovers.json"
import timestamp_team_values from "./data/timestamps/ts_team_values.json"
import timestamp_revenue_sum from "./data/timestamps/ts_revenue_sum.json"
import timestamp_league_user_stats from "./data/timestamps/ts_league_user_stats.json"
// import timestamp_live_points from "./data/timestamps/ts_live_points.json"
import timestamp_balances from "./data/timestamps/ts_balances.json"

// What each stage of the last run did. Every timestamp above carries the id of the run
// that wrote it, so this is what turns "there is a date here" into "this table is current".
import run_manifest from "./data/timestamps/ts_run_manifest.json"

import { datasetStatus, runStatus, runSummary, statusColour, statusLabel } from "./components/freshness"

// The datasets shown in the Dev tab, in the order the run writes them
const DATASETS = [
    ["market", "Market", timestamp_market],
    ["market_value_changes", "Market Value Changes", timestamp_market_value_changes],
    ["taken_players", "Taken Players", timestamp_taken_players],
    ["free_players", "Free Players", timestamp_free_players],
    ["balances", "Balances", timestamp_balances],
    ["turnovers", "Turnovers", timestamp_turnovers],
    ["revenue_sum", "Revenue Sum", timestamp_revenue_sum],
    ["team_values", "Team Values", timestamp_team_values],
    ["league_user_stats", "League User Stats", timestamp_league_user_stats]
    // ["live_points", "Live Points", timestamp_live_points]
]

// Create dark and light themes using Material-UI
const darkTheme = createTheme({ palette: { mode: "dark" } })
const lightTheme = createTheme({ palette: { mode: "light" } })

// Main App component
function App() {
  // State variables
  // The Tagesplan opens first: it is the only tab that says what changed since you last
  // looked, which is the question the rest of the tabs are opened to answer by hand.
  const [selectedTab, setSelectedTab] = useState("0")
  const [darkModeEnabled, setDarkModeEnabled] = useState(false)
  const [disclaimerVisible, setDisclaimerVisible] = useState(true);
  // const [refreshing, setRefreshing] = useState(false); // State to manage refreshing
  
  // Handlers
  const handleCloseDisclaimer = () => setDisclaimerVisible(false);

  // const RefreshButtonLivepoints = () => (
  //   <Button onClick={handleRefreshLivepoints} disabled={refreshing}>
  //     {refreshing ? "Refreshing..." : "Refresh Live Points"}
  //   </Button>
  // );

  // // Function to handle Live Points refresh button click
  // const handleRefreshLivepoints = async () => {
  //   // Set refreshing to true to indicate the refresh is in progress
  //   setRefreshing(true);

  //   try {
  //     const response = await fetch("/api/livepoints")
  //     const data = await response.json();
  //     // Handle the retrieved data (update state, etc.)
  //     console.log(data);
  //   } catch (error) {
  //     console.error("Error refreshing live points data:", error);
  //   } finally {
  //     // Set refreshing to false once the refresh is complete
  //     setRefreshing(false);
  //   }
  // };

  // Return the JSX for the App component
  // TODO: The what?
  return (
    // ThemeProvider enables theming using Material-UI themes
    <ThemeProvider theme={darkModeEnabled ? darkTheme : lightTheme}>
      {/* CssBaseline provides a consistent baseline style across browsers */}
      <CssBaseline />

      {/* Main container for the application */}
      {/* The merged market table needs the room for its 21 columns. Capped so it does not
          spread itself across an ultrawide monitor. */}
      <Box sx={{ maxWidth: "1800px", width: "100%", minWidth: "700px", margin: "auto", position: "relative", marginBottom: "100px"}}>

        {/* TabContext manages the state of the tabs */}
        <TabContext value={selectedTab}>

          {/* Top Layer of Navigation Bar */}
          <Grid container direction="row" justifyContent="space-between" alignItems="center" sx={{ borderBottom: 1, borderColor: "divider" }}>
            {/* Left side - Project Name, Links, and Version */}
            <Grid item>
              <Typography variant="h5" sx={{ fontFamily: "", fontWeight: "bold"}}>Kickbase Insights</Typography>          
            </Grid>

            <Grid item>
              <IconButton variant="button" component="a" href="https://uptime.k1da.de" target="_blank" rel="noopener noreferrer" sx={{ fontSize: "20px"}}>
                Uptime
              </IconButton>

              <IconButton variant="button" component="a" href="https://k1da.de" target="_blank" rel="noopener noreferrer" sx={{ fontSize: "20px"}}>
                Website
              </IconButton>

              <IconButton component="a" href="https://github.com/casudo/Kickbase-Insights" target="_blank" rel="noopener noreferrer" sx={{ fontSize: "20px"}}>
                GitHub
              </IconButton>
            </Grid>

            <Grid item sx={{ textAlign: "right" }}>
              <Typography variant="button" style={{ color: "green" }}>{process.env.REACT_APP_VERSION || "development"}</Typography><br/>
              {/* The colour follows the run, not the clock. It used to be green whatever
                  happened, so a scraper that had been failing for two days looked exactly
                  like one that had just finished. */}
              <Tooltip title={runSummary(run_manifest)} arrow>
                <Typography variant="button" style={{ color: statusColour(runStatus(run_manifest)), opacity: "0.7", cursor: "help" }}>
                  {new Date(timestamp_main.time).toLocaleString("de-DE")}
                  {runStatus(run_manifest) !== "current" && ` (${statusLabel(runStatus(run_manifest))})`}
                </Typography>
              </Tooltip>
            </Grid>
          </Grid>

          {/* Bottom Layer of Navigation Bar */}
          <Grid container direction="row" justifyContent="space-between" alignItems="center" sx={{ borderBottom: 1, borderColor: "divider" }}>
            <Grid item>
              {/* TabList contains the tabs for navigation */}
              <TabList onChange={(e, v) => setSelectedTab(v)}>
                <Tab label="Tagesplan" value="0" />
                <Tab label="Transfers" value="1" />
                <Tab label="Transfererlöse" value="2" />
                <Tab label="Spieler" value="3" />
                {/* <Tab label="Live" value="4" /> */}
                <Tab label="Liga" value="5" />
                <Tab label="Changelog" value="6" />
                <Tab label="Dev" value="7" />
              </TabList>
            </Grid>

            <Grid item>
              {/* Dark Mode toggle switch */}
              <FormControlLabel control={<Switch checked={darkModeEnabled} onChange={(e) => setDarkModeEnabled(e.target.checked)} />} label={<Typography variant="button" style={{ opacity: "0.7" }}>Dark Mode</Typography>} />
            </Grid>
          </Grid>

          {/* TabPanel contains the content for each tab */}
          <TabPanel sx={{ padding: 0 }} value="0">

            {/* What changed since the last runs. Everything else in this app shows a state;
                this shows the difference between two of them. */}
            <Paper sx={{ marginTop: "25px" }} elevation={5}>
              <Typography variant="h4" sx={{ padding: "15px" }}>Tagesplan <HelpIcon text="Was sich in den letzten 48 Stunden geändert hat, aus dem Vergleich aufeinanderfolgender Läufe: neue Listungen, Preissenkungen, Marktwertsprünge, ablaufende Kickbase-Angebote und Manager, denen der Spielraum ausgeht. Die Kennzeichnung sagt, wie dringend es ist - 'Jetzt' heißt, dass Warten bis zum nächsten Blick wahrscheinlich zu spät ist. Ein Ablaufdatum liefert Kickbase nur für seine eigenen Angebote, deshalb tauchen von Managern gelistete Spieler hier nie als 'Läuft ab' auf."/></Typography>
              <Tagesplan />
            </Paper>
          </TabPanel>

          <TabPanel sx={{ padding: 0 }} value="1">
            
            {/* "Transfers" related components */}
            <Paper sx={{ marginTop: "25px" }} elevation={5}>
              <Typography variant="h4" sx={{ padding: "15px" }}>Transfermarkt <HelpIcon text="Alle Spieler auf dem Transfermarkt. Hellblau hinterlegte Zeilen sind Free Agents, also direkt von Kickbase gelistet; alle anderen sind von Nutzern aus der Liga gelistet. 'Dein Gebot' zeigt dein laufendes Gebot und den Aufschlag auf den aktuellen Marktwert. Ein Ablaufdatum liefert Kickbase nur für die eigenen Angebote. 'Tage bis BEP' sind die Tage, die der Marktwert beim Zuwachs der letzten drei Tage braucht, um den Preis einzuholen; ein Strich heißt, dass der Marktwert gerade nicht steigt. Neben jeder Euro-Spalte steht derselbe Zuwachs relativ zum aktuellen Marktwert. 'Verdeckte Bieter', 'Mindestgebot' und 'Zwangsverkauf droht' rechnen mit den geschätzten Budgets aus den Balances - Kickbase verrät weder Kontostände noch wer bietet, also sind das Schätzungen; die Spaltenköpfe sagen jeweils, welche. Die Bieter-Übersicht über der Tabelle zeigt alle Manager nach geschätztem Maximalgebot."/></Typography>
              <MarketTable />
            </Paper>
            <Paper sx={{ marginTop: "25px" }} elevation={5}>
              <Typography variant="h4" sx={{ padding: "15px" }}>Marktwertveränderungen</Typography>
              <MarketValueChangesTable />
            </Paper>
            <Paper sx={{ marginTop: "25px" }} elevation={5}>
              <Typography variant="h4" sx={{ padding: "15px" }}>Aufstellungsplaner <HelpIcon text="Der aktuelle Kontostand kann eingegeben und Spieler in der letzten Spalte zum Verkaufen markiert werden. Der neue Kontostand wird dynamisch ausgerechnet. Mögliche Formationen werden über der Tabelle angezeigt: Spieler im Kader (blau), mögliche Formation (grün), nicht mögliche Formation (rot)" /></Typography>
              <LineupPlanner />
            </Paper>
          </TabPanel>

          <TabPanel sx={{ padding: 0 }} value="2">
            {/* "Transfererlöse" related components */}
            <Paper sx={{ marginTop: "25px" }} elevation={5}>
              <Typography variant="h4" sx={{ padding: "15px" }}>Transfererlöse <HelpIcon text="Liste alle verkauften Spieler und deren Erlöse. Gut zum recherchieren, welcher Spieler den meisten Gewinn oder Verlust erbracht hat."/></Typography>
              <TurnoversTable />
            </Paper>
            <Paper sx={{ marginTop: "25px" }} elevation={5}>
              <Typography variant="h4" sx={{ padding: "15px" }}>Summe der Transfererlöse <HelpIcon text="Zeigt den Gesamtgewinn oder Verlust des jeweiligen Spielers in der Saison an."/></Typography>
              <TransferRevenueLineChart darkModeEnabled={darkModeEnabled} />
            </Paper>
            <Paper sx={{ marginTop: "25px" }} elevation={5}>
              <Typography variant="h4" sx={{ padding: "15px" }}>Teamwert</Typography>
              <TeamValueLineChart darkModeEnabled={darkModeEnabled} />
            </Paper>
          </TabPanel>

          <TabPanel sx={{ padding: 0 }} value="3">
            {/* "Spieler" related components */}
            <Paper sx={{ marginTop: "25px" }} elevation={5}>
              <Typography variant="h4" sx={{ padding: "15px" }}>Gebundene Spieler</Typography>
              <TakenPlayersTable />
            </Paper>
            <Paper sx={{ marginTop: "25px" }} elevation={5}>
              <Typography variant="h4" sx={{ padding: "15px" }}>Freie Spieler</Typography>
              <FreePlayersTable />
            </Paper>
          </TabPanel>

          {/* <TabPanel sx={{ padding: 0 }} value="4"> */}
            {/* "Live" related components */}
            {/* <Paper sx={{ marginTop: "25px"}} elevation={5}> */}
              {/* <Typography variant="h4" sx={{ padding: "15px" }}>Live Punkte <HelpIcon text="Um die Live Punkte zu benutzen, aktualisiert die Punkte mit den dafür vorgesehenen Knopf. Anschließend muss die Website einmal neu geladen werden."/> <RefreshButtonLivepoints/> </Typography> */}
              {/* <LivePoints /> */}
            {/* </Paper> */}
          {/* </TabPanel> */}

          <TabPanel sx={{ padding: 0 }} value="5">
            {/* "Liga" related components */}
            <Paper sx={{ marginTop: "25px"}} elevation={5}>
              <Typography variant="h4" sx={{ padding: "15px" }}>Tabelle <HelpIcon text="Die Statistiken beziehen sich auf die aktuell laufende Saison."/></Typography>
              <LeagueUserTable />
            </Paper>            
            <Paper sx={{ marginTop: "25px"}} elevation={5}>
              <Typography variant="h4" sx={{ padding: "15px" }}>Saison Statistiken <HelpIcon text="Die Statistiken beziehen sich auf die aktuell laufende Saison."/></Typography>
              <SeasonStatsTable />
            </Paper>                   
            <Paper sx={{ marginTop: "25px"}} elevation={5}>
              <Typography variant="h4" sx={{ padding: "15px" }}>Balances <HelpIcon text="Ungefähre Kontostände der Manager. Ohne den Schalter zählen nur Transfers. Mit dem Schalter kommen täglicher Login-Bonus und Erfolge dazu - beides geschätzt: der tägliche Login wird für alle unterstellt, die Erfolge werden aus dem Spielstand abgeleitet. Erfolge, die sich nicht herleiten lassen, fehlen."/></Typography>
              <Balances />
            </Paper>
            <Paper sx={{ marginTop: "25px"}} elevation={5}>
              <Typography variant="h4" sx={{ padding: "15px" }}>Battles <HelpIcon text="Herausragende Leistungen von Spielern in der Liga."/></Typography>
              <Battles />
            </Paper>        
          </TabPanel>

          <TabPanel sx={{ padding: 0 }} value="6">
            {/* "Changelog" related components */}
            <Paper sx={{ marginTop: "25px"}} elevation={5}>
              <Typography variant="h4" sx={{ padding: "15px" }}>Changelog</Typography>
              <Changelog/>
            </Paper>
          </TabPanel>

          <TabPanel sx={{ padding: 0 }} value="7">
            {/* "Dev" related components */}
            <Paper sx={{ marginTop: "25px"}} elevation={5}>
              <Typography variant="h4" sx={{ padding: "15px" }}>Development</Typography>

              {/* What the last run did, stage by stage. This is the file that makes the
                  timestamps below mean something: without it a date only says that some
                  run ended, not that it produced anything. */}
              <Typography variant="h6" sx={{ padding: "0px 15px 0px 15px" }}>Letzter Lauf</Typography>
              <Typography variant="body1" sx={{ padding: "0px 15px 15px 15px" }}>
                Lauf: <Typography variant="button" style={{ opacity: "0.7" }}>{run_manifest.runId || "unbekannt"}</Typography><br/>
                Ergebnis: <Typography variant="button" style={{ color: statusColour(runStatus(run_manifest)) }}>{runSummary(run_manifest)}</Typography><br/>
                Beendet: <Typography variant="button" style={{ opacity: "0.7" }}>{new Date(timestamp_main.time).toLocaleString("de-DE")}</Typography>
              </Typography>

              <Typography variant="body1" component="div" sx={{ padding: "0px 15px 15px 15px" }}>
                {(run_manifest.stages || []).map((stage) => (
                  <div key={stage.name}>
                    <Typography variant="button" style={{ color: statusColour(stage.status === "ok" ? "current" : "failed") }}>
                      {stage.status === "ok" ? "✓" : "✗"} {stage.name}
                    </Typography>
                    <Typography variant="button" style={{ opacity: "0.7", marginLeft: "8px" }}>
                      {stage.durationSeconds}s
                    </Typography>
                    {stage.error && (
                      <Typography variant="body2" style={{ opacity: "0.7", marginLeft: "20px" }}>
                        {stage.error}
                      </Typography>
                    )}
                  </div>
                ))}
              </Typography>

              {/* Display Timestamps of various JSON data files.
                  Coloured per dataset, because per-stage isolation means they can disagree:
                  a failed stage leaves its file exactly where it was, with a date that
                  still looks perfectly reasonable. */}
              <Typography variant="h6" sx={{ padding: "0px 15px 0px 15px" }}>Timestamps</Typography>
              <Typography variant="body1" component="div" sx={{ padding: "0px 15px 15px 15px" }}>
                {DATASETS.map(([name, label, stamp]) => {
                  const status = datasetStatus(stamp, run_manifest, name)

                  return (
                    <div key={name}>
                      {label}: <Typography variant="button" style={{ color: statusColour(status), opacity: "0.7" }}>
                        {new Date(stamp.time).toLocaleString("de-DE")}
                      </Typography>
                      <Typography variant="button" style={{ color: statusColour(status), marginLeft: "8px" }}>
                        {statusLabel(status)}
                      </Typography>
                      {stamp.rows !== undefined && (
                        <Typography variant="button" style={{ opacity: "0.5", marginLeft: "8px" }}>
                          {stamp.rows} Zeilen
                        </Typography>
                      )}
                    </div>
                  )
                })}
              </Typography>
            </Paper>
          </TabPanel>          
        </TabContext>
        
        {/* Additional Disclaimer popup */}
        {disclaimerVisible && (
          <Paper sx={{ position: "fixed", bottom: 0, left: "50%", transform: "translateX(-50%)", width: "440px", textAlign: "center" }} elevation={24}>
            <Typography variant="h6" sx={{ padding: "10px" }}>Disclaimer</Typography>
            <Typography sx={{ padding: "0px 15px 15px" }}>
              This site is for educational and non-profit purposes only.<br />
              All trademarks, logos and brand names are the property of their respective owners.<br/><br/>
              <a href="mailto:contact@k1da.de.de">contact@k1da.de</a>
            </Typography>
            <IconButton onClick={handleCloseDisclaimer} sx={{ position: "absolute", top: "8px", right: "8px" }}>
              <CloseIcon />
            </IconButton>
          </Paper>
        )}

      </Box>
    </ThemeProvider>
  )
}

// Export the App component as the default export
export default App