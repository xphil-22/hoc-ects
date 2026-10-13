# hoc-ects

Filtert die Kurse des House of Competence (HoC) am KIT nach ECTS.

Auf [HoCampus-Next](https://next.hoc.kit.edu) gibt es keinen ECTS-Filter: Die Punkte stehen nur im
Beschreibungstext jedes Kurses („Arbeitsaufwand für ECTS: 1 ECTS: … 2 ECTS: …“). Dieser kleine Server
lädt alle Kursseiten eines Semesters, liest die Angaben aus und zeigt eine filterbare Liste. Jeder
Eintrag verlinkt auf die Originalseite, wo man sich wie gewohnt anmeldet.

> Inoffizielles Projekt, nicht vom KIT oder HoC betrieben. Maßgeblich sind immer die Angaben auf der
> Kursseite. Kurse, deren Beschreibung keine ECTS nennt, erscheinen als **unbekannt**.

## Starten

Benötigt nur Python 3.9+ (keine Abhängigkeiten):

```sh
python3 server.py
# -> http://localhost:8765
```

Mit Docker:

```sh
docker build -t hoc-ects .
docker run -d -p 8765:8765 -v hoc-ects-data:/data hoc-ects
```

## Optionen

| Flag | Umgebungsvariable | Standard | Bedeutung |
|---|---|---|---|
| `--host` | `HOST` | `127.0.0.1` | `0.0.0.0`, um im Netzwerk erreichbar zu sein |
| `--port` | `PORT` | `8765` | Port |
| `--term` | `HOC_TERM` | aktuelles Semester | Semester-ID aus der HoC-URL (`/de/events/<id>`) |
| | `CACHE_FILE` | `./cache.json` | Ort des Caches |

Ein anderes Semester lässt sich auch per URL wählen: `http://localhost:8765/?term=<id>`.

## Funktionsweise

- Beim ersten Aufruf werden alle Kursseiten des Semesters geladen (ca. 120 Seiten, ~30 s), danach
  6 Stunden aus dem Cache bedient.
- „↻ neu laden“ holt die Daten frisch, höchstens einmal alle 10 Minuten pro Semester, damit der
  HoC-Server nicht unnötig belastet wird.
- Filter: ECTS-Werte (mehrere wählbar), „nur genau diese ECTS“ (Kurs bietet ausschließlich die
  gewählten Werte an), Typ, Sprache, Freitextsuche über Titel und Dozierende. Die Auswahl wird im
  Browser gespeichert.

API: `GET /api/events[?term=<id>][&refresh=1]` liefert die Kurse als JSON.

## Lizenz

MIT
