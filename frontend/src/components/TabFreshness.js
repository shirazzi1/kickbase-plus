// How old the data in this tab is, per dataset.
//
// The header has carried one badge for the whole run since phase 1c. That is the right thing
// for "is the scraper alive", and the wrong resolution for "can I trust the table I am looking
// at": per-stage isolation means a failed stage leaves its file exactly where it was, with a
// date that still looks perfectly reasonable, while everything around it is current.
//
// So each tab says it for its own datasets. The judgement itself is freshness.js - this only
// decides what to draw and, deliberately, draws nothing at all when everything in the tab was
// written by the latest run. A row of green chips on every tab is decoration; a single orange
// one is information.

import Chip from "@mui/material/Chip"
import Stack from "@mui/material/Stack"
import Tooltip from "@mui/material/Tooltip"

import { CURRENT, datasetStatus, statusColour, statusLabel } from "./freshness"

// freshness.js keys its stage table by the timestamp file name; the timestamp index keys by the
// dataset. Both spellings meet here.
const chipColour = {
    stale: "warning",
    failed: "error",
    unknown: "default"
}

/**
 * One chip per dataset in this tab that is not current.
 *
 * @param {Array} datasets the dataset names, as the timestamp index keys them ("market")
 * @param {object} timestamps the /api/data/timestamps response
 * @param {object} manifest the run manifest out of it
 */
function TabFreshness({ datasets = [], timestamps = {}, manifest = null }) {
    const judged = datasets.map((name) => ({
        name,
        stamp: timestamps[name],
        status: datasetStatus(timestamps[name], manifest, name)
    }))

    const worth = judged.filter((entry) => entry.status !== CURRENT)

    if (worth.length === 0)
        return null

    return (
        <Stack direction="row" spacing={1} sx={{ padding: "0 15px 10px", flexWrap: "wrap" }} useFlexGap>
            {worth.map((entry) => {
                const when = entry.stamp?.time
                    ? new Date(entry.stamp.time).toLocaleString("de-DE")
                    : "unbekannt"

                const why = entry.status === "failed"
                    ? "Der Schritt, der diesen Datensatz schreibt, ist im letzten Lauf gescheitert. "
                        + "Angezeigt wird, was der letzte erfolgreiche Lauf geschrieben hat."
                    : entry.status === "stale"
                        ? "Der letzte Lauf hat diesen Datensatz nicht neu geschrieben."
                        : "Zu diesem Datensatz gibt es keine Lauf-Zuordnung, das Alter ist nicht zu beurteilen."

                return (
                    <Tooltip key={entry.name} arrow title={`Stand: ${when}. ${why}`}>
                        <Chip
                            size="small"
                            variant="outlined"
                            color={chipColour[entry.status] ?? "default"}
                            label={`${entry.name}: ${statusLabel(entry.status)}`}
                            sx={{ cursor: "help", borderColor: statusColour(entry.status) }}
                        />
                    </Tooltip>
                )
            })}
        </Stack>
    )
}

export default TabFreshness
