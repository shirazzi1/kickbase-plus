// The three things a table can say before it has any rows.
//
// Compile-time imports meant a component either had its data or the build had failed. Fetching
// gives every table a "not yet" and a "could not", and thirteen tables inventing their own
// wording for that is how a dashboard stops reading like one thing.
//
// So: one spinner, one error, one "the scrape has not written this yet". The dataset's German
// name comes from dataContracts.js, so the message names the table rather than the file.

import Alert from "@mui/material/Alert"
import AlertTitle from "@mui/material/AlertTitle"
import Box from "@mui/material/Box"
import Button from "@mui/material/Button"
import CircularProgress from "@mui/material/CircularProgress"
import Typography from "@mui/material/Typography"

import { datasetLabel } from "../hooks/dataContracts"
import { ERROR, LOADING } from "../hooks/useJsonData"

/**
 * The spinner, with the name of what is being loaded next to it.
 *
 * Deliberately not a skeleton table: a fake table that turns into a real one shifts every
 * column width, and these tables have up to 21 of them.
 */
export function DataLoading({ name }) {
    return (
        <Box sx={{ display: "flex", alignItems: "center", gap: "12px", padding: "30px 20px" }}>
            <CircularProgress size={22} />
            <Typography variant="body2" sx={{ opacity: 0.7 }}>
                {datasetLabel(name)} wird geladen …
            </Typography>
        </Box>
    )
}

/**
 * A fetch that did not work, with the reason and a way to try again.
 *
 * The reason is shown rather than swallowed. The three realistic causes read very differently
 * - the container is starting, the reverse proxy does not forward /api, the file on disk is
 * damaged - and only the message tells them apart.
 */
export function DataError({ name, error, onRetry }) {
    return (
        <Alert
            severity="error"
            sx={{ margin: "15px" }}
            action={onRetry && <Button color="inherit" size="small" onClick={onRetry}>Erneut laden</Button>}
        >
            <AlertTitle>{datasetLabel(name)} konnte nicht geladen werden</AlertTitle>
            {error || "Unbekannter Fehler."}
        </Alert>
    )
}

/**
 * A dataset the scrape has not produced yet.
 *
 * Not an error, and it must not look like one: on a fresh deployment this is what most tabs
 * show until the first run finishes.
 */
export function DataMissing({ name, children }) {
    return (
        <Box sx={{ padding: "40px 20px", textAlign: "center" }}>
            <Typography variant="h6" sx={{ opacity: 0.8 }}>
                {datasetLabel(name)}: noch keine Daten
            </Typography>
            <Typography variant="body2" sx={{ opacity: 0.6, marginTop: "10px", maxWidth: "640px", marginX: "auto" }}>
                {children || "Diesen Datensatz schreibt erst ein Scrape-Lauf. Bis dahin ist hier nichts zu zeigen."}
            </Typography>
        </Box>
    )
}

/**
 * Whichever of the three applies, or null once the data is there.
 *
 * Callers use it as a guard: `const gate = dataGate(...); if (gate) return gate`. That keeps
 * the hook call above the early return, which the rules of hooks require, and keeps the
 * component's own body unaware that its rows arrive over the network.
 */
export function dataGate({ name, status, error, missing, reload, missingText, rows }) {
    if (status === LOADING)
        return <DataLoading name={name} />

    if (status === ERROR)
        return <DataError name={name} error={error} onRetry={reload} />

    if (missing)
        return <DataMissing name={name}>{missingText}</DataMissing>

    // `rows` is only passed by the components that cannot render an empty dataset at all -
    // the ones that read row zero, or seed a dropdown from the first entry. Most tables can,
    // and the DataGrid says "keine Einträge" for them by itself.
    if (rows !== undefined && (!rows || rows.length === 0))
        return <DataMissing name={name}>{missingText}</DataMissing>

    return null
}
