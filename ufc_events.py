"""Official UFC event/card ingestion with durable last-good snapshots."""
import hashlib
import json
import logging
import re
import sqlite3
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
import truststore

log = logging.getLogger(__name__)
EVENTS_URL = 'https://www.ufc.com/events'
REFRESH_HOURS = 6


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def official_url(value, kind):
    parsed = urlsplit(urljoin(EVENTS_URL, value))
    if (parsed.scheme != 'https' or parsed.netloc not in ('www.ufc.com', 'ufc.com') or
            not re.fullmatch('/' + kind + r'/[a-z0-9-]+/?', parsed.path)):
        raise ValueError('Unexpected UFC resource URL')
    return 'https://www.ufc.com' + parsed.path.rstrip('/')


def fetch_html(url):
    # System trust can obtain intermediates missing from the site's certificate chain.
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    request = Request(url, headers={'User-Agent': 'HotdogFighting/1.0 (public event reader)',
                                   'Accept': 'text/html'})
    deadline = time.monotonic() + 20
    with urlopen(request, timeout=15, context=context) as response:
        size, chunks = 0, []
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError('UFC download deadline exceeded')
            chunk = response.read1(64 * 1024)
            if not chunk:
                return b''.join(chunks).decode('utf-8')
            size += len(chunk)
            if size > 4 * 1024 * 1024:
                raise ValueError('UFC page exceeds size limit')
            chunks.append(chunk)


def parse_events(html):
    soup = BeautifulSoup(html, 'html.parser')
    events = {}
    cards = soup.select('.c-card-event--result')
    for card in cards:
        headline = card.select_one('.c-card-event--result__headline a')
        date = card.select_one('[data-main-card-timestamp]')
        if not headline or not date:
            raise ValueError('UFC listing structure changed')
        url = official_url(headline['href'], 'event')
        starts = datetime.fromtimestamp(int(date['data-main-card-timestamp']), timezone.utc)
        title = headline.get_text(' ', strip=True)
        if not title:
            raise ValueError('Missing UFC event name')
        events[url] = dict(url=url, slug=url.rsplit('/', 1)[-1], title=title, starts_at=starts.isoformat())
    if not events:
        raise ValueError('UFC listing contained no events')
    return list(events.values())


def parse_card(html):
    soup = BeautifulSoup(html, 'html.parser')
    bouts = []
    for bout in soup.select('.c-listing-fight'):
        fighters = []
        for corner in ('red', 'blue'):
            name = bout.select_one(f'.c-listing-fight__corner-name--{corner} a')
            if not name:
                raise ValueError('Incomplete UFC bout; retain last good card')
            url = official_url(name['href'], 'athlete')
            text = name.get_text(' ', strip=True)
            if not text:
                raise ValueError('Missing UFC fighter name')
            fighters.append(dict(id=url.rsplit('/', 1)[-1], name=text, url=url))
        weight = bout.select_one('.c-listing-fight__class-text')
        fight = dict(red=fighters[0], blue=fighters[1], weight_class=weight.get_text(' ', strip=True) if weight else '')
        if not any(b['red']['id'] == fight['red']['id'] and b['blue']['id'] == fight['blue']['id'] for b in bouts):
            bouts.append(fight)
    if not bouts:
        raise ValueError('No announced bouts found; retain last good card')
    return bouts


def init_events(db_file):
    with closing(sqlite3.connect(db_file, timeout=30)) as conn, conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS ufc_events (
            url TEXT PRIMARY KEY, slug TEXT NOT NULL, title TEXT NOT NULL, starts_at TEXT NOT NULL,
            listed_at TEXT NOT NULL, card_json TEXT, card_fetched_at TEXT, last_error TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS ufc_card_history (
            id INTEGER PRIMARY KEY, event_url TEXT NOT NULL, card_hash TEXT NOT NULL,
            card_json TEXT NOT NULL, fetched_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS ufc_source_status (
            id INTEGER PRIMARY KEY CHECK(id=1), attempted_at TEXT, succeeded_at TEXT, last_error TEXT)''')


def refresh_events(db_file, force=False):
    """At most one listing + twelve card requests per six hours; no request-time fetches."""
    init_events(db_file)
    stamp = now_iso()
    with closing(sqlite3.connect(db_file, timeout=30)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        status = conn.execute('SELECT attempted_at FROM ufc_source_status WHERE id=1').fetchone()
        if not force and status and datetime.fromisoformat(stamp) - datetime.fromisoformat(status[0]) < timedelta(hours=REFRESH_HOURS):
            return {'refreshed': 0, 'failed': 0, 'skipped': True}
        conn.execute('''INSERT INTO ufc_source_status(id, attempted_at) VALUES(1, ?)
            ON CONFLICT(id) DO UPDATE SET attempted_at=excluded.attempted_at''', (stamp,))
    try:
        events = parse_events(fetch_html(EVENTS_URL))
        now = datetime.now(timezone.utc)
        selected = [e for e in events if now - timedelta(days=14) <= datetime.fromisoformat(e['starts_at']) <= now + timedelta(days=120)]
        selected.sort(key=lambda e: (datetime.fromisoformat(e['starts_at']) < now, e['starts_at']))
        selected = selected[:12]
        if not selected:
            raise ValueError('Listing has no events in the supported date window')
        with closing(sqlite3.connect(db_file, timeout=30)) as conn, conn:
            conn.executemany('''INSERT INTO ufc_events(url,slug,title,starts_at,listed_at) VALUES(?,?,?,?,?)
                ON CONFLICT(url) DO UPDATE SET title=excluded.title, starts_at=excluded.starts_at,
                listed_at=excluded.listed_at''', [(e['url'], e['slug'], e['title'], e['starts_at'], stamp) for e in selected])
            conn.execute('UPDATE ufc_source_status SET succeeded_at=?, last_error=NULL WHERE id=1', (stamp,))
    except Exception as exc:
        error = type(exc).__name__
        with closing(sqlite3.connect(db_file, timeout=30)) as conn, conn:
            conn.execute('UPDATE ufc_source_status SET last_error=? WHERE id=1', (error,))
        log.warning('UFC listing refresh failed (%s); retained cached events', error)
        return {'refreshed': 0, 'failed': 1, 'skipped': False}

    def card_result(event):
        try:
            return event, parse_card(fetch_html(event['url'])), None
        except Exception as exc:
            return event, None, type(exc).__name__
    refreshed, failed = 0, 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        for event, bouts, error in pool.map(card_result, selected):
            with closing(sqlite3.connect(db_file, timeout=30)) as conn, conn:
                if error:
                    conn.execute('UPDATE ufc_events SET last_error=? WHERE url=?', (error, event['url']))
                    failed += 1
                    continue
                data = json.dumps(bouts, sort_keys=True, ensure_ascii=False)
                digest = hashlib.sha256(data.encode()).hexdigest()
                previous = conn.execute('SELECT card_json FROM ufc_events WHERE url=?', (event['url'],)).fetchone()[0]
                if previous != data:
                    conn.execute('INSERT INTO ufc_card_history(event_url,card_hash,card_json,fetched_at) VALUES(?,?,?,?)',
                                 (event['url'], digest, data, stamp))
                conn.execute('UPDATE ufc_events SET card_json=?,card_fetched_at=?,last_error=NULL WHERE url=?', (data, stamp, event['url']))
                refreshed += 1
    log.info('UFC cards refreshed: %d; unavailable: %d', refreshed, failed)
    return {'refreshed': refreshed, 'failed': failed, 'skipped': False}


def cached_events(db_file):
    init_events(db_file)
    with closing(sqlite3.connect(db_file, timeout=30)) as conn:
        conn.row_factory = sqlite3.Row
        status = conn.execute('SELECT attempted_at,succeeded_at,last_error FROM ufc_source_status WHERE id=1').fetchone()
        result = []
        for row in conn.execute('SELECT * FROM ufc_events ORDER BY starts_at'):
            event = dict(row)
            event['bouts'] = json.loads(event.pop('card_json') or '[]')
            fetched = event['card_fetched_at']
            event['stale'] = bool((status and status['last_error']) or event['last_error'] or not fetched or
                                  datetime.now(timezone.utc) - datetime.fromisoformat(event['listed_at']) > timedelta(hours=REFRESH_HOURS) or
                                  datetime.now(timezone.utc) - datetime.fromisoformat(fetched) > timedelta(hours=REFRESH_HOURS))
            result.append(event)
    return {'source': EVENTS_URL, 'status': dict(status) if status else None, 'events': result}
