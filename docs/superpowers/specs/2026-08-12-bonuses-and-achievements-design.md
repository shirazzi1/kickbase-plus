# Login-Boni und Erfolge im Kontostand

Ein Schalter über der Balances-Tabelle rechnet tägliche Login-Boni und Erfolgsprämien in
Kontostand und Max. Gebot ein. Der Detail-Dialog zeigt sie als eigene Zeilen, erkennbar
als Schätzung.

`balances()` rechnet heute nur Transfers, und der Docstring sagt das auch. Gegen den
echten Kontostand in der App fehlten bei shirazzi genau 1.000.000 €: 650.000 € Login-Boni
und 350.000 € Erfolge.

## Der Login-Bonus

Prämie am Tag `n`: `min(100.000, (n - 1) * 10.000)`. Tag 1 bringt nichts und erzeugt kein
Event. Ab Tag 11 bleibt es bei 100.000 € pro Tag.

Belegt, nicht angenommen: die echten Feed-Events (Typ 22) für shirazzi liefern exakt
diese Reihe.

| Tag | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| € | 10k | 20k | 30k | 40k | 50k | 60k | 70k | 80k | 90k | 100k | 100k |

**Die Tageszählung geht über Kalendertage in der App-Zeitzone**, nicht über verstrichene
Stunden. Die Feed-Zeitstempel belegen es: Tag 11 am 11.08. um 01:13 UTC, Tag 12 am 11.08.
um 22:03 UTC — 21 Stunden auseinander, aber in `Europe/Berlin` zwei verschiedene Tage.
Der Tag des Saisonstarts ist Tag 1. Die Zeitzone kommt aus `TZ`, Default `Europe/Berlin`,
wie im Rest des Projekts.

Über eine Zeitdifferenz gerechnet läge man heute bei Tag 11 statt 12 — ein Fehler, der
über die Saison mitwächst.

**Die Annahme:** jeder Manager loggt täglich ein. Für andere Manager ist das nicht
prüfbar, Typ-22-Events sind privat und erscheinen im Feed nur für den angemeldeten Nutzer.
Die Formel behandelt damit alle gleich, was für einen Vergleich untereinander besser
taugt als echte Werte für einen und Schätzungen für zwölf.

## Die Erfolge

Prämien laut `help.kickbase.com`, ergänzt um die dort nicht dokumentierten Erfolge aus
dem Feed. Der Katalog liegt als eine Datenstruktur an einer Stelle im Code.

| ID | Erfolg | Bedingung | Prämie | Quelle |
| --- | --- | --- | --- | --- |
| 500 | First deal | 1 Transfer | 100.000 | `trades` |
| 501 | Transfer King bronze | 50 Transfers | 250.000 | `trades` |
| 400 | Team value bronze | 125 Mio Teamwert | 100.000 | `teamValue` |
| 600 | Kreisliga | Liga hat 3 Manager | 1.000.000 | Mitgliederzahl |
| 601 | Regionalliga | Liga hat 6 Manager | 1.000.000 | Mitgliederzahl |
| 602 | 2. Liga | Liga hat 12 Manager | 1.000.000 | Mitgliederzahl |
| — | Spieltagssieger | je Spieltagssieg | 1.000.000 | `mdWins`, wiederholbar |
| — | Spieltagspunkte Silber | 1000 Punkte an einem Spieltag | 250.000 | `user_performance()` |
| — | Spieltagspunkte Gold | 1500 Punkte | 500.000 | `user_performance()` |
| — | Jahrhundertspiel | 2000 Punkte | 1.000.000 | `user_performance()` |
| — | Bronzenes Händchen | 3 Mio Gewinn mit einem Spieler | 250.000 | `turnovers` |
| — | Silbernes Händchen | 5 Mio Gewinn | 500.000 | `turnovers` |
| — | Goldenes Händchen | 10 Mio Gewinn | 1.000.000 | `turnovers` |
| — | Königstransfer | 25 Mio Gewinn | 2.000.000 | `turnovers` |
| — | Meister | Saisonsieg | 2.000.000 | `placement` |
| — | Vizemeister | Platz 2 | 1.000.000 | `placement` |

Zwei Regeln, beide vom Nutzer bestätigt:

- **Stufen stapeln.** 6 Mio Gewinn mit einem Spieler bringt Bronze *und* Silber, also
  750.000 €.
- **Die Händchen-Familie zählt nur bei Verkäufen an den Markt.** Ein Verkauf an einen
  anderen Manager löst sie nicht aus. In der Liga waren zum Zeitpunkt des Entwurfs alle
  vier Verkäufe über 3 Mio Gewinn Managertausche — die Regel verhindert also konkret
  1.500.000 € an falscher Gutschrift.
- **„Transfer King" zählt dagegen alles**, auch Managertausche. Das Dashboard-Feld
  `trades` ist damit die richtige Quelle.

### Der START_DATE-Schnitt gilt auch für Erfolge

Erfolge von vor dem Saisonstart oder Liga-Reset zählen nicht, genau wie Transfers.

Hergeleitet, nicht geraten: gegen den echten Kontostand fehlten bei shirazzi 350.000 € an
Erfolgen, nicht die 3.450.000 € des vollen Katalogs. Dafür gab es zwei Erklärungen, die
beide exakt 350.000 € ergeben, weil „First deal" und „Team value bronze" beide 100.000 €
wert sind:

- **A** — Liga-Größen-Erfolge und „Team value bronze" zahlen kein Geld.
- **B** — alles vor dem Reset zählt nicht. Die drei Liga-Erfolge und „First deal" wurden
  am 01.08. vor 18:00 Uhr vergeben, die Beitritts-Events liegen zwischen 17:46 und 17:56.

Die Entscheidung fällt auf **B**, weil die App für die Liga-Erfolge ausdrücklich
1.000.000 € ausweist: A müsste unterstellen, dass eine angezeigte Prämie nie gezahlt wird.
B erklärt dasselbe Ergebnis damit, dass sie gezahlt wurde und der Reset sie mit allem
anderen auf 50 Mio zurückgesetzt hat. Und B ist dieselbe Regel, die Transfers und
Transfererlöse schon befolgen.

Ein Gegentest an Twilli und Reddy (die A und B um 100.000 € unterschiedlich treffen)
bestätigte die Größenordnung, konnte die beiden Erklärungen aber nicht trennen.

**Das Restrisiko steht im Code.** In einer frisch erstellten Liga ohne Reset fallen die
Liga-Größen-Erfolge hinter `START_DATE` und würden 3 Mio pro Manager gutschreiben. Wäre A
doch richtig, wäre das dort ein großer Fehler. Die Entscheidung ist deshalb eine benannte
Konstante mit dieser Begründung als Kommentar, umstellbar in einer Zeile.

### Nicht enthalten

- **Topscorer, Matchwinner, Weltklasse, Fussballgott** — brauchen die Punkte eines
  einzelnen Spielers pro Spieltag. `maxPoints` kommt aus einem Battle-Leaderboard und ist
  saisonweit, taugt dafür nicht.
- **MVP** — braucht einen ligaweiten Vergleich pro Spieler und Spieltag.
- **Tormaschine** — Tordaten fehlen im Projekt vollständig.
- **Transfer King und Team value in Silber und Gold** — Schwellen und Beträge unbekannt.
  Der Katalog ist so gebaut, dass ein Nachtrag reine Zahlenpflege ist.

## Erreichte Erfolge werden mitgeschrieben

Neue Datei `achievements.json`, pro Manager eine Liste aus `{id, earnedAt, amount}`, die
nur wächst — dasselbe Prinzip wie `all_transfers.json`.

Sie löst zwei Probleme auf einmal:

1. **Ein erreichter Erfolg bleibt erreicht.** Die Herleitung schaut auf den aktuellen
   Stand. Bei `trades` ist das unkritisch, die Zahl wächst nur. Beim Teamwert nicht: fällt
   er unter 125 Mio, verschwände der Erfolg sonst wieder. `team_values.json` hilft dabei
   nicht, es hält nur einen Wert pro Spieltag.
2. **Der Zeitpunkt bleibt stabil.** Ohne Datum hat die Saldo-Spalte keine Reihenfolge.

Wo sich das Datum aus dem Feed herleiten lässt, wird es das — „First deal" ist der erste
Transfer, „Transfer King bronze" der fünfzigste, Spieltagserfolge der Spieltag. Für den
Rest gilt der Zeitpunkt der ersten Beobachtung. Dass das nötig ist, zeigt „Team value
bronze": im Feed am 06.08. vergeben, aus den vorhandenen Daten nicht datierbar.

## Datenmodell

Jeder Eintrag in `balances.json` bekommt zusätzlich:

```json
{
  "balance": -19346628,
  "maxBid": 30565889,
  "events": [ ... ],

  "balanceWithBonuses": -18346628,
  "maxBidWithBonuses": 31895889,
  "eventsWithBonuses": [ ... ]
}
```

`eventsWithBonuses` ist die chronologische Zusammenführung aus Transfers, Bonus- und
Erfolgs-Events mit neu durchgerechnetem Saldo. Die neuen Event-Typen sind `login_bonus`
und `achievement`; letzterer trägt zusätzlich `achievementName`.

Beide Listen kommen fertig aus dem Backend. Das Frontend schaltet nur um und rechnet
nichts selbst, damit die beiden Ansichten nicht auseinanderlaufen können. Die doppelte
Liste kostet bei dreizehn Managern und ein paar hundert Events nichts, was ins Gewicht
fällt.

## Frontend

Ein Switch „Boni & Erfolge einrechnen" über der Balances-Tabelle steuert Tabelle und
Dialog gemeinsam — eine Einstellung, damit die Tabelle nicht eine andere Annahme zeigen
kann als die Detailansicht dahinter.

- **Tabelle:** „Kontostand" und „Max. Gebot" zeigen die jeweiligen Werte.
- **Dialog:** rendert die passende Event-Liste. Bonus- und Erfolgszeilen sind als
  Schätzung gekennzeichnet und dadurch von belegten Transfers unterscheidbar.
- Der Hilfe-Text an der Balances-Überschrift nennt die Annahme des täglichen Logins.

**Bekannte Enge:** Login-Boni als Tageszeilen sind heute 11 Zeilen, am Saisonende rund
280. Die würden die Transfers im Dialog zuschütten. Es bleibt zunächst bei Tageszeilen;
das Zusammenfassen pro Monat wäre eine Änderung an einer Stelle.

## Was diese Rechnung nicht leistet

Der Kontostand bleibt eine Schätzung. Der tägliche Login ist unterstellt, die nicht
herleitbaren Erfolge fehlen, und zwei Erfolgsfamilien haben unbekannte Stufen. Für
shirazzi stimmt sie nach diesem Entwurf auf den Euro — das ist ein Beleg für die Formel,
kein Beweis für die anderen zwölf.
