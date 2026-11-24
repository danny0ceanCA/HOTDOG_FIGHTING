import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from app import create_app
from event_coverage import update_event_coverage, candidate_events, article_feed
from news import save_articles_to_db
from ufc_events import refresh_events, cached_events
from test_ufc_events import listing, card


class EventCoverageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / 'test.db'
        self.app = create_app(self.db)
        with patch('ufc_events.fetch_html', side_effect=[listing(), card()]):
            refresh_events(self.db)
        self.url = 'https://www.ufc.com/event/ufc-test'
        self.now = datetime.now(timezone.utc).isoformat()
        self.titles = ['Waldo Cortes Acosta vs Johnny Walker odds and prediction',
                       'Waldo Cortes Acosta weighs in ahead of Johnny Walker fight',
                       'Johnny Walker shares his favorite movie', 'Conor McGregor documentary news']
        save_articles_to_db(self.db, [dict(category='MMA', title=title, link=f'https://source.com/{i}', published_date=self.now)
                                     for i, title in enumerate(self.titles)])
        save_articles_to_db(self.db, [dict(category='Boxing', title='Boxing still here', link='https://boxing.com/1', published_date=self.now)])
        config = patch('event_coverage.settings', return_value=('test', 'test-model'))
        config.start()
        self.addCleanup(config.stop)

    def decisions(self, key, model, prompt, inputs, schema):
        return {sid: {'event_url': None if 'favorite movie' in data['headlines'][0]['title'] else self.url,
                      'confidence': .99, 'relation': 'event'} for sid, data in inputs.items()}

    def feed(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return article_feed(self.db, conn, 'MMA')

    @patch('event_coverage.request_json')
    def test_individual_articles_have_event_tags_without_grouping(self, request):
        request.side_effect = self.decisions
        self.assertEqual(update_event_coverage(self.db), 3)
        articles = self.feed()
        by_title = {a['title']: a for a in articles}
        self.assertEqual(len(by_title[self.titles[0]]['event_tags']), 1)
        self.assertEqual(by_title[self.titles[2]]['event_tags'], [])
        html = self.app.test_client().get('/').get_data(as_text=True)
        self.assertNotIn('<details', html)
        self.assertNotIn('View all', html)
        self.assertIn('class="event-tag"', html)
        self.assertIn('Boxing still here', html)
        for i in range(4):
            self.assertEqual(html.count(f'https://source.com/{i}"'), 1)

    @patch('event_coverage.request_json')
    def test_tags_never_override_date_order_and_no_calls_on_page_load(self, request):
        request.side_effect = self.decisions
        update_event_coverage(self.db)
        save_articles_to_db(self.db, [dict(category='MMA', title='Newest unrelated article',
            link='https://newest.com/1', published_date=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())])
        self.assertEqual(update_event_coverage(self.db), 0)
        self.assertEqual(self.feed()[0]['title'], 'Newest unrelated article')
        html = self.app.test_client().get('/').get_data(as_text=True)
        self.assertLess(html.index('Newest unrelated article'), html.index(self.titles[0]))
        self.assertEqual(request.call_count, 1)

    @patch('event_coverage.request_json')
    def test_card_change_invalidates_cached_fighter_match(self, request):
        request.side_effect = self.decisions
        update_event_coverage(self.db)
        with patch('ufc_events.fetch_html', side_effect=[listing(), card('replacement', 'New Opponent')]):
            refresh_events(self.db, force=True)
        self.assertFalse(any(a['event_tags'] for a in self.feed()))

    @patch('event_coverage.request_json', side_effect=TimeoutError)
    def test_api_failure_keeps_every_article_visible(self, request):
        self.assertEqual(update_event_coverage(self.db), 0)
        html = self.app.test_client().get('/').get_data(as_text=True)
        for title in self.titles:
            self.assertIn(title, html)

    @patch('event_coverage.request_json')
    def test_unknown_event_ids_rejected(self, request):
        request.side_effect = lambda *args: {sid: {'event_url': 'https://evil.test', 'confidence': 1} for sid in args[3]}
        self.assertEqual(update_event_coverage(self.db), 0)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_story_links').fetchone()[0], 0)

    def test_early_explicit_event_reference_gets_tag_without_ai(self):
        now = datetime.now(timezone.utc)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('INSERT INTO ufc_events(url,slug,title,starts_at,listed_at) VALUES(?,?,?,?,?)',
                ('https://www.ufc.com/event/ufc-336', 'ufc-336', 'Main event', (now + timedelta(days=87)).isoformat(), self.now))
        save_articles_to_db(self.db, [dict(category='MMA', title='Withdrawal from UFC 336', link='https://early.com/1', published_date=self.now)])
        early = next(a for a in self.feed() if a['title'] == 'Withdrawal from UFC 336')
        self.assertEqual(early['event_tags'][0]['label'], 'UFC 336')

    @patch('event_coverage.request_json')
    def test_follow_event_tag_to_ordered_coverage_and_official_card(self, request):
        interview = 'Johnny Walker reflects on his career'
        save_articles_to_db(self.db, [dict(category='MMA', title=interview, link='https://source.com/interview',
            published_date=(datetime.fromisoformat(self.now) - timedelta(hours=2)).isoformat())])

        def decisions(key, model, prompt, inputs, schema):
            result = self.decisions(key, model, prompt, inputs, schema)
            for article_id, data in inputs.items():
                if data['headlines'][0]['title'] == interview:
                    result[article_id]['relation'] = 'fighter'
            return result

        request.side_effect = decisions
        update_event_coverage(self.db)
        client = self.app.test_client()
        home = BeautifulSoup(client.get('/').data, 'html.parser')
        tags = home.select('a.event-tag')
        self.assertEqual({tag['href'] for tag in tags}, {'/events/ufc-test'})
        card_label = 'UFC Fight Night: Test vs Opponent'
        self.assertCountEqual([tag.get_text(strip=True) for tag in tags],
                              [card_label, card_label, f'Fighter on {card_label}'])

        response = client.get(tags[0]['href'])
        self.assertEqual(response.status_code, 200)
        event = BeautifulSoup(response.data, 'html.parser')
        official = event.find('a', href=self.url)
        self.assertIsNotNone(official)
        self.assertIn('official', official.get_text(strip=True).lower())
        self.assertEqual([a['href'] for a in event.select('a.story-title')],
                         ['https://source.com/1', 'https://source.com/0', 'https://source.com/interview'])
        interview_card = event.find('a', href='https://source.com/interview').find_parent('li')
        self.assertIn(f'Fighter on {card_label}', interview_card.get_text(' ', strip=True))
        self.assertEqual(request.call_count, 1)

    def test_numbered_event_shows_explicit_tags_beyond_homepage_and_ai_limits(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('INSERT INTO ufc_events(url,slug,title,starts_at,listed_at) VALUES(?,?,?,?,?)',
                         ('https://www.ufc.com/event/ufc-336', 'ufc-336', 'Main event', self.now, self.now))
        old_date = (datetime.fromisoformat(self.now) - timedelta(days=2)).isoformat()
        save_articles_to_db(self.db, [dict(category='MMA', title='UFC 336 card announcement',
            link='https://source.com/announcement', published_date=old_date)])
        save_articles_to_db(self.db, [dict(category='MMA', title=f'Other news {i}',
            link=f'https://other.com/{i}', published_date=self.now) for i in range(401)])
        save_articles_to_db(self.db, [dict(category='MMA', title='UFC 336 weigh-ins',
            link='https://source.com/weigh-ins', published_date=self.now)])

        client = self.app.test_client()
        with patch('event_coverage.request_json') as request:
            home = BeautifulSoup(client.get('/').data, 'html.parser')
            self.assertIsNone(home.find('a', href='https://source.com/announcement'))
            tag = home.find('a', href='/events/ufc-336')
            self.assertIsNotNone(tag)
            event = BeautifulSoup(client.get(tag['href']).data, 'html.parser')
            self.assertEqual([a['href'] for a in event.select('a.story-title')],
                             ['https://source.com/weigh-ins', 'https://source.com/announcement'])
            request.assert_not_called()

    def test_unknown_event_page_is_not_found(self):
        self.assertEqual(self.app.test_client().get('/events/no-such-card').status_code, 404)

    def test_archives_and_old_generic_mentions_not_candidates(self):
        event = cached_events(self.db)['events'][0]
        for title, date in [('Full Fight: Waldo Cortes Acosta vs Johnny Walker', datetime.now(timezone.utc)),
                            ('Johnny Walker discusses training', datetime.now(timezone.utc) - timedelta(days=90))]:
            topic = {'lead': {'date': date}, 'articles': [{'title': title}]}
            self.assertEqual(candidate_events(topic, [event]), [])

    @patch('event_coverage.request_json')
    def test_fighter_tag_is_metadata_not_a_group(self, request):
        request.side_effect = lambda *args: {sid: {'event_url': self.url, 'confidence': .99, 'relation': 'fighter'} for sid in args[3]}
        update_event_coverage(self.db)
        article = next(a for a in self.feed() if a['title'] == self.titles[0])
        self.assertTrue(article['event_tags'][0]['fighter'])
        html = self.app.test_client().get('/').get_data(as_text=True)
        self.assertNotIn('<details', html)
        self.assertEqual(html.count('class="story-card"'), 5)

    @patch('event_coverage.request_json')
    def test_historical_group_cannot_spread_a_tag_to_another_article(self, request):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM articles WHERE category='MMA' ORDER BY id")]
            conn.executemany('INSERT INTO ai_story_assignments VALUES(?,?,?,?)',
                             [(aid, ids[0], 'old-model', self.now) for aid in ids])
        def independent(key, model, prompt, inputs, schema):
            return {sid: {'event_url': self.url if 'odds' in data['headlines'][0]['title'] else None,
                          'confidence': .99, 'relation': 'event'} for sid, data in inputs.items()}
        request.side_effect = independent
        update_event_coverage(self.db)
        by_title = {a['title']: a for a in self.feed()}
        self.assertTrue(by_title[self.titles[0]]['event_tags'])
        self.assertFalse(by_title[self.titles[1]]['event_tags'])
