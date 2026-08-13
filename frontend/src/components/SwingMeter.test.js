import { render, screen } from "@testing-library/react"

// The data files are written by the scraper at runtime, so the test brings its own, the way
// the market table test does.

// A window that is open at any "now": the meter then classifies by points, which is the
// only interesting phase.
jest.mock("../data/match_days.json", () => ([
    { day: 7, firstMatch: "2020-01-01T00:00:00Z", lastMatch: "2999-01-01T00:00:00Z" }
]))

jest.mock("../data/timestamps/ts_live_points.json", () => ({ time: "2020-01-01T00:00:00Z" }))

// Three managers. "Ich" has two players left, "Max" one, "Zoe" is only there to give the
// match day enough played players for an average.
jest.mock("../data/taken_players.json", () => ([
    { owner: "Ich", playerId: "1", firstName: "Manuel", lastName: "Neuer", position: "TW", status: 0 },
    { owner: "Ich", playerId: "2", firstName: "Joshua", lastName: "Kimmich", position: "MF", status: 0 },
    { owner: "Ich", playerId: "3", firstName: "Jamal", lastName: "Musiala", position: "MF", status: 2 },
    { owner: "Max", playerId: "4", firstName: "Serge", lastName: "Gnabry", position: "ST", status: 0 },
    { owner: "Max", playerId: "5", firstName: "Alphonso", lastName: "Davies", position: "ABW", status: 0 },
    { owner: "Zoe", playerId: "6", firstName: "Sven", lastName: "Ulreich", position: "TW", status: 0 },
    { owner: "Zoe", playerId: "7", firstName: "Leon", lastName: "Goretzka", position: "MF", status: 0 },
    { owner: "Zoe", playerId: "8", firstName: "Kingsley", lastName: "Coman", position: "ST", status: 0 }
]))

const SwingMeter = require("./SwingMeter").default

// Five players have scored 100 points between them, so one player is worth 20
const entries = [
    { userId: "1", userName: "Ich", livePoints: 20, totalPoints: 100, players: [{ playerId: "1", points: 20, fullName: "Manuel Neuer (1)" }] },
    { userId: "2", userName: "Max", livePoints: 45, totalPoints: 200, players: [{ playerId: "4", points: 45, fullName: "Serge Gnabry (7)" }] },
    { userId: "3", userName: "Zoe", livePoints: 35, totalPoints: 300, players: [
        { playerId: "6", points: 10, fullName: "Sven Ulreich (1)" },
        { playerId: "7", points: 10, fullName: "Leon Goretzka (8)" },
        { playerId: "8", points: 15, fullName: "Kingsley Coman (11)" }
    ] }
]

describe("SwingMeter", () => {
    // Managers are sorted, so "Ich" is the own team and "Max" the first available rival
    beforeEach(() => render(<SwingMeter entries={entries} />))

    it("names the gap and what is still playing", () => {
        expect(screen.getByText("Du liegst 25 Punkte hinter Max – 2 deiner Spieler spielen noch, 1 bei Max"))
            .toBeTruthy()
    })

    it("shows the settled part of the gap", () => {
        expect(screen.getByText("Fix – beide Spieler haben gespielt")).toBeTruthy()
        // 20 own points against 45, and no open player can change that part
        expect(screen.getByText("-25 Punkte")).toBeTruthy()
    })

    it("keeps the shared part visible and explains why it is empty", () => {
        expect(screen.getByText("Geteilt, läuft noch (falls aufgestellt) – 0 Spieler")).toBeTruthy()
        expect(screen.getByText(/gehört ein Spieler nur einem Manager/)).toBeTruthy()
    })

    it("splits the open differentials by side", () => {
        expect(screen.getByText("Differential, läuft noch (falls aufgestellt) – 2 für dich")).toBeTruthy()
        expect(screen.getByText("Differential, läuft noch (falls aufgestellt) – 1 für Max")).toBeTruthy()
        expect(screen.getByText("+40 Punkte")).toBeTruthy()
        expect(screen.getByText("-20 Punkte")).toBeTruthy()
    })

    it("derives the range from the match day average", () => {
        expect(screen.getByText(
            "Spanne: von -45 bis +15 Punkten, wenn jeder noch offene Spieler den Spieltags-Ø von 20 Punkten holt."))
            .toBeTruthy()
    })

    it("lists the open players on both sides by name", () => {
        expect(screen.getByText("Joshua Kimmich")).toBeTruthy()
        expect(screen.getByText("Jamal Musiala")).toBeTruthy()
        expect(screen.getByText("Alphonso Davies")).toBeTruthy()
        // Already played, so not part of what is left
        expect(screen.queryByText("Manuel Neuer")).toBeNull()
    })

    it("says how old the live points are", () => {
        expect(screen.getByText(/^Live-Daten: vor /)).toBeTruthy()
    })

    it("names the match day and its phase", () => {
        expect(screen.getByText("Spieltag 7 – läuft")).toBeTruthy()
    })
})
