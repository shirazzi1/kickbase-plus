# Kontostand-Verlauf pro Manager

Ein Klick auf eine Zeile der Balances-Tabelle öffnet eine Liste aller Ereignisse, die
den Kontostand dieses Managers verändert haben: das Startbudget, gefolgt von jedem Kauf
und Verkauf.

Heute zeigt die Tabelle nur das Ergebnis. Wie es zustande kommt, steht nirgends — und ein
Kontostand von −40 Mio. ohne Herleitung lässt sich weder nachvollziehen noch anzweifeln.

## Datenmodell

Die Events hängen als `events`-Array am bestehenden User-Eintrag in `balances.json`.
Sie entstehen in derselben Schleife, die den Kontostand berechnet, und können deshalb
nicht von ihm abweichen. Eine getrennte Datei würde diese Kopplung verstecken, statt sie
zu garantieren.

```json
{
  "userId": "1573526",
  "username": "Blida FC",
  "profilePic": null,
  "teamValue": 160087058.0,
  "balance": 14769781.0,
  "maxBid": 57702757.0,
  "events": [
    {
      "date": "2026-08-01T18:00:00Z",
      "type": "start",
      "amount": 50000000,
      "balance": 50000000,
      "playerName": null,
      "playerImage": null,
      "teamId": null,
      "tradePartner": null
    },
    {
      "date": "2026-08-01T18:10:57Z",
      "type": "buy",
      "amount": -2387664,
      "balance": 47612336,
      "playerName": "Müller",
      "playerImage": "https://kickbase.b-cdn.net/content/file/4b5913efdf3d4852a6f220421034c402.png",
      "teamId": "8",
      "tradePartner": "Jonny"
    }
  ]
}
```

| Feld | Bedeutung |
| --- | --- |
| `date` | ISO 8601 wie im Feed. Beim Startbudget der `START_DATE`-Zeitpunkt. |
| `type` | `start`, `buy` oder `sell`. |
| `amount` | Vorzeichenbehaftet: Kauf negativ, Verkauf positiv, Startbudget positiv. |
| `balance` | Laufender Saldo **nach** diesem Event. |
| `playerName` | Nachname aus `data.pn`. `null` beim Startbudget. |
| `playerImage` | `https://kickbase.b-cdn.net/` + `data.pim`. `null` wenn der Feed kein Bild liefert. |
| `teamId` | `data.tid`, für das lokale Vereinslogo. `null` beim Startbudget. |
| `tradePartner` | Gegenpart des Transfers, oder `null` wenn keiner aufgelöst werden kann. |

`events[-1].balance` ist per Konstruktion identisch mit dem `balance`-Feld des Users.
Das ist der Invariant, an dem Tabelle und Liste zusammenhängen.

Kein Vorname: der Feed liefert nur den Nachnamen, alles weitere käme aus
`player_statistics` — ein API-Call pro Transfer, den `balances()` heute nicht macht und
der die Funktion spürbar verlangsamen würde.

## Backend

### `build_balance_events()` in `backend/miscellaneous.py`

Die Rechenlogik wird als reine Funktion herausgezogen, neben `filter_transfers_from()`
und `calculate_revenue_data_daily()`. Ohne Netzwerk, damit sie testbar ist.

```python
def build_balance_events(transfers, user_name, initial_balance, start_datetime) -> list:
```

- `transfers`: Feed-Einträge mit `t == 15`, in beliebiger Reihenfolge.
- `user_name`: Anzeigename des Managers; `slr`/`byr` im Feed sind Namen, keine IDs.
- Rückgabe: chronologisch aufsteigende Event-Liste, beginnend mit dem `start`-Event.

Ablauf:

1. Events vor `start_datetime` über `filter_transfers_from()` verwerfen.
2. Nach `dt` aufsteigend sortieren. Der Feed kommt neueste-zuerst; für die Summe ist das
   egal, für einen laufenden Saldo nicht.
3. `start`-Event mit `initial_balance` voranstellen.
4. Je Transfer prüfen, ob der Manager Käufer (`byr`) oder Verkäufer (`slr`) ist, sonst
   überspringen. Saldo fortschreiben, Event anhängen.
5. Handelspartner: bei einem Kauf der `slr`, bei einem Verkauf der `byr`. Fehlt die
   Gegenseite im Event, bleibt das Feld `null`.

### `balances()` in `main.py`

Die bestehende Schleife über `all_transfers` (`main.py:865-883`) weicht dem Aufruf von
`build_balance_events()`. Der Kontostand ist dann `events[-1]["balance"]`; `teamValue`,
`maxBid` und der Rest bleiben unverändert.

**Änderung an der Berechnung:** `balances()` filtert Events vor `START_DATE` bislang
nicht, im Gegensatz zu `turnovers()` (`main.py:516-522`). Das wird angeglichen. Enthält
der Feed Events von vor dem Saisonstart oder einem Liga-Reset, ändern sich dadurch die
angezeigten Kontostände — sie werden dann richtig.

## Frontend

### `frontend/src/components/BalanceEventsDialog.js` (neu)

Eigene Komponente, damit `Balances.js` die Tabelle bleibt und nicht zusätzlich die
Detailansicht wird. Props: der Manager-Datensatz (oder `null`) und `onClose`.

MUI `Dialog`, `maxWidth="md" fullWidth`, Titel „Kontostand-Verlauf: <Manager>",
Schließen über Button, Escape und Backdrop. Inhalt ist ein `PagedDataGrid` — dieselbe
Pagination wie in allen anderen Tabellen.

| Spalte | Inhalt |
| --- | --- |
| Verein | `/images/<teamId>.png`, `onError`-Fallback auf `default.png` wie in `TurnoversTable.js:20-23` |
| Spieler | Foto (`playerImage`) plus `playerName` |
| Datum | `toLocaleString("de-DE")` |
| Event | „Startbudget", „Kauf", „Verkauf" |
| Handelspartner | `tradePartner`, bei `null` der Text „Kickbase" |
| Betrag | `currencyFormatter`, eingefärbt über `deltaCellClassName` / `deltaColumnStyles` |
| Saldo | `currencyFormatter` |

- Fehlen `teamId` oder `playerImage` (Startbudget), gibt `renderCell` `null` zurück statt
  ein Bild anzufordern, das es nicht gibt.
- „Kickbase" steht in jeder Zeile ohne aufgelösten Gegenpart — auch beim Startbudget.
- Standardsortierung ist Datum aufsteigend, damit die Saldo-Spalte von oben nach unten
  lesbar ist. Umsortieren bleibt erlaubt: `balance` ist pro Zeile vorberechnet und bleibt
  korrekt an seiner Zeile.

### `frontend/src/components/Balances.js`

- `useState` für den ausgewählten Manager.
- `onRowClick={(params) => setSelectedManager(params.row)}`.
- `sx={{ "& .MuiDataGrid-row": { cursor: "pointer" } }}`, damit die Zeile als klickbar
  erkennbar ist.
- `events` wandert in das Zeilenobjekt.
- `<BalanceEventsDialog manager={selectedManager} onClose={() => setSelectedManager(null)} />`.

## Tests

`tests/test_balance_events.py`, im Stil der bestehenden Skripte: dependency-frei,
Aufruf über `./venv/bin/python tests/test_balance_events.py`. Fixtures sind echte
Feed-Einträge aus `frontend/src/data/all_transfers.json`.

1. Ein Manager ohne Transfers bekommt genau ein Event: `start` mit `START_MONEY`.
2. `events[-1]["balance"]` entspricht dem Saldo, den die alte Schleife errechnet hätte.
3. Unsortierter Feed ergibt eine chronologisch aufsteigende Liste.
4. Kauf ergibt ein negatives, Verkauf ein positives `amount`.
5. Events vor `START_DATE` erscheinen weder in der Liste noch im Saldo.
6. Handelspartner wird bei Manager↔Manager-Transfers aufgelöst und ist `null`, wenn im
   Event nur eine Seite steht.

## Nicht im Umfang

- **Login-Boni und Erfolgs-Prämien** fehlen weiterhin in der Berechnung; der Docstring von
  `balances()` sagt das bereits. Der angezeigte Kontostand kann deshalb vom echten
  abweichen. Die Event-Liste macht diese Lücke sichtbarer, verursacht sie aber nicht.
- Keine Änderung an `turnovers()`, `revenue_sum.json` oder den anderen Tabellen.
