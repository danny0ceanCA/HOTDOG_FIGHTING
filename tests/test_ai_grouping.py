import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch, MagicMock

from ai_grouping import update_ai_groups, validate_assignments, request_assignments, name_ai_groups
from app import create_app
from news import save_articles_to_db


class AITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'test.db'
        self.app = create_app(self.db)
        save_articles_to_db(self.db, [dict(category='MMA', title=title, link=f'https://example.com/{i}',
                                        published_date='2026-11-03T12:00:00Z') for i, title in enumerate([
            'Jones fights Aspinall', 'Heavyweights booked for championship clash'])])
        with closing(sqlite3.connect(self.db)) as conn:
            self.ids = [r[0] for r in conn.execute('SELECT id FROM articles ORDER BY id')]
        self.settings = patch('ai_grouping.settings', return_value=('test-key', 'test-model'))
        self.settings.start()
        self.addCleanup(self.settings.stop)
        naming = patch('ai_grouping.name_ai_groups', return_value=0)
        self.mock_naming = naming.start()
        self.addCleanup(naming.stop)

    def assignments(self):
        return [dict(id=i, story_id=self.ids[0], confidence=.95) for i in self.ids]

    @patch('ai_grouping.request_assignments')
    def test_saved_groups_render_and_are_not_rebilled(self, request):
        request.return_value = self.assignments()
        self.assertEqual(update_ai_groups(self.db), 2)
        self.assertEqual(update_ai_groups(self.db), 0)
        self.assertEqual(request.call_count, 1)
        html = self.app.test_client().get('/').get_data(as_text=True)
        self.assertIn('View all 2 articles', html)
        self.assertEqual(request.call_count, 1)

    @patch('ai_grouping.request_assignments')
    def test_new_headline_can_join_existing_group(self, request):
        request.return_value = self.assignments()
        update_ai_groups(self.db)
        save_articles_to_db(self.db, [dict(category='MMA', title='Third report', link='https://other.com/new',
                                         published_date='2026-11-03T13:00:00Z')])
        with closing(sqlite3.connect(self.db)) as conn:
            new_id = conn.execute('SELECT MAX(id) FROM articles').fetchone()[0]
        request.return_value = [dict(id=new_id, story_id=self.ids[0], confidence=.95)]
        self.assertEqual(update_ai_groups(self.db), 1)
        self.assertIn('View all 3 articles', self.app.test_client().get('/').get_data(as_text=True))

    @patch('ai_grouping.request_assignments', side_effect=TimeoutError)
    def test_failure_leaves_pending_and_fallback_available(self, request):
        self.assertEqual(update_ai_groups(self.db), 0)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM ai_story_assignments').fetchone()[0], 0)
        self.assertEqual(self.app.test_client().get('/').status_code, 200)

    @patch('ai_grouping.request_assignments')
    def test_missing_key_never_calls_provider(self, request):
        with patch('ai_grouping.settings', return_value=('', 'model')):
            self.assertEqual(update_ai_groups(self.db), 0)
        request.assert_not_called()

    def test_invalid_ids_missing_ids_cycles_confidence_and_time(self):
        pending = [dict(id=i, published_date='2026-11-03T12:00:00+00:00') for i in (1, 2)]
        with self.assertRaises(ValueError):
            validate_assignments([dict(id=1, story_id=99, confidence=.99)], pending, {})
        with self.assertRaises(ValueError):
            validate_assignments([dict(id=1, story_id=99, confidence=.99), dict(id=2, story_id=2, confidence=1)], pending, {})
        result = validate_assignments([dict(id=1, story_id=2, confidence=.99), dict(id=2, story_id=1, confidence=.99)], pending, {})
        self.assertEqual(result, {1: 1, 2: 2})
        # Cycles must resolve conservatively to separate groups, never a transitive merge.
        result = validate_assignments([dict(id=1, story_id=1, confidence=1), dict(id=2, story_id=1, confidence=.5)], pending, {})
        self.assertEqual(result, {1: 1, 2: 2})
        result = validate_assignments([dict(id=1, story_id=1, confidence=1), dict(id=2, story_id=3, confidence=.99)], pending,
                                      {3: [dict(published_date='2026-10-01T12:00:00+00:00')]})
        self.assertEqual(result, {1: 1, 2: 2})

    @patch('ai_grouping.urlopen')
    def test_responses_api_contract(self, open_url):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'status': 'completed', 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps({'assignments': {
                str(a['id']): {'story_id': a['story_id'], 'confidence': a['confidence']} for a in self.assignments()}})}]}]}).encode()
        open_url.return_value = response
        self.assertEqual(request_assignments('test-key', 'test-model', [], []), self.assignments())
        payload = json.loads(open_url.call_args.args[0].data)
        self.assertFalse(payload['store'])
        self.assertTrue(payload['text']['format']['strict'])

    @patch('ai_grouping.request_assignments')
    @patch('ai_grouping.request_json')
    def test_titles_cached_and_links_inside_coverage(self, request_json, request):
        request.return_value = self.assignments()
        update_ai_groups(self.db)
        request_json.return_value = {str(self.ids[0]): 'HEAVYWEIGHT DRAMA GOES BOOM!'}
        # Call the imported real function; automatic naming is mocked in setUp.
        self.assertEqual(name_ai_groups(self.db), 1)
        self.assertEqual(name_ai_groups(self.db), 0)
        self.assertEqual(request_json.call_count, 1)
        html = self.app.test_client().get('/').get_data(as_text=True)
        self.assertIn('HEAVYWEIGHT DRAMA GOES BOOM!</h3>', html)
        start, coverage = html.split('<details>', 1)
        self.assertNotIn('https://example.com/0', start)
        self.assertIn('https://example.com/0', coverage)
        self.assertIn('https://example.com/1', coverage)

    @patch('ai_grouping.request_assignments')
    @patch('ai_grouping.request_json')
    def test_bad_or_failed_title_does_not_break_coverage(self, request_json, request):
        request.return_value = self.assignments()
        update_ai_groups(self.db)
        request_json.return_value = {str(self.ids[0]): ''}
        self.assertEqual(name_ai_groups(self.db), 0)
        request_json.side_effect = TimeoutError
        self.assertEqual(name_ai_groups(self.db), 0)
        self.assertIn('View all 2 articles', self.app.test_client().get('/').get_data(as_text=True))
