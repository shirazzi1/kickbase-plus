import {
    BEFORE,
    DONE,
    LINEUP_SIZE,
    MATCH_DURATION,
    RUNNING,
    averagePointsPerPlayer,
    buildRoster,
    classifyRoster,
    currentMatchDay,
    decomposeSwing,
    swingBounds,
    swingHeadline,
} from "./swingFormulas"

// Synthetic data throughout, in the shape the files on disk have: live_points.json is a
// list of managers with a players array, taken_players.json a flat list whose "owner" is
// the manager's *name*, match_days.json a list of {day, firstMatch, lastMatch}.

const matchDays = [
    { day: 1, firstMatch: "2026-08-28T18:30:00Z", lastMatch: "2026-08-30T15:30:00Z" },
    { day: 2, firstMatch: "2026-09-04T18:30:00Z", lastMatch: "2026-09-06T15:30:00Z" },
]

const player = (playerId, points, rest = {}) => ({ playerId, points, name: `Spieler ${playerId}`, ...rest })

describe("currentMatchDay", () => {
    it("finds the match day whose window contains the moment", () => {
        expect(currentMatchDay(matchDays, Date.parse("2026-08-29T16:00:00Z")))
            .toMatchObject({ day: 1, phase: RUNNING })
    })

    it("keeps a match day running until the last kickoff has been played out", () => {
        const lastKickoff = Date.parse("2026-08-30T15:30:00Z")
        expect(currentMatchDay(matchDays, lastKickoff + MATCH_DURATION - 1)).toMatchObject({ phase: RUNNING })
        expect(currentMatchDay(matchDays, lastKickoff + MATCH_DURATION + 1)).toMatchObject({ day: 1, phase: DONE })
    })

    it("shows the last finished match day between two match days", () => {
        // What the live view keeps showing until the next kickoff
        expect(currentMatchDay(matchDays, Date.parse("2026-09-02T12:00:00Z")))
            .toMatchObject({ day: 1, phase: DONE })
    })

    it("looks ahead to the first match day before the season starts", () => {
        expect(currentMatchDay(matchDays, Date.parse("2026-08-13T12:00:00Z")))
            .toMatchObject({ day: 1, phase: BEFORE })
    })

    it("has nothing to say without a schedule", () => {
        expect(currentMatchDay([], Date.parse("2026-08-29T16:00:00Z"))).toBeNull()
        expect(currentMatchDay(null, Date.parse("2026-08-29T16:00:00Z"))).toBeNull()
        expect(currentMatchDay([{ day: 1, firstMatch: "kein Datum", lastMatch: "auch nicht" }], 0)).toBeNull()
    })
})

describe("buildRoster", () => {
    const takenPlayers = [
        { owner: "Max", playerId: "1", firstName: "Koen", lastName: "Casteels", position: "TW", status: 0 },
        { owner: "Max", playerId: "2", firstName: "Jamal", lastName: "Musiala", position: "MF", status: 2 },
        { owner: "Jonny", playerId: "3", firstName: "Sven", lastName: "Ulreich", position: "TW", status: 0 },
    ]

    it("joins the live points onto the owned squad", () => {
        const roster = buildRoster({
            userName: "Max",
            livePlayers: [{ playerId: "1", points: 42, fullName: "Koen Casteels (1)" }],
            takenPlayers,
        })

        expect(roster).toEqual([
            { playerId: "1", name: "Koen Casteels", position: "TW", status: 0, points: 42 },
            // Owned, no points yet — the player the swing is about
            { playerId: "2", name: "Jamal Musiala", position: "MF", status: 2, points: 0 },
        ])
    })

    it("keeps a live player the ownership snapshot does not know", () => {
        // The two files are written at different times, and dropping him would drop his
        // points out of the gap
        const roster = buildRoster({
            userName: "Max",
            livePlayers: [{ playerId: "99", points: 12, fullName: "Neuer Zugang (9)" }],
            takenPlayers,
        })

        expect(roster.map((entry) => entry.playerId)).toEqual(["1", "2", "99"])
        expect(roster[2]).toMatchObject({ name: "Neuer Zugang (9)", points: 12, status: null })
    })

    it("reads ids as strings, since the two files disagree on the type", () => {
        const roster = buildRoster({
            userName: "Max",
            livePlayers: [{ playerId: 1, points: 42, fullName: "Koen Casteels (1)" }],
            takenPlayers,
        })

        expect(roster.find((entry) => entry.playerId === "1").points).toBe(42)
        expect(roster).toHaveLength(2)
    })

    it("gives a manager without a matching owner name only their live players", () => {
        // taken_players.json has no user id, so the join is on the display name: a rename
        // between two scrapes must not silently hand out someone else's squad
        expect(buildRoster({ userName: "Maximilian", livePlayers: [], takenPlayers })).toEqual([])
    })
})

describe("classifyRoster", () => {
    const roster = [player("1", 42), player("2", 0), player("3", -2)]

    it("counts a player with points as played and one without as still to come", () => {
        const { played, open } = classifyRoster(roster, RUNNING)
        expect(played.map((entry) => entry.playerId)).toEqual(["1", "3"])
        expect(open.map((entry) => entry.playerId)).toEqual(["2"])
    })

    it("has played nothing before the first kickoff", () => {
        const { played, open } = classifyRoster(roster, BEFORE)
        expect(played).toHaveLength(0)
        expect(open).toHaveLength(3)
    })

    it("has nothing left open once the match day is over", () => {
        // Including the player who never got on the pitch: he can no longer score
        const { played, open } = classifyRoster(roster, DONE)
        expect(played).toHaveLength(3)
        expect(open).toHaveLength(0)
    })
})

describe("decomposeSwing", () => {
    it("puts the whole current gap into the finished part", () => {
        const decomposition = decomposeSwing({
            ownPlayers: [player("1", 30), player("2", 0)],
            rivalPlayers: [player("3", 48), player("4", 0), player("5", 0)],
            phase: RUNNING,
        })

        expect(decomposition.gap).toBe(-18)
        expect(decomposition.ownPlayed).toEqual({ count: 1, points: 30 })
        expect(decomposition.rivalPlayed).toEqual({ count: 1, points: 48 })
        expect(decomposition.ownOpen.map((entry) => entry.playerId)).toEqual(["2"])
        expect(decomposition.rivalOpen.map((entry) => entry.playerId)).toEqual(["4", "5"])
    })

    it("cancels a player who is open on both rosters instead of counting him twice", () => {
        // Kickbase gives a player to one manager only, so this happens when the ownership
        // snapshot and the live points were written on different sides of a transfer
        const decomposition = decomposeSwing({
            ownPlayers: [player("1", 0), player("2", 0)],
            rivalPlayers: [player("1", 0), player("3", 0)],
            phase: RUNNING,
        })

        expect(decomposition.shared.map((entry) => entry.playerId)).toEqual(["1"])
        expect(decomposition.ownOpen.map((entry) => entry.playerId)).toEqual(["2"])
        expect(decomposition.rivalOpen.map((entry) => entry.playerId)).toEqual(["3"])
    })

    it("keeps a contested player out of the gap even when only one side has his points", () => {
        // The real shape of a snapshot skew: the live endpoint credits the points to one of
        // the two managers, so he is finished on that side and open on the other. Comparing
        // only the open players would let him through and put his 20 points in the gap.
        const decomposition = decomposeSwing({
            ownPlayers: [player("1", 30), player("shared", 20)],
            rivalPlayers: [player("2", 8), player("shared", 0)],
            phase: RUNNING,
        })

        expect(decomposition.shared.map((entry) => entry.playerId)).toEqual(["shared"])
        expect(decomposition.gap).toBe(22)
        expect(decomposition.ownPlayed).toEqual({ count: 1, points: 30 })
        expect(decomposition.ownOpen).toHaveLength(0)
        expect(decomposition.rivalOpen).toHaveLength(0)
    })

    it("shows the points of a contested player from whichever side has them", () => {
        const decomposition = decomposeSwing({
            ownPlayers: [player("shared", 0, { name: "eigene Sicht" })],
            rivalPlayers: [player("shared", 20, { name: "Sicht des Rivalen" })],
            phase: RUNNING,
        })

        expect(decomposition.shared[0]).toMatchObject({ name: "Sicht des Rivalen", points: 20 })
    })

    it("counts how many players each side may still field", () => {
        const nine = Array.from({ length: 9 }, (unused, index) => player(`o${index}`, 5))
        const decomposition = decomposeSwing({
            ownPlayers: [...nine, player("bank1", 0), player("bank2", 0), player("bank3", 0)],
            rivalPlayers: [player("r1", 5)],
            phase: RUNNING,
        })

        // Nine played, so two of the three open players can still be on the pitch
        expect(decomposition.ownStartersLeft).toBe(2)
        expect(decomposition.ownOpen).toHaveLength(3)
        expect(decomposition.ownFieldable).toBe(2)
        expect(decomposition.rivalStartersLeft).toBe(LINEUP_SIZE - 1)
    })

    it("has nothing fieldable once eleven players have scored", () => {
        // The regular end of a match day: the eleven are done, the bench is still "open"
        const eleven = Array.from({ length: 11 }, (unused, index) => player(`o${index}`, 5))
        const decomposition = decomposeSwing({
            ownPlayers: [...eleven, player("bank1", 0), player("bank2", 0)],
            rivalPlayers: [player("r1", 5)],
            phase: RUNNING,
        })

        expect(decomposition.ownOpen).toHaveLength(2)
        expect(decomposition.ownFieldable).toBe(0)
    })

    it("never reports negative room in the lineup", () => {
        const twelve = Array.from({ length: 12 }, (unused, index) => player(`o${index}`, 5))
        const decomposition = decomposeSwing({ ownPlayers: twelve, rivalPlayers: [], phase: RUNNING })
        expect(decomposition.ownStartersLeft).toBe(0)
        expect(decomposition.ownFieldable).toBe(0)
    })

    it("has an all-open, zero gap before the match day starts", () => {
        const decomposition = decomposeSwing({
            ownPlayers: [player("1", 0)],
            rivalPlayers: [player("2", 0)],
            phase: BEFORE,
        })

        expect(decomposition.gap).toBe(0)
        expect(decomposition.ownOpen).toHaveLength(1)
        expect(decomposition.rivalOpen).toHaveLength(1)
    })
})

describe("averagePointsPerPlayer", () => {
    const entries = [
        { players: [player("1", 10), player("2", 20), player("3", 0)] },
        { players: [player("4", 30), player("5", 40), player("6", 20)] },
    ]

    it("averages over the players who have played, not over the squads", () => {
        // 120 points from five players who scored; the pointless one has not played yet
        expect(averagePointsPerPlayer(entries, RUNNING)).toBeCloseTo(24)
    })

    it("has no reference value while too few players have played", () => {
        expect(averagePointsPerPlayer([{ players: [player("1", 10), player("2", 20)] }], RUNNING)).toBeNull()
        expect(averagePointsPerPlayer(entries, BEFORE)).toBeNull()
        expect(averagePointsPerPlayer([], RUNNING)).toBeNull()
    })
})

describe("swingBounds", () => {
    const decomposition = decomposeSwing({
        ownPlayers: [player("1", 30), player("2", 0), player("3", 0)],
        rivalPlayers: [player("4", 48), player("5", 0)],
        phase: RUNNING,
    })

    it("spans the gap by what the open differentials could add", () => {
        const bounds = swingBounds(decomposition, 10)
        expect(bounds).toMatchObject({ ownSwing: 20, rivalSwing: 10 })
        expect(bounds.ceiling).toBe(2)
        expect(bounds.floor).toBe(-28)
    })

    it("counts no more open players than the lineup has room for", () => {
        const nine = Array.from({ length: 9 }, (unused, index) => player(`o${index}`, 5))
        const crowded = decomposeSwing({
            ownPlayers: [...nine, player("bank1", 0), player("bank2", 0), player("bank3", 0)],
            rivalPlayers: [player("r1", 5)],
            phase: RUNNING,
        })

        // Three open players, but only two of them can be fielded: 45 own points against 5,
        // plus the two players still allowed on the pitch
        expect(swingBounds(crowded, 10).ownSwing).toBe(20)
        expect(swingBounds(crowded, 10).ceiling).toBe(40 + 20)
    })

    it("leaves ceiling and floor open without a reference value", () => {
        // A range of "0 to 0" would read as a settled match day
        const bounds = swingBounds(decomposition, null)
        expect(bounds.ceiling).toBeNull()
        expect(bounds.floor).toBeNull()
        expect(bounds.ownSwing).toBeNull()
    })
})

describe("swingHeadline", () => {
    // The banner counts what can still be fielded, the same number the bars use
    const headline = (gap, ownOpen, rivalOpen, ownFieldable = ownOpen, rivalFieldable = rivalOpen) => swingHeadline(
        {
            gap,
            ownOpen: new Array(ownOpen).fill(player("x", 0)),
            rivalOpen: new Array(rivalOpen).fill(player("y", 0)),
            ownFieldable,
            rivalFieldable,
        },
        { rivalName: "Max" })

    it("names the direction and what is left", () => {
        expect(headline(-18, 3, 0)).toBe("Du liegst 18 Punkte hinter Max – 3 deiner Spieler spielen noch")
        expect(headline(18, 2, 1)).toBe("Du liegst 18 Punkte vor Max – 2 deiner Spieler spielen noch, 1 bei Max")
    })

    it("keeps the singular readable", () => {
        expect(headline(-1, 1, 0)).toBe("Du liegst 1 Punkt hinter Max – einer deiner Spieler spielt noch")
    })

    it("says the gap is settled once nothing can be fielded any more", () => {
        expect(headline(-18, 0, 0)).toBe("Du liegst 18 Punkte hinter Max – kein Spieler kann noch punkten")
        expect(headline(0, 0, 0)).toBe("Gleichstand mit Max – kein Spieler kann noch punkten")
        // The regular end of a match day: three open players, all of them on the bench. The
        // bars below the banner say zero, so the banner must not promise three.
        expect(headline(-18, 3, 2, 0, 0)).toBe("Du liegst 18 Punkte hinter Max – kein Spieler kann noch punkten")
    })

    it("says höchstens where the squad has more open players than the lineup has room for", () => {
        expect(headline(-18, 3, 0, 2, 0))
            .toBe("Du liegst 18 Punkte hinter Max – höchstens 2 deiner 3 offenen Spieler können noch spielen")
        expect(headline(-18, 3, 0, 1, 0))
            .toBe("Du liegst 18 Punkte hinter Max – höchstens 1 deiner 3 offenen Spieler kann noch spielen")
        expect(headline(4, 0, 4, 0, 2)).toBe("Du liegst 4 Punkte vor Max – höchstens 2 von 4 bei Max")
    })

    it("mentions the rival's open players even when none of yours are left", () => {
        expect(headline(5, 0, 2)).toBe("Du liegst 5 Punkte vor Max – 2 bei Max")
    })
})
