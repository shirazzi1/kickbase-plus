# Arbeiten in diesem Repo

Mehrere Agents arbeiten hier gleichzeitig und teilen sich denselben Haupt-Checkout.
Daraus folgen zwei Regeln, die keine Stilfragen sind — beide wurden schon verletzt, und
beide Male landete fremde Arbeit im falschen Commit.

## Eigener Worktree, eigener Branch von `main`

Vor der ersten Dateiänderung einen eigenen Worktree anlegen, auf einem eigenen Branch von
`main`. Nie im Haupt-Checkout arbeiten, nie auf einem fremden Branch aufsetzen.

Der Worktree schützt davor, dass ein anderer Agent den Branch unter dir wechselt. Das
Abzweigen von `main` hält jede Arbeit für sich reviewbar und verhindert, dass ein Feature
ein unbeteiligtes in seinen PR zieht.

## Nie blind stagen

Nie mit `git add -A` oder `git commit -a` einchecken. Pfade explizit nennen und vor dem
Commit `git status` prüfen.

Der explizite Pfad allein reicht nicht: `git commit` nimmt alles mit, was bereits im Index
liegt — auch das, was ein anderer Agent dort vorgemerkt hat. Der Blick auf `git status`
ist der Teil, der das abfängt.

# Konventionen

- Nutzertexte im Frontend sind deutsch.
- Python-Kommentare beginnen mit `###`, Docstrings mit `"""### Zusammenfassung`.
- JavaScript in `frontend/src`: vier Leerzeichen Einrückung, doppelte Anführungszeichen,
  keine Semikolons.
- Tests sind eigenständige Skripte unter `tests/`, ohne Test-Framework, ausgeführt mit
  `./venv/bin/python tests/<name>.py`. Als Vorlage dient `tests/test_start_date.py`.
- Die Datensätze liegen unter `data/public` (was das Frontend über `/api/data/<name>` liest)
  und `data/state` (was nur das Backend liest). Beides wird zur Laufzeit erzeugt und ist
  nicht eingecheckt. Ein frischer Worktree hat die Dateien nicht — `npm run build` und
  `npm start` funktionieren trotzdem, weil das Frontend nichts mehr zur Buildzeit importiert.
  Wer echte Daten im UI sehen will, kopiert `data/` aus dem Haupt-Checkout.
- Welcher Datensatz wohin gehört, steht in `backend/datasets.py`. Ein neuer Datensatz muss
  dort in genau eine der beiden Listen — und, wenn ihn das Frontend liest, zusätzlich in
  `frontend/src/hooks/dataContracts.js`. `tests/test_data_plane.py` hält beide aneinander fest.
