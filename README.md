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

## Official UFC event cache

The launcher refreshes the official `https://www.ufc.com/events` listing and linked event pages every six hours. It caches up to 12 events, from the past 14 days through the next 120 days. The listing is limited to the events UFC includes on that page; this is not a complete historical archive.

```powershell
python app.py --refresh-events
python app.py --events-status
```

`--refresh-events` forces a refresh; scheduled checks respect a persisted six-hour interval, including after failures. Event pages are fetched with at most three concurrent requests, time and size limits, and no automatic retries. HTTPS certificate and hostname checks remain enabled. The `truststore` SSL context uses the operating system trust store and can obtain missing intermediates (see https://truststore.readthedocs.io/en/latest/). No browser automation, cookies, or access-control bypass is required for the locally verified source.

The cached records contain event URLs/IDs, titles, main-card start times in UTC, bout pairings, weight classes, fighter names, and official athlete profile IDs/URLs. These IDs anchor event candidates. The homepage uses AI-confirmed event assignments to place related topic groups under UFC event hubs.

`GET /api/events` returns the public cached records, last attempt/success timestamps, per-event last-success times, and staleness flags. It never fetches UFC pages during a web request. On a failed listing or card parse, the last successful data remains in SQLite and the status reports the failure. A listed event can have no usable card yet; it returns an empty bout list with a stale flag, rather than invented fighters. Unexpected markup or incomplete fighter pairings are rejected. Each changed card is saved in `ufc_card_history`, preserving prior pairings when fighters are replaced; unchanged cards do not add duplicate snapshots. Removed events are retained in cache and become stale when no longer refreshed. Public listings can still change or block automated access in future; check the cached status rather than assuming freshness.

## Event hubs and topic coverage

MMA coverage is prioritized as numbered UFC events, Fight Nights, other multi-article story groups, then individual articles. Priority is applied across the latest 400 articles before the 50-topic display limit, so newer singles cannot crowd event coverage off the page. Each event retains separate topic cards (e.g. announcement, preview, result); topic coverage links and funny group names are preserved. Boxing continues to use the existing topic feed. Only upcoming/current event hubs receive priority (numbered events, then Fight Nights). Past hubs are mixed with individual articles after other topic groups, sorted together by their newest article publication time rather than the event date. An event becomes past six hours after its scheduled main-card start (a grace window because the source does not provide reliable completion times). Event hubs start collapsed and past cards carry a Past event label. Within each priority tier, coverage is ordered by its newest article, with stories and articles newest first. If higher-priority coverage fills the 50-topic limit, lower-priority singles are omitted.

Fighter names generate candidates within 60 days before an event through seven days afterward. Explicit references to a verified numbered event can match earlier than 60 days, so advance announcements and withdrawal coverage are not excluded merely because the card is months away. AI distinguishes specific event coverage from current news/interviews/analysis about roster fighters. Current fighter coverage may join an upcoming card even without naming the event, and is labeled Fighter coverage. Archives, explicitly different matchups/events, personal disputes, and ambiguous identities remain outside. Fighter-only coverage is not attached to past cards. Candidate selection alone does not assign an event. Current rosters generate fighter candidates; old full-fight videos are excluded unless an explicit event reference warrants review. Unknown events, incomplete responses, and confidence below 0.85 do not produce links. Uncertain/unreviewed topics remain visible in the regular feed.

Event decisions are saved in `event_story_links` and cached against a fingerprint of the topic headlines and candidate cards. New coverage or changed fight cards invalidates the decision until rechecked. Matching runs after headline grouping and event refreshes, at most 40 changed candidate topics in one additional API request per run. It sends only public headlines/dates and cached event names/fighter pairings. There are no AI calls during page loads. A missing key or failed call leaves the regular topic feed usable.

```powershell
python app.py --match-events
```

Use that command to review the current candidate backlog without fetching feeds or regrouping topics. `/api/events` remains available for checking cached card freshness. Event hubs link back to the official card and show a notice when card data is stale.
