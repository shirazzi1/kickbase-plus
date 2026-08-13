// One tab crashing must not take the other seven with it.
//
// This exists because of something the move to runtime fetching gave up. A compile-time
// `import data from "../data/market.json"` made webpack resolve the file during the build, so a
// dataset that was missing or unparsable failed the build and nobody ever saw a broken page.
// The bundle now builds against no data at all, and a row with a field the component did not
// expect throws inside render - which in React unmounts the whole tree from the root.
//
// The shape check in hooks/dataContracts.js catches the coarse version of that. This catches
// the rest: it is the layer that decides a bad Marktwert row costs the Transfermarkt tab and
// nothing else.
//
// The error is shown, not just logged. A tab that renders "something went wrong" with no
// detail is how a data bug becomes unreportable.

import { Component } from "react"

import Alert from "@mui/material/Alert"
import AlertTitle from "@mui/material/AlertTitle"
import Button from "@mui/material/Button"
import Typography from "@mui/material/Typography"

class TabErrorBoundary extends Component {
    constructor(props) {
        super(props)
        this.state = { error: null }
    }

    static getDerivedStateFromError(error) {
        return { error }
    }

    componentDidCatch(error, info) {
        // The console is where a stack trace is worth something, so it goes there in full
        // while the page shows the message only
        console.error(`Fehler im Bereich "${this.props.name}":`, error, info)
    }

    render() {
        if (!this.state.error)
            return this.props.children

        return (
            <Alert
                severity="error"
                sx={{ margin: "15px" }}
                action={
                    <Button color="inherit" size="small" onClick={() => this.setState({ error: null })}>
                        Nochmal versuchen
                    </Button>
                }
            >
                <AlertTitle>{this.props.name} konnte nicht dargestellt werden</AlertTitle>
                <Typography variant="body2">
                    {String(this.state.error?.message || this.state.error)}
                </Typography>
                <Typography variant="caption" sx={{ opacity: 0.7, display: "block", marginTop: "8px" }}>
                    Die übrigen Bereiche funktionieren weiter. Details stehen in der Browser-Konsole.
                </Typography>
            </Alert>
        )
    }
}

export default TabErrorBoundary
