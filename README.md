# Hotdog Fighting

A Flask news feed for MMA and boxing. Every article appears individually, newest publication date first. Event badges link relevant MMA articles to official UFC cards. There are no topic groups, event hubs, generated group names, or event-based ranking. The mobile MMA/Boxing selector is preserved.

## Run locally

Use Python 3.11 or newer:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

Open http://localhost:8000. On macOS/Linux use `.venv/bin/python`.

The single-process launcher uses Waitress, refreshes RSS feeds every 90 minutes, and refreshes UFC cards every six hours. Both jobs start in the background so pages can load immediately. Each category displays its latest 50 articles with publisher, publication time in UTC, and any relevant event tags. Tags never change article ordering. Analytics tracks article clicks and supports browser exclusion via `?analytics=off`; `?analytics=on` reverses it.

## Configuration

- `DB_FILE`: SQLite path; defaults to `news.db` beside `app.py`. Use an absolute path on persistent production storage.
- `PORT`: HTTP port, default 8000.
- `REFRESH_MINUTES`: RSS interval, default 90 minutes.
- `OPENAI_API_KEY`: optional, enables AI event tagging.
- `OPENAI_GROUPING_MODEL`: tagging model, default `gpt-4.1-mini` (historical variable name retained).

Set values in your shell or hosting environment. For local API configuration, the ignored `.env.ai` supports simple `OPENAI_API_KEY=...` and `OPENAI_GROUPING_MODEL=...` lines. Environment values take priority. The legacy `.env` is never loaded. Do not reuse its historically exposed key; revoke/rotate it with the provider if still valid. Removing a tracked secret does not revoke it or erase Git history.

The Procfile launches `python app.py`. Use one application process with persistent SQLite storage. `create_app()` initializes storage but does not start background jobs when imported by another WSGI launcher. In that mode schedule the refresh commands externally. Logs go to standard output/error.

## Refresh and checks

```powershell
python app.py --init-db
python app.py --refresh
python app.py --refresh-events
python app.py --match-events
python app.py --events-status
python -m unittest discover -s tests -v
node --test tests/analytics.test.cjs
```

RSS ingestion has transient retries, download time/size limits, per-entry validation, and up to four concurrent feeds. Failures retain previous articles. Links are unique and dates are normalized to UTC.

## Event tags

The official `https://www.ufc.com/events` listing supplies up to 12 cached cards from the past 14 days through the next 120 days. Cached data includes start times, bout pairings, athlete profile IDs and names. The reader uses `truststore` with certificate and hostname checks enabled. Card changes retain history in `ufc_card_history`. Failed requests/parses preserve last-good snapshots and record staleness. `GET /api/events` exposes public cached data without triggering a fetch.

Explicit references to verified numbered events can display badges immediately. AI reviews up to 40 changed candidate articles per run using public headline/date and cached card evidence. Roster mentions use a 60-day pre-event window; explicit numbered-event references can match farther ahead. Coverage through seven days after an event remains eligible. Archived full-fight videos and ambiguous identities are excluded from roster matching. Current fighter news may be tagged to an upcoming card, with a tooltip distinguishing fighter coverage. The six-hour main-card grace window avoids treating an in-progress event as past.

Decisions are cached per article in `event_story_links`, validated against known events and confidence >= 0.85, and invalidated when article/card inputs change. OpenAI requests use structured output, `store=false`, and no automatic retries. Usage is billed to the configured OpenAI project. Without a key or when the API fails, articles remain visible and explicit numbered-event badges still work. Page loads never call AI. Automatic story grouping and group naming are disabled; historical assignments/titles remain in SQLite but do not affect display or tagging.

## Database maintenance

The first initialization of a legacy database writes a timestamped `news.db.pre-migration-*.bak`, then deduplicates in one transaction, keeping the most recently scraped row per trimmed link (highest ID breaks ties) and enforcing unique links. Empty links are omitted. Naive legacy dates are interpreted as UTC; invalid publication dates fall back to scraped time. Invalid legacy scraped dates use the Unix epoch. The unused legacy headline column remains in the backup.

Stop old application processes before migration/restoration. To restore, copy a backup over the database; the next launch migrates again. Keep regular off-disk backups. Databases, migration backups, API keys, logs, virtual environments and IDE files are ignored by Git. Historical grouping tables are preserved to avoid deleting stored data.
