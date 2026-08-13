// The component renders the times in the browser's timezone, and the assertions below name
// them. Pinned here rather than left to the machine running the suite, which is the same
// reason the scraper pins TZ for the history store.
process.env.TZ = "Europe/Berlin"

import { render, screen } from "@testing-library/react"

// The virtual mock of ../data/events.json that used to stand here is gone with the import it
// stood in for: the tab fetches events.json now, and a file the scrape has not written is a 404
// rather than an unresolvable module. The cases below hand the events in as a prop, and the
// component then requests nothing at all.

const Tagesplan = require("./Tagesplan").default

// The day the grouping is judged against. Fixed, so "Heute" and "Gestern" do not depend on
// when the suite runs.
const NOW = new Date("2026-08-13T12:00:00+02:00")

const events = [
    {
        key: "neue_listung|2|a", type: "neue_listung", severity: 3,
        ts: "2026-08-13T10:00:00+02:00", playerId: "2", managerId: null,
        text: "Neu auf dem Markt: Max Muster für 1,80 Mio. € (Marktwert 2,00 Mio. €, unter Marktwert), gelistet von Zoe."
    },
    {
        key: "cash_hortung|u1|b", type: "cash_hortung", severity: 2,
        ts: "2026-08-13T08:00:00+02:00", playerId: null, managerId: "u1",
        text: "Zoe hortet Geld: +3,00 Mio. € über 3 Snapshots, kein Kauf in 48 Std."
    },
    {
        key: "preissenkung|7|c", type: "preissenkung", severity: 2,
        ts: "2026-08-12T20:00:00+02:00", playerId: "7", managerId: null,
        text: "Preis gesenkt: Leon Goretzka von 5,00 Mio. € auf 4,50 Mio. € (-10 %, Marktwert 4,00 Mio. €)."
    }
]

describe("Tagesplan", () => {
    // The first state in production, and the one a user sees before the second run has ever
    // finished. It has to read as "nothing has happened yet", not as "something is broken".
    it("explains itself when there is nothing to show", () => {
        render(<Tagesplan events={[]} now={NOW} />)

        expect(screen.getByText("Noch keine Ereignisse — der Verlauf baut sich ab dem nächsten Lauf auf")).toBeTruthy()
        expect(screen.getByText(/vergleicht zwei aufeinanderfolgende Läufe/)).toBeTruthy()
    })

    it("survives an events.json that is not a list", () => {
        render(<Tagesplan events={null} now={NOW} />)

        expect(screen.getByText("Noch keine Ereignisse — der Verlauf baut sich ab dem nächsten Lauf auf")).toBeTruthy()
    })

    describe("with events", () => {
        beforeEach(() => render(<Tagesplan events={events} now={NOW} />))

        it("groups by day and counts each group", () => {
            expect(screen.getByText("Heute — 2 Ereignisse")).toBeTruthy()
            expect(screen.getByText("Gestern — 1 Ereignis")).toBeTruthy()
        })

        it("shows every event's text", () => {
            events.forEach((event) => expect(screen.getByText(event.text)).toBeTruthy())
        })

        it("labels the severity of each event", () => {
            expect(screen.getByText("Jetzt")).toBeTruthy()
            expect(screen.getAllByText("Beachten").length).toBe(2)
        })

        it("names the event types in German", () => {
            expect(screen.getByText("Neue Listung")).toBeTruthy()
            expect(screen.getByText("Preissenkung")).toBeTruthy()
            expect(screen.getByText("Geld gehortet")).toBeTruthy()
        })

        it("shows the time of each event", () => {
            expect(screen.getByText("10:00")).toBeTruthy()
            expect(screen.getByText("08:00")).toBeTruthy()
            expect(screen.getByText("20:00")).toBeTruthy()
        })
    })

    // The 48 hour window can straddle three calendar days, and the third one is not
    // "Gestern" - it gets its date, so a row cannot be misread as more recent than it is.
    it("dates a day that is neither today nor yesterday", () => {
        render(<Tagesplan events={[{
            key: "x", type: "mv_sprung", severity: 2, ts: "2026-08-11T22:00:00+02:00",
            playerId: "7", managerId: null, text: "Marktwert gefallen: Leon Goretzka -1,20 Mio. € auf 8,80 Mio. € (-12 %)."
        }]} now={NOW} />)

        expect(screen.getByText(/Dienstag, 11\.08\. — 1 Ereignis/)).toBeTruthy()
    })
})
