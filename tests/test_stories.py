import unittest
from stories import group_stories


def row(title, source='one', date='2026-11-03T12:00:00+00:00'):
    return title, f'https://{source}.com/story', date


class StoryTests(unittest.TestCase):
    def test_similar_coverage_grouped(self):
        groups = group_stories([
            row('UFC announces Jon Jones vs Tom Aspinall title fight'),
            row('Jon Jones vs Tom Aspinall title fight announced', 'two'),
        ])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]['articles']), 2)
        self.assertEqual(groups[0]['publisher_count'], 2)

    def test_distinct_developments_and_events_stay_separate(self):
        titles = ['Jon Jones vs Tom Aspinall title fight announced',
                  'Jon Jones vs Tom Aspinall title fight cancelled',
                  'Jon Jones vs Tom Aspinall title fight results',
                  'Jon Jones vs Tom Aspinall title fight predictions']
        self.assertEqual(len(group_stories([row(t) for t in titles])), 4)
        self.assertEqual(len(group_stories([row('UFC 310 full fight card announced'),
                                           row('UFC 311 full fight card announced')])), 2)

    def test_old_coverage_does_not_join_new_story(self):
        title = 'Jon Jones vs Tom Aspinall title fight announced'
        self.assertEqual(len(group_stories([row(title), row(title, 'two', '2026-10-01T12:00:00+00:00')])), 2)

    def test_entities_and_case_normalized(self):
        groups = group_stories([row('Jones &amp; Aspinall title fight announced'),
                               row('JONES & Aspinall title fight announced', 'two')])
        self.assertEqual(len(groups), 1)
        self.assertNotIn('&amp;', groups[0]['lead']['title'])

    def test_expandable_coverage_rendered(self):
        import tempfile
        from pathlib import Path
        from app import create_app
        from news import save_articles_to_db
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'test.db'
            app = create_app(db)
            save_articles_to_db(db, [dict(title=t, link=link, published_date=d, category='MMA') for t, link, d in [
                row('Jon Jones vs Tom Aspinall title fight announced'),
                row('Jon Jones vs Tom Aspinall title fight announced', 'two')]])
            html = app.test_client().get('/').get_data(as_text=True)
            self.assertEqual(html.count('class="story-card"'), 1)
            self.assertIn('<details>', html)
            self.assertIn('View all 2 articles', html)
            self.assertIn('https://two.com/story', html)


if __name__ == '__main__':
    unittest.main()
