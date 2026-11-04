# Hotdog Fighting

A Flask site collecting MMA and boxing headlines from RSS feeds. Headlines are stored in SQLite and refreshed every 90 minutes. The homepage shows the newest 50 per category.

## Local setup

Use Python 3.11 or newer. On Windows:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

Open http://localhost:8000. On macOS/Linux use `.venv/bin/python` instead.

The launcher uses Waitress, initializes the database, and schedules an immediate background refresh. Existing headlines remain available during fetching. Each feed logs its outcome; failures preserve previously stored articles. Only HTTP(S) article links with titles are accepted. Downloads have time and size limits and transient failures receive up to three attempts.

## Configuration and deployment

Set environment variables in your shell or hosting provider. `.env.example` documents them; `.env` files are **not** automatically loaded.

- `DB_FILE`: SQLite file, defaulting to `news.db` beside `app.py`. Use an absolute path on persistent storage in production.
- `PORT`: HTTP port, default 8000.
- `REFRESH_MINUTES`: refresh interval, default 90.

Run `python app.py` (the Procfile uses this command). Use one application process/instance with persistent SQLite storage. The embedded scheduler is per-process; multiple replicas would duplicate refresh jobs. For scaling, separate scheduling from serving and choose shared storage. `create_app()` supports WSGI imports and initializes storage, but deliberately does not start a scheduler. In that mode run `python app.py --refresh` from an external scheduler.

Logs go to standard output/error for the hosting platform to collect. No credentials are needed for the configured RSS feeds. Analytics and AdSense identifiers remain in the template.

## Article analytics

`static/analytics.js` sends one `article_click` event per article activation, with `article_title` (first 100 characters), `article_category` (MMA or Boxing), `link_url`, and `link_domain`. Standard enhanced-measurement `click` events can also be collected; use `article_click` alone for article-click totals rather than adding both event names together. GA4 has event-scoped custom dimensions named **Article title** (`article_title`) and **Article category** (`article_category`). New custom-dimension data can take 24â€“48 hours to appear. Historical headlines cannot be reconstructed from these new fields.

The saved GA4 exploration is **Article clicks**: https://analytics.google.com/analytics/web/#/analysis/a339752235p471274630/edit/O87i0S4HQVyMjnYuFsH-SA . It lists Article title and Article category as rows, Event count as the value, and filters Event name to exactly `article_click`.

To exclude your visits, open `https://hotdogfighting.com/?analytics=off` once in every browser/profile you use on every device. A visible status message confirms exclusion. The preference is stored in local storage with a one-year first-party cookie fallback and prevents Analytics from loading, including on the initial exclusion visit. Clearing site storage, changing browser/profile, or using a new private session requires setting it again. Open `?analytics=on` to re-enable collection. This affects future collection only and does not change AdSense behavior or erase historical visits.

Run `node --test tests/analytics.test.cjs` for tracking and exclusion checks. On November 3, 2026, production was still running the older inline-template entry point; the analytics-only production commit embeds the same script in `HotDogMMA.com.py`. The pending local app cleanup uses this separate static script. Preserve these analytics changes when deploying that cleanup.

Live checks during this cleanup found moved feeds for Bloody Elbow, MMA Fighting, and BoxingScene; their URLs have been updated and verified. Eight of ten sources returned parseable feeds. UFC failed certificate verification on the review machine and MMA Junkie returned HTTP 402. Those sources remain configured for future retries; TLS verification stays enabled and access restrictions are not bypassed. Their failures are logged and do not prevent other sources from refreshing.

## Database migration and maintenance

```powershell
python app.py --init-db
python app.py --refresh
python -m unittest discover -s tests -v
```

Stop older application processes before migrating. The first initialization of a legacy database writes a timestamped `news.db.pre-migration-*.bak` backup next to it, then migrates in one transaction. It retains the most recently scraped row for each trimmed link (highest ID breaks ties), normalizes dates to UTC, and adds a unique-link constraint. Empty links are omitted. The unused legacy `headline` column remains available in the backup. Naive legacy dates are interpreted as UTC because their original timezone was not recorded; invalid publication dates fall back to scraped time. Invalid scraped dates use the Unix epoch during migration.

Migration is versioned and repeatable. To restore, stop the app and copy the backup over the database; the next launch will migrate it again. Keep regular database backups outside the application disk as well.

Databases, migration backups, logs, virtual environments, IDE settings, and secrets are ignored by Git. Existing local copies are preserved when removed from tracking. The old `venv/` contains unrelated chatbot experiments and should not be used to run this app.

## Credential follow-up

An `.env` containing an API-key setting was previously committed. It is now untracked, but still exists in historical commits and on GitHub until these changes are pushed. If the credential is real, revoke/rotate it with its provider. Removing the current file does not revoke a key or erase Git history. History rewriting requires separate coordination with anyone using the repository.

## Story coverage

The homepage groups similar headlines within each category, comparing the latest 400 articles and displaying up to 50 story cards. Expand a card to see every matching article with its publisher and UTC publication time. The newest article supplies the main headline. Without an API key, matching is computed locally from the stored headlines on each page load. With a key configured, saved AI assignments take priority.

Matches require strong word overlap, publication within 48 hours, matching numbers, and matching development types (announcements, cancellations, results, previews, reactions). Every member must match every other member to prevent broad topic chains. This deliberately leaves uncertain matches separate. Headline-only matching can still miss related coverage or occasionally combine distinct stories. Groups are computed views, so existing articles are preserved and new refreshes participate automatically.

## AI headline grouping

Put `OPENAI_API_KEY=your-key` in the ignored `.env.ai` beside `app.py`, or set `OPENAI_API_KEY` in your hosting environment. Do not reuse the historically exposed legacy key. `.env.ai` supports simple NAME=value lines, optionally quoted; environment values take priority. The normal `.env` is never loaded. Default model: `gpt-4.1-mini`; override with `OPENAI_GROUPING_MODEL`.

After every feed refresh, the server sends up to 60 previously unreviewed headlines per category, plus recent story context, to the OpenAI Responses API. It sends public headlines (limited to 1,000 characters), numeric IDs, publisher domains, and dates. No full articles are scraped. Requests use structured JSON output with `store=false`. API usage is billed to your OpenAI project. There are at most two requests per refresh and no automatic retries. Configure project spending controls in your OpenAI account.

Assignments are validated for complete coverage, known IDs, confidence >= 0.85, and a 48-hour time span before saving to SQLite. Lower-confidence articles stay separate. Already reviewed headlines are not sent for reassignment; stored assignments survive restarts. New articles can join existing groups. Pending articles use local matching until reviewed. Failures keep previous assignments and defer pending work to the next refresh. This is automated judgment, and incorrect or missed matches remain possible.

To process pending headlines immediately without fetching feeds:

```powershell
python app.py --group-stories
```

One run processes up to 120 pending headlines from the latest 400 per category; subsequent refreshes gradually process the backlog. The website never makes AI calls on page loads. Without a key, it continues using the local matcher.

API implementation reference: https://developers.openai.com/api/docs/guides/structured-outputs

AI coverage groups also receive cached, funny, loud titles. The title is plain text; all publisher links sit inside expandable coverage. Headlines are the only naming source; allegations and rumors must retain attribution. Naming processes at most 50 unnamed groups per refresh in one additional API request. Run `python app.py --name-stories` to backfill names without fetching feeds or regrouping articles. Failed naming falls back to the original lead headline.
