"""SQLite storage and bounded, fault-tolerant RSS ingestion."""
import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import feedparser
from dateutil import parser

log = logging.getLogger(__name__)
SCHEMA_VERSION = 1
MAX_FEED_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT = 10
FETCH_ATTEMPTS = 3
NEWS_SOURCES = {
    'MMA': [
        'https://bloodyelbow.com/feed/',
        'https://www.lowkickmma.com/feed/',
        'https://www.mmamania.com/rss/index.xml',
        'https://www.ufc.com/rss/news',
        'https://www.mmafighting.com/rss/current.xml',
        'https://www.mmaweekly.com/feed',
        'https://mmajunkie.usatoday.com/feed',
    ],
    'Boxing': [
        'https://www.badlefthook.com/rss/index.xml',
        'https://www.boxingscene.com/feed',
        'https://www.worldboxingnews.net/feed',
    ],
}


def utc_date(value, fallback=None):
    """Normalize legacy RFC dates and ISO dates; naive historical dates mean UTC."""
    try:
        result = parser.parse(str(value))
    except (ValueError, TypeError, OverflowError):
        result = fallback or datetime.now(timezone.utc)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc).isoformat(timespec='seconds')


def init_db(db_file):
    """Back up and migrate legacy rows once, transactionally and repeatably."""
    path = Path(db_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as conn:
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError('Database schema is newer than this application')
        if version == SCHEMA_VERSION:
            return
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='articles'").fetchone()
        if exists:
            backup = path.with_name(path.name + '.pre-migration-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.bak')
            with closing(sqlite3.connect(backup)) as target:
                conn.backup(target)
            log.info('Database backup: %s', backup)
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            # Another process may have finished migration while this one waited.
            if conn.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION:
                return
            conn.execute('''CREATE TABLE articles_migrated (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL, title TEXT NOT NULL, link TEXT NOT NULL UNIQUE,
                published_date TEXT NOT NULL, scraped_date TEXT NOT NULL)''')
            rows = []
            if exists:
                rows = conn.execute('SELECT id, category, title, link, published_date, scraped_date FROM articles').fetchall()
            unique = {}
            for row in rows:
                row_id, category, title, link, published, scraped = row
                if not link or not str(link).strip():
                    continue
                scraped = utc_date(scraped, datetime(1970, 1, 1, tzinfo=timezone.utc))
                published = utc_date(published, datetime.fromisoformat(scraped))
                link = link.strip()
                record = (row_id, category or 'MMA', title or '(Untitled)', link, published, scraped)
                if link not in unique or (scraped, row_id) > (unique[link][5], unique[link][0]):
                    unique[link] = record
            conn.executemany('INSERT INTO articles_migrated VALUES (?, ?, ?, ?, ?, ?)', unique.values())
            if exists:
                conn.execute('DROP TABLE articles')
            conn.execute('ALTER TABLE articles_migrated RENAME TO articles')
            conn.execute('CREATE INDEX idx_category_date ON articles(category, published_date DESC)')
            conn.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
        log.info('Database ready: %d legacy rows -> %d unique articles', len(rows), len(unique))


def download_feed(url):
    """Limit network waits and body size; retry transient failures only."""
    for attempt in range(FETCH_ATTEMPTS):
        try:
            request = Request(url, headers={'User-Agent': 'HotdogFighting/1.0 (RSS reader)',
                                            'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml'})
            deadline = time.monotonic() + FETCH_TIMEOUT
            with urlopen(request, timeout=FETCH_TIMEOUT) as response:
                chunks, size = [], 0
                while True:
                    if time.monotonic() > deadline:
                        raise TimeoutError('Feed download exceeded deadline')
                    # read1 returns after one buffered/socket read, so a slow
                    # trickle cannot indefinitely postpone the deadline check.
                    chunk = response.read1(64 * 1024)
                    if not chunk:
                        return b''.join(chunks)
                    size += len(chunk)
                    if size > MAX_FEED_BYTES:
                        raise ValueError('Feed exceeds size limit')
                    chunks.append(chunk)
        except HTTPError as exc:
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == FETCH_ATTEMPTS - 1:
                raise
        except (URLError, TimeoutError, OSError):
            if attempt == FETCH_ATTEMPTS - 1:
                raise
        time.sleep(2 ** attempt)


def fetch_news_rss(feed_url, category):
    try:
        feed = feedparser.parse(download_feed(feed_url))
    except Exception:
        log.exception('Feed fetch failed: %s', feed_url)
        return []
    if feed.get('bozo'):
        log.warning('Feed parsing warning for %s: %s', feed_url, feed.get('bozo_exception'))
    articles, seen = [], set()
    for entry in feed.get('entries', []):
        try:
            link = entry.get('link', '')
            if isinstance(link, dict):
                link = link.get('href', '')
            link = link.strip()
            title = entry.get('title', '').strip()
            parsed = urlsplit(link)
            if not title or parsed.scheme not in ('https', 'http') or not parsed.netloc or link in seen:
                continue
            articles.append(dict(category=category, title=title, link=link,
                                 published_date=utc_date(entry.get('published') or entry.get('updated'))))
            seen.add(link)
        except (AttributeError, TypeError, ValueError):
            log.warning('Skipping malformed entry in %s', feed_url)
    if not articles:
        log.warning('Feed returned no usable articles: %s', feed_url)
    else:
        log.info('Feed %s: %d usable articles', feed_url, len(articles))
    return sorted(articles, key=lambda article: article['published_date'], reverse=True)


def save_articles_to_db(db_file, articles):
    with closing(sqlite3.connect(db_file, timeout=30)) as conn, conn:
        before = conn.total_changes
        conn.executemany('''INSERT INTO articles(category, title, link, published_date, scraped_date)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT(link) DO NOTHING''',
            [(a['category'], a['title'], a['link'], utc_date(a['published_date']), utc_date(None)) for a in articles])
        return conn.total_changes - before


def aggregate_news(db_file, sources=None):
    sources = NEWS_SOURCES if sources is None else sources
    inserted = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(fetch_news_rss, url, category): url for category, urls in sources.items() for url in urls}
        for job in as_completed(jobs):
            try:
                articles = job.result()
            except Exception:
                log.exception('Unexpected feed failure: %s', jobs[job])
                continue
            inserted += save_articles_to_db(db_file, articles)
    log.info('Refresh complete: %d new articles', inserted)
    from ai_grouping import update_ai_groups
    update_ai_groups(db_file)
    return inserted
