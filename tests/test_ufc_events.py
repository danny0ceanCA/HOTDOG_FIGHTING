import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ufc_events import parse_events, parse_card, refresh_events, cached_events, official_url


def listing():
    stamp = int(datetime.now(timezone.utc).timestamp()) + 86400
    return f'''<article class="c-card-event--result">
        <h3 class="c-card-event--result__headline"><a href="/event/ufc-test">Test vs Opponent</a></h3>
        <div data-main-card-timestamp="{stamp}"></div></article>'''


def card(blue='johnny-walker', name='Johnny Walker'):
    return f'''<div class="c-listing-fight">
        <div class="c-listing-fight__class-text">Heavyweight Bout</div>
        <div class="c-listing-fight__corner-name--red"><a href="https://www.ufc.com/athlete/waldo-cortes-acosta">
            <span>Waldo</span><span>Cortes Acosta</span></a></div>
        <div class="c-listing-fight__corner-name--blue"><a href="/athlete/{blue}">{name}</a></div>
        </div>'''


class UFCEventsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / 'news.db'

    def test_parses_official_markup_and_rejects_unexpected_structure(self):
        event = parse_events(listing())[0]
        self.assertEqual(event['url'], 'https://www.ufc.com/event/ufc-test')
        bouts = parse_card(card())
        self.assertEqual(bouts[0]['red']['name'], 'Waldo Cortes Acosta')
        self.assertEqual(bouts[0]['blue']['id'], 'johnny-walker')
        with self.assertRaises(ValueError):
            parse_events('<html>Access denied</html>')
        with self.assertRaises(ValueError):
            parse_card('<div class="c-listing-fight"></div>')

    def test_only_official_event_and_athlete_links(self):
        for url in ['https://evil.example/event/ufc-test', '//evil.example/event/ufc-test', 'http://www.ufc.com/event/ufc-test']:
            with self.assertRaises(ValueError):
                official_url(url, 'event')

    @patch('ufc_events.fetch_html')
    def test_cache_rate_limit_and_listing_failure_keep_card(self, fetch):
        fetch.side_effect = [listing(), card()]
        self.assertEqual(refresh_events(self.db)['refreshed'], 1)
        first = cached_events(self.db)
        self.assertFalse(first['events'][0]['stale'])
        fetch.reset_mock(side_effect=True)
        self.assertTrue(refresh_events(self.db)['skipped'])
        fetch.assert_not_called()
        fetch.side_effect = TimeoutError
        self.assertEqual(refresh_events(self.db, force=True)['failed'], 1)
        last = cached_events(self.db)
        self.assertEqual(first['events'][0]['bouts'], last['events'][0]['bouts'])
        self.assertEqual(last['status']['last_error'], 'TimeoutError')
        self.assertTrue(last['events'][0]['stale'])

    @patch('ufc_events.fetch_html')
    def test_card_failure_retains_snapshot_and_reports_stale(self, fetch):
        fetch.side_effect = [listing(), card()]
        refresh_events(self.db)
        fetch.side_effect = [listing(), '<html>Unavailable</html>']
        result = refresh_events(self.db, force=True)
        self.assertEqual(result['failed'], 1)
        saved = cached_events(self.db)['events'][0]
        self.assertTrue(saved['stale'])
        self.assertEqual(saved['bouts'][0]['blue']['id'], 'johnny-walker')

    @patch('ufc_events.fetch_html')
    def test_replacement_updates_current_card_preserves_history(self, fetch):
        fetch.side_effect = [listing(), card(), listing(), card('replacement', 'New Opponent'), listing(), card('replacement', 'New Opponent')]
        for _ in range(3):
            refresh_events(self.db, force=True)
        self.assertEqual(cached_events(self.db)['events'][0]['bouts'][0]['blue']['id'], 'replacement')
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM ufc_card_history').fetchone()[0], 2)

    def test_empty_cache_endpoint_does_not_fetch(self):
        from app import create_app
        app = create_app(self.db)
        with patch('ufc_events.fetch_html') as fetch:
            response = app.test_client().get('/api/events')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['events'], [])
        fetch.assert_not_called()
