import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from app import create_app
from event_coverage import update_event_coverage, candidate_events, event_homepage, coverage_sections
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
        self.config = patch('event_coverage.settings', return_value=('test', 'test-model'))
        self.config.start()
        self.addCleanup(self.config.stop)

    def decisions(self, key, model, prompt, inputs, schema):
        return {sid: {'event_url': None if 'favorite movie' in data['headlines'][0]['title'] else self.url,
                      'confidence': .99} for sid, data in inputs.items()}

    @patch('event_coverage.request_json')
    def test_event_umbrella_preserves_topics_and_unrelated_news(self, request):
        request.side_effect = self.decisions
        self.assertEqual(update_event_coverage(self.db), 3)
        with closing(sqlite3.connect(self.db)) as conn:
            hubs, other = event_homepage(self.db, conn)
        self.assertEqual(len(hubs), 1)
        self.assertEqual(len(hubs[0]['stories']), 2)
        self.assertEqual(len(other), 2)
        html = self.app.test_client().get('/').get_data(as_text=True)
        self.assertIn('UFC event coverage', html)
        self.assertIn('<details class="event-hub">', html)
        self.assertNotIn('<details class="event-hub" open', html)
        self.assertIn('Other MMA news', html)
        self.assertIn('Boxing still here', html)
        for i in range(4):
            self.assertEqual(html.count(f'https://source.com/{i}"'), 1)

    @patch('event_coverage.request_json')
    def test_matching_cached_and_never_called_on_page_load(self, request):
        request.side_effect = self.decisions
        update_event_coverage(self.db)
        self.assertEqual(update_event_coverage(self.db), 0)
        self.app.test_client().get('/')
        self.assertEqual(request.call_count, 1)

    @patch('event_coverage.request_json')
    def test_card_change_invalidates_event_assignment(self, request):
        request.side_effect = self.decisions
        update_event_coverage(self.db)
        with patch('ufc_events.fetch_html', side_effect=[listing(), card('replacement', 'New Opponent')]):
            refresh_events(self.db, force=True)
        with closing(sqlite3.connect(self.db)) as conn:
            hubs, other = event_homepage(self.db, conn)
        self.assertEqual(hubs, [])
        self.assertEqual(len(other), 4)

    @patch('event_coverage.request_json', side_effect=TimeoutError)
    def test_api_failure_keeps_all_topics_visible(self, request):
        self.assertEqual(update_event_coverage(self.db), 0)
        html = self.app.test_client().get('/').get_data(as_text=True)
        for title in self.titles:
            self.assertIn(title, html)

    @patch('event_coverage.request_json')
    def test_unknown_events_rejected(self, request):
        def bad(*args):
            return {sid: {'event_url': 'https://evil.test', 'confidence': 1} for sid in args[3]}
        request.side_effect = bad
        self.assertEqual(update_event_coverage(self.db), 0)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_story_links').fetchone()[0], 0)

    def test_old_fighter_story_not_candidate_for_new_card(self):
        event = cached_events(self.db)['events'][0]
        old = {'lead': {'date': datetime.now(timezone.utc) - timedelta(days=90)},
               'articles': [{'title': 'Waldo Cortes Acosta fight prediction'}]}
        self.assertEqual(candidate_events(old, [event]), [])

    def test_explicit_number_matches_months_before_card_without_current_fighter(self):
        now = datetime.now(timezone.utc)
        event = dict(slug='ufc-336', starts_at=(now + timedelta(days=87)).isoformat(), bouts=[])
        topic = {'lead': {'date': now}, 'articles': [{'title': 'Fighter withdraws from UFC 336'}]}
        self.assertEqual(candidate_events(topic, [event]), [event])
        topic['articles'][0]['title'] = 'Fighter withdraws from UFC 335'
        self.assertEqual(candidate_events(topic, [event]), [])
        topic['articles'][0]['title'] = 'Fighter withdraws from UFC 3360'
        self.assertEqual(candidate_events(topic, [event]), [])

    def test_generic_fighter_mentions_and_archive_not_event_candidates(self):
        event = cached_events(self.db)['events'][0]
        for title in ['Full Fight: Waldo Cortes Acosta vs Johnny Walker', 'Throwback: Johnny Walker full fight']:
            topic = {'lead': {'date': datetime.now(timezone.utc)}, 'articles': [{'title': title}]}
            self.assertEqual(candidate_events(topic, [event]), [])

    def test_priority_applies_before_limit_numbered_then_fight_night_groups_singles(self):
        now = datetime.now(timezone.utc)
        def topic(key, days=0, count=1):
            article = dict(title=key, published=(now - timedelta(days=days)).isoformat(),
                           date=now - timedelta(days=days), link=f'https://example.com/{key}')
            return dict(story_key=key, lead=article, articles=[article] * count)
        topics = [topic(f's{i}') for i in range(55)]
        topics += [topic('group', days=2, count=2), topic('night', days=3), topic('numbered', days=4), topic('past-numbered')]
        night = dict(url='https://www.ufc.com/event/fight-night', slug='fight-night', title='Night', starts_at=self.now)
        numbered = dict(url='https://www.ufc.com/event/ufc-334', slug='ufc-334', title='Numbered', starts_at=self.now)
        past = dict(url='https://www.ufc.com/event/ufc-333', slug='ufc-333', title='Past', starts_at=(now - timedelta(days=2)).isoformat())
        def candidates(group, events):
            return {'night': [night], 'numbered': [numbered], 'past-numbered': [past]}.get(group['story_key'], [])
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executemany('INSERT INTO event_story_links(story_key,event_url,input_hash,confidence,reviewed_at) VALUES(?,?,?,?,?)',
                             [(key, event['url'], 'unchanged', 1, self.now) for key, event in [('night', night), ('numbered', numbered), ('past-numbered', past)]])
        with patch('event_coverage.read_topics', return_value=topics), patch('event_coverage.cached_events', return_value={'events': [night, numbered]}), \
                patch('event_coverage.candidate_events', side_effect=candidates), patch('event_coverage.fingerprint', return_value='unchanged'), \
                closing(sqlite3.connect(self.db)) as conn:
            hubs, other = event_homepage(self.db, conn)
        self.assertEqual([h['slug'] for h in hubs], ['ufc-334', 'fight-night', 'ufc-333'])
        self.assertFalse(hubs[0]['past'])
        self.assertTrue(hubs[2]['past'])
        self.assertEqual(other[0]['story_key'], 'group')
        self.assertEqual(other[1]['story_key'], 's0')
        self.assertEqual(sum(len(h['stories']) for h in hubs) + len(other), 50)

    def test_past_hubs_interleave_with_singles_by_latest_article_not_event_date(self):
        now = datetime.now(timezone.utc)
        def topic(hours, count=1):
            return {'articles': [{}] * count, 'lead': {'date': now - timedelta(hours=hours)}}
        past = {'past': True, 'priority': 0, 'stories': [topic(3)], 'date': now - timedelta(days=7)}
        upcoming = {'past': False, 'priority': 1, 'stories': [topic(8)]}
        newer, older, grouped = topic(1), topic(5), topic(10, count=2)
        events, groups, tail = coverage_sections([past, upcoming], [older, grouped, newer])
        self.assertEqual(events, [upcoming])
        self.assertEqual(groups, [grouped])
        self.assertEqual([e['kind'] for e in tail], ['story', 'event', 'story'])
        self.assertEqual([e['value'] for e in tail], [newer, past, older])

    @patch('event_coverage.request_json')
    def test_current_fighter_news_joins_upcoming_card_with_fighter_label(self, request):
        save_articles_to_db(self.db, [dict(category='MMA', title='Johnny Walker discusses his confidence',
                                         link='https://confidence.com/article', published_date=self.now)])
        def decide(key, model, prompt, inputs, schema):
            return {sid: {'event_url': self.url if 'confidence' in data['headlines'][0]['title'] else None,
                          'confidence': .99, 'relation': 'fighter' if 'confidence' in data['headlines'][0]['title'] else 'none'}
                    for sid, data in inputs.items()}
        request.side_effect = decide
        update_event_coverage(self.db)
        html = self.app.test_client().get('/').get_data(as_text=True)
        top, other = html.split('Other MMA news', 1)
        self.assertIn('Johnny Walker discusses his confidence', top)
        self.assertIn('Fighter coverage', top)
        self.assertNotIn('Johnny Walker discusses his confidence', other)
