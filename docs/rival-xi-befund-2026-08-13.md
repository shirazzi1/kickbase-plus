# Befund: Rivalen-Aufstellung und `player_variance.json`

*13.08.2026 — Vorstufe zu A5 (Trailing-Mode-Aufstellungsberater, `docs/game-plan-2026-08-13.md` §4.3)*

Diese Untersuchung ist ohne einen einzigen API-Aufruf entstanden: es liegen keine Credentials
vor. Belegt wird deshalb ausschliesslich aus dem Code und aus den Dateien, die ein echter Lauf
im Haupt-Checkout hinterlassen hat. Jede Aussage trägt ihre Fundstelle als `Datei:Zeile`. Wo
der Code nichts sagt, steht ausdrücklich "unbelegt" — die Lücken sind hier das Ergebnis, nicht
der Platzhalter.

Ergebnis in zwei Sätzen: **`player_variance.json` ist nicht gebaut worden**, weil die
Punkte-Historie pro Spieler nirgends im Lauf existiert und ihr Abruf 466 zusätzliche Requests
pro Lauf kostete. **Eine beobachtete Rivalen-XI gibt es heute nicht**, aber es gibt genau einen
Kandidaten im Code — den Legacy-Live-Endpoint — und ein Ein-Request-Experiment, das die Frage
entscheidet.

---

## 1. Teil 1: `player_variance.json` — nicht gebaut

### 1.1 Was der Lauf pro Spieler heute überhaupt holt

Es sind drei Responses, und keine trägt einen Punkteverlauf:

| Aufruf | URL | Punkte darin |
| --- | --- | --- |
| `leagues.player_statistics()` (`backend/kickbase/v4/leagues.py:149`) | `/v4/competitions/1/players/{pid}?leagueId={lid}` | `"tp"` — ein Skalar |
| `leagues.player_marketvalue()` (`backend/kickbase/v4/leagues.py:244`) | `/v4/competitions/1/players/{pid}/marketValue/{days}` | keine |
| `competitions.get_team_overview()` (`backend/kickbase/v4/competitions.py:32`) | `/v4/competitions/1/teams/{tid}/teamprofile` | `"ap"` — ein Skalar |

Im Detail:

- **Spielerprofil.** Gelesen werden `"tid"`, `"pos"`, `"fn"`, `"ln"`, `"mv"` (`main.py:424-428`),
  `"stxt"` (`main.py:312`), `"opl"` (`backend/miscellaneous.py:286`) und `"tp"`
  (`main.py:553`). `"tp"` sind die Gesamtpunkte der Saison — eine Zahl, kein Verlauf. Ein
  realistisches Fixture derselben Response steht in `tests/test_market_table.py:251`
  (`{"i", "st", "stl", "stxt"}`); auch dort kein Punktefeld.
- **Marktwert-Historie.** Einträge sind `{"dt", "mv"}` (`backend/profiles.py:112`,
  `backend/miscellaneous.py:325`). Marktwerte, keine Punkte.
- **Teamprofil.** Die `"it"`-Liste landet als `players` in `STATIC_teams.json`
  (`backend/kickbase/v4/competitions.py:57-65,78`). Auf der Platte hat dort jeder Spieler
  genau diese Felder: `ap, i, iotm, lst, mv, mvgl, mvt, n, ofc, pim, pos, prob, sdmvt, st, tid`
  (18 Teams, 466 Spieler, `frontend/src/data/STATIC_teams.json`). `"ap"` ist ein
  Durchschnitt — so heisst es auch im Modell: `Market_Players.averagePoints = "ap"`
  (`backend/kickbase/endpoints/leagues.py:200`).

Damit ist µ faktisch gratis zu haben (`ap`, allerdings für 156 der 466 Spieler `null`), **σ
aber nicht**: aus einem Mittelwert lässt sich keine Streuung rekonstruieren. Und σ ist genau
das, worauf §4.3 aufbaut — `mu + k·sigma` ist ohne σ kein Modell, sondern eine Sortierung nach
`ap`. Eine Datei namens `player_variance.json` mit `sigma: null` wäre ein Versprechen ohne
Deckung.

### 1.2 Spieltagspunkte gibt es im Projekt nur pro Manager

`leagues.user_performance()` (`backend/kickbase/v4/leagues.py:363`) holt
`/v4/leagues/{lid}/managers/{uid}/performance`, und `miscellaneous.matchday_points()` liest
daraus `performance["it"][-1]["it"][*]["mdp"]` (`backend/miscellaneous.py:890-896`) — Punkte
pro gespieltem Spieltag, aber pro **Manager**. Ein Spielerbezug existiert in dieser Response
laut Code nicht. Genutzt wird sie für die Achievements (`main.py:1095`).

### 1.3 Auch der History-Store akkumuliert nichts Brauchbares

Historisiert werden `market`, `market_value_changes`, `balances`, `taken_players`
(`backend/miscellaneous.py:960-965`). Die `taken_players`-Einträge tragen
`owner/playerId/teamId/position/firstName/lastName/buyPrice/marketValue/status/trend`
(`main.py:532-543`) — kein Punktefeld. `free_players` trägt `"points": tp` (`main.py:553`),
ist aber bewusst **nicht** historisiert (`backend/miscellaneous.py:953-954`). `live_points`
ist ebenfalls ausgenommen (`backend/miscellaneous.py:955-957`). Es sammelt sich also nichts an,
woraus sich rückblickend Spieltagspunkte pro Spieler ergäben.

### 1.4 Was ein Fetch kosten würde

**Es gibt im Repo keinen Endpoint, der Spieltagspunkte pro Spieler liefert.** Vollständige
Liste aller aufgerufenen URLs: `backend/kickbase/v4/leagues.py:64,92,149,244,277,311,342,363,378,407,425`,
`backend/kickbase/v4/competitions.py:32,93`, `backend/kickbase/v4/user.py:47,88`. Ob ein
`/v4/competitions/1/players/{pid}/performance` existiert, ist ohne Credentials nicht
feststellbar; der Manager-Zwilling (`.../managers/{uid}/performance`,
`backend/kickbase/v4/leagues.py:363`) legt die Form nahe, ist aber **kein Beleg**.

Kosten, falls es ihn gibt — ein Request pro Spieler und Lauf:

- 466 Spieler (`STATIC_teams.json`, 18 Teams).
- Heute holt `prefetch_players()` zwei Requests pro Spieler
  (`backend/kickbase/v4/leagues.py:128-131`) = 932 pro Lauf. Ein dritter wäre **+466 pro Lauf,
  also +50 % auf der Spielerebene**.
- Bei 6 Läufen am Tag: **+2.796 Requests pro Tag**.
- Die Parallelität ist absichtlich klein gehalten: `MAX_PLAYER_WORKERS = 8`
  (`backend/kickbase/v4/leagues.py:25`), begründet mit "this runs against the user's own
  Kickbase account, and being throttled costs more than it saves"
  (`backend/kickbase/v4/leagues.py:23-24`). Der Lauf würde also nicht nur teurer, sondern
  spürbar länger.

Das Muster von `backend/profiles.py` hilft hier nicht weiter: `leagues.cached_market_value()`
(`backend/kickbase/v4/leagues.py:216-230`) kann nur herausgeben, was der Lauf ohnehin geholt
hat — und Punkte holt er nicht. Deshalb: nicht gebaut.

### 1.5 Zwei Wege zu σ, die keinen einzigen zusätzlichen Request kosten

**(a) `"tp"` in einen historisierten Datensatz aufnehmen.** `taken_free_players()` liest für
jeden Spieler ohnehin das Profil (`main.py:491`), `"tp"` ist im Prozess also schon vorhanden
(`main.py:553` benutzt es für die freien Spieler). Trüge `taken_players` ein
`"points": player_stats.get("tp", 0)`, schriebe der History-Store es sechsmal täglich mit
(`backend/miscellaneous.py:960-965`) — und die Differenz über ein Spieltagsfenster **ist** die
Spieltagspunktzahl. Kosten: null Requests. Preis: kein Backfill, der Verlauf beginnt am Tag der
Änderung, und ein σ aus zwei oder drei Spieltagen ist noch keins. Genau das ist der zweite
Grund, `player_variance.json` jetzt nicht auszuliefern: es gäbe monatelang eine Datei, deren
Zahlen niemand benutzen dürfte.

**(b) Die Feed-Items, die der Lauf schon herunterlädt und wegwirft.** `leagues.transfers()`
pagiert den kompletten Activity Feed und filtert auf `t == 15`
(`backend/kickbase/v4/leagues.py:317`); gecacht wird nur das Gefilterte
(`backend/kickbase/v4/leagues.py:326`). Dabei fallen unter anderem weg:

- Typ 17 — "Matchday final points and ranking" (`backend/miscellaneous.py:155`),
- Typ 8 — "final matchday points entry" (`backend/kickbase/endpoints/leagues.py:121,146`).

Was in diesen Items steht, sagt der Code nicht. Sie sind aber **bereits bezahlt**: sie kommen
in jeder Feed-Seite mit. Ein Lauf, der die Nicht-15er-Typen einmal mitloggt, kostet null
zusätzliche Requests und beantwortet die Frage endgültig — für Teil 1 wie für Teil 2.

---

## 2. Teil 2: Ist die tatsächliche Aufstellung der Rivalen beobachtbar?

### 2.1 Was heute auf der Platte liegt, ist der Kader — nicht die Elf

Besitz kommt aus der per-Liga-Liste `"opl"` (`backend/miscellaneous.get_player_owner`,
`backend/miscellaneous.py:267-297`) und landet in `taken_players.json` (`main.py:532-543`).
Kein Feld dieser Einträge unterscheidet aufgestellt von Bank, keines nennt eine Formation.
`LineupPlanner.js` baut auf genau dieser Datei (`frontend/src/components/LineupPlanner.js:15`)
und plant Kader, nicht Aufstellungen (`frontend/src/components/LineupPlanner.js:18`).

Das in §4.3 benannte "load-bearing risk" ist damit codebelegt: Transferhistorie und `"opl"`
liefern *Besitz*, und ein Spieler auf beiden Kadern hebt sich nur auf, **wenn er auf beiden
Seiten wirklich spielt**.

### 2.2 Der einzige Kandidat im Code: der Legacy-Live-Endpoint

`leagues.live_points()` (`backend/kickbase/v4/leagues.py:384-410`) ruft
`https://api.kickbase.com/leagues/{lid}/live` auf — **ohne `/v4`**. Der Docstring sagt selbst,
was das bedeutet: Legacy-(v1)-Pfad, kein v4-Äquivalent implementiert, Feature "on-hold", der
Aufruf "unverified against the current API" (`backend/kickbase/v4/leagues.py:403-406`).

Die dokumentierte Response (`backend/kickbase/v4/leagues.py:388-401`):

```json
{"u": [{"id": "xxxx", "n": "USERNAME", "t": 419, "st": 12199, "pl": [ ... ]}]}
```

`"t"` sind Live-Punkte, `"st"` Gesamtpunkte, **`"pl"` die "Players of the user"**
(`backend/kickbase/v4/leagues.py:397`). `main.py:930-945` liest daraus pro Spieler:
`"id"`, `"tid"`, `"fn"`, `"n"`, `"nr"` (Rückennummer), `"t"` (Punkte), `"g"` (Tore),
`"a"` (Assists), `"r"`, `"y"`, `"yr"` (Karten).

**Das ist die einzige Stelle im ganzen Repo, an der eine Spielerliste pro fremdem Manager
existiert.** Überall sonst heisst `"pl"` "placement"
(`backend/kickbase/endpoints/leagues.py:26`, `main.py:875`, `main.py:1097`) — die Live-Response
ist die Ausnahme.

Was der Code **nicht** hergibt:

- Kein Feld, das aufgestellt von Bank trennt.
- Kein Beleg, ob `"pl"` die Elf oder den ganzen Kader enthält. "Players of the user"
  (`backend/kickbase/v4/leagues.py:397`) lässt beides zu.
  `frontend/src/components/LivePoints.js:44` zählt `players.filter(p => p.points > 0)` als
  "who have scored" — das setzt voraus, dass Spieler mit 0 Punkten in der Liste vorkommen
  können, was mit beiden Lesarten verträglich ist. **Kein Beleg.**

Und der Zustand drumherum ist so ungünstig wie möglich:

- Die Stufe ist im Lauf deaktiviert (`main.py:132`), erreichbar nur über
  `/api/livepoints` in Flask (`app.py:46-81`).
- `frontend/src/data/live_points.json` auf der Platte ist eine **Attrappe** mit einem
  einzigen Nutzer `"u1"/"TestUser"` und einem Spieler — die Feldform lässt sich also auch
  nicht aus den Daten bestätigen.
- Der Live-Tab im Frontend ist auskommentiert (`frontend/src/App.js:49,69,99-104`).
- `live_points` ist aus dem History-Store ausgenommen (`backend/miscellaneous.py:955-957`):
  es wird nichts akkumuliert, aus dem sich später eine Aufstellungshistorie ergäbe.
- Im README steht die Feature-Lage ausdrücklich: "The live points feature is currently
  on-hold and not present as of v2.4.0!" (`README.md:62`).

### 2.3 Das entscheidende Experiment (ein Request)

Die offene Frage — Elf oder Kader — ist nicht durch Codelesen zu klären, aber mit **einem**
Aufruf: `/leagues/{lid}/live` abrufen und für einen Rivalen `len(entry["pl"])` gegen dessen
Kadergrösse aus `taken_players.json` halten.

- `len(pl)` ≈ 11 (bzw. genau die gespielte Formation) → die Aufstellung **ist** beobachtbar,
  A5 verliert seinen Gate-Blocker für den laufenden Spieltag.
- `len(pl)` = Kadergrösse → es bleibt Besitz, und Rivalen-XI-Inferenz ist Pflicht.
- HTTP-Fehler → der Legacy-Pfad ist tot, und damit gibt es im Projekt **keinen** Kandidaten
  mehr.

Das ist ein Nachmittag mit eingeloggtem Token, kein Projekt — und es entscheidet, ob A5
überhaupt gebaut werden kann.

### 2.4 Was prinzipiell fehlt, auch im besten Fall

- **Zeitpunkt.** Selbst wenn `"pl"` die Elf ist, ist sie erst *ab* Anpfiff sichtbar. Die
  Entscheidung, für die §4.3 den Berater will (wen stelle ich auf, bevor der Spieltag
  beginnt), fällt vorher. Brauchbar ist die Live-Elf damit für den **laufenden** Spieltag
  (Swing-Meter) und — über gesammelte Spieltage — als Trainingsmaterial für eine Inferenz.
  Für die Aufstellungsentscheidung selbst ist sie strukturell zu spät.
- **Keine Paarungen.** `match_days.json` kennt pro Spieltag nur `firstMatch` und `lastMatch`
  (`backend/kickbase/v4/competitions.py:107-113`). "Hat dieser Spieler schon gespielt?" ist
  daraus nicht beantwortbar, nur "das Spieltagsfenster läuft".
- **`"prob"` ist unaufgeklärt.** Die Teamprofil-Spieler tragen ein Feld `"prob"` mit fünf
  diskreten Werten 1..5 (Verteilung über die 466 Spieler: 5→126, 3→116, 1→83, 4→83, 2→58),
  und **keine Zeile im Repo liest es** (geprüft über `backend/`, `main.py`, `app.py`,
  `frontend/src`). Was es bedeutet, ist unbelegt. Sollte es eine Einsatz- oder
  Startelf-Einschätzung sein, läge das `start_probability` aus §4.3 bereits ohne einen
  einzigen zusätzlichen Request auf der Platte. Das zu klären ist billiger als jedes
  Inferenzmodell.

### 2.5 Verhältnis zum Swing-Meter (A3)

Der Live-Swing-Meter aus A3 liegt als Commit `5b2b2a2` vor (noch nicht auf `main`: das Repo
auf `main` hat kein `frontend/src/components/SwingMeter.js`). Seine Commit-Nachricht nimmt die
hier belegte Lage schon vorweg — er rechnet bewusst ohne Aufstellungsinferenz und schreibt
"falls aufgestellt" an jeden offenen Teil. Diese Untersuchung bestätigt das aus dem Code und
ergänzt, wo der eine Aufruf steht, der die Annahme prüfbar macht.

---

## 3. Empfohlene nächste Schritte

Alle vier kosten null zusätzliche API-Requests und sind einzeln committebar:

1. **Feed-Typen 8 und 17 einmal mitloggen** (`backend/kickbase/v4/leagues.py:317`). Sie werden
   heute heruntergeladen und verworfen. Klärt in einem Lauf, ob Spieltags-Endpunktstände pro
   Manager oder gar pro Spieler im Feed stehen.
2. **Ein `/leagues/{lid}/live`-Abruf** und `len(pl)` gegen die Kadergrösse prüfen (§2.3).
   Entscheidet A5s Gate.
3. **`"prob"` klären** (§2.4). Möglicherweise liegt `start_probability` schon auf der Platte.
4. **`"tp"` in `taken_players` aufnehmen** (§1.5a), falls σ später gewollt ist. Ab dann sammelt
   der History-Store die Spieltagspunkte pro Spieler von selbst — je früher, desto eher ist
   `player_variance.json` mehr als ein leeres Versprechen.
