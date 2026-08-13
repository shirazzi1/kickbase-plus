import { render, screen } from "@testing-library/react"

import ManagerDossier, { formatHold } from "./ManagerDossier"

// A manager with data behind every metric, and one who has done nothing but exist. The
// backend writes both kinds: every league member gets an entry, traded or not.
const busy = {
    managerId: "1",
    managerName: "Anna",
    holdDuration: { medianDays: 2.5, medianSeconds: 216000, n: 22, roundTripsWithinAnHour: 3 },
    purchaseMarkup: { meanPercent: 4.2, medianPercent: 3.1, n: 18, buysConsidered: 22 },
    momentumBuys: { share: 0.778, risingBuys: 14, n: 18, windowDays: 7 },
    topClubs: {
        clubs: [
            { teamId: "5", teamName: "Borussia Dortmund", buys: 6 },
            { teamId: "13", teamName: "FC Augsburg", buys: 4 }
        ],
        n: 22
    },
    activityWindow: {
        hourCounts: [0, 0, 0, 0, 0, 0, 1, 2, 0, 0, 0, 0, 3, 0, 0, 0, 0, 0, 0, 9, 4, 0, 0, 0],
        peakHour: 19,
        n: 19,
        timezone: "Europe/Berlin"
    }
}

const idle = {
    managerId: "2",
    managerName: "Zoe",
    holdDuration: { medianDays: null, medianSeconds: null, n: 0, roundTripsWithinAnHour: 0 },
    purchaseMarkup: { meanPercent: null, medianPercent: null, n: 0, buysConsidered: 0 },
    momentumBuys: { share: null, risingBuys: 0, n: 0, windowDays: 7 },
    topClubs: { clubs: [], n: 0 },
    activityWindow: { hourCounts: Array(24).fill(0), peakHour: null, n: 0, timezone: "Europe/Berlin" }
}

const doc = (managers, coverage) => ({
    marketValueCoverage: coverage ?? { players: 20, of: 20 },
    managers: Object.fromEntries(managers.map((manager) => [manager.managerId, manager]))
})

describe("formatHold", () => {
    it("keeps the fast end readable", () => {
        // Round trips through the market run in seconds. As "0 Min." they look like a missing
        // value next to an n that says there were six sales.
        expect(formatHold(14)).toBe("14 Sek.")
        expect(formatHold(216000)).toBe("2 Tage 12 Std.")
    })

    it("has no answer without a median", () => {
        expect(formatHold(null)).toBeNull()
    })
})

describe("ManagerDossier", () => {
    it("says the profiles are missing rather than breaking the tab", () => {
        // No prop, so the component asks its loader - which finds nothing under Jest, exactly
        // as it finds nothing on a deployment where the stage has never run
        render(<ManagerDossier />)

        expect(screen.getByText(/Noch keine Manager-Profile/)).toBeTruthy()
        expect(screen.getByText(/manager_profiles/)).toBeTruthy()
    })

    it("says the same for a document without managers", () => {
        render(<ManagerDossier profiles={{ marketValueCoverage: { players: 0, of: 0 }, managers: {} }} />)

        expect(screen.getByText(/Noch keine Manager-Profile/)).toBeTruthy()
    })

    it("shows every metric with the number of transfers behind it", () => {
        render(<ManagerDossier profiles={doc([busy])} />)

        expect(screen.getByText("Anna")).toBeTruthy()

        // Hold duration, markup, momentum and the favourite clubs, each with its own n
        expect(screen.getByText("2 Tage 12 Std.")).toBeTruthy()
        expect(screen.getByText("aus 22 Verkäufen")).toBeTruthy()
        expect(screen.getByText("+4,2 %")).toBeTruthy()
        expect(screen.getByText(/aus 18 von 22 Käufen/)).toBeTruthy()
        expect(screen.getByText("77,8 %")).toBeTruthy()
        expect(screen.getByText(/14 von 18 Käufen/)).toBeTruthy()
        expect(screen.getByText("Borussia Dortmund (6)")).toBeTruthy()
        expect(screen.getByText(/aus 22 Käufen mit Klub-Angabe/)).toBeTruthy()
    })

    it("calls the round trips what they are", () => {
        render(<ManagerDossier profiles={doc([busy])} />)

        // Bought off the market and sold straight back within the hour: a trade towards
        // Kickbase's transfer bonus, and the reason a busy manager's median reads in hours
        expect(screen.getByText("3 Rundläufe - Bonus-Farming")).toBeTruthy()
    })

    it("shows the activity window with its peak hour", () => {
        render(<ManagerDossier profiles={doc([busy])} />)

        expect(screen.getByText("aktivste Stunde 19:00")).toBeTruthy()
        expect(screen.getByText(/aus 19 Buchungen, Zeitzone Europe\/Berlin/)).toBeTruthy()
        // One bar per hour of the day, each labelled with its own count
        expect(screen.getByLabelText("19:00 - 9 Buchungen")).toBeTruthy()
        expect(screen.getByLabelText("6:00 - 1 Buchung")).toBeTruthy()
    })

    it("writes 'keine Datenlage' where a metric has no n", () => {
        render(<ManagerDossier profiles={doc([idle])} />)

        expect(screen.getByText("Zoe")).toBeTruthy()
        // A zero here would be a measurement: "0 % Aufschlag" means paying exactly the market
        // value, which is a very different claim from "no purchase could be checked"
        expect(screen.getAllByText("keine Datenlage").length).toBe(5)
        expect(screen.getByText("kein Verkauf erfasst")).toBeTruthy()
        expect(screen.getByText("0 von 0 Käufen prüfbar")).toBeTruthy()
        // And no round trip chip for a manager who has never sold anything
        expect(screen.queryByText(/Bonus-Farming/)).toBeNull()
        expect(screen.queryByText(/aktivste Stunde/)).toBeNull()
    })

    it("warns when the market value stage delivered nothing this run", () => {
        render(<ManagerDossier profiles={doc([busy, idle], { players: 0, of: 178 })} />)

        // Without this the empty markup and momentum of every manager read as a league that
        // never buys anything
        expect(screen.getByText(/Marktwert-Vorstufe lieferte diesmal nichts/)).toBeTruthy()
    })

    it("puts a card up for every manager", () => {
        render(<ManagerDossier profiles={doc([busy, idle])} />)

        expect(screen.getByText("Anna")).toBeTruthy()
        expect(screen.getByText("Zoe")).toBeTruthy()
    })
})
