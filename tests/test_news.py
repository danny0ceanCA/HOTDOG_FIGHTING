import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError

import news
from app import create_app


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'news.db'

    def test_legacy_migration_backup_dates_and_uniqueness(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('CREATE TABLE articles(id INTEGER PRIMARY KEY, category, title, headline, link, published_date, scraped_date)')
            conn.executemany('INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?)', [
                (1, 'MMA', 'Old', '', 'https://example.com/a', 'Wed, 18 Dec 2024 23:53:25 GMT', '2025-01-01'),
                (2, 'MMA', 'New', '', 'https://example.com/a', '2024-12-18T15:53:25-08:00', '2025-01-02'),
                (3, 'Boxing', 'Other', '', 'https://example.com/b', 'bad', '2025-01-02'),
            ])
        news.init_db(self.db)
        news.init_db(self.db)
        backups = list(self.db.parent.glob('*.bak'))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM articles').fetchone()[0], 3)
        with closing(sqlite3.connect(self.db)) as conn:
            rows = conn.execute('SELECT title, published_date FROM articles ORDER BY id').fetchall()
        self.assertEqual(rows, [('New', '2024-12-18T23:53:25+00:00'), ('Other', '2025-01-02T00:00:00+00:00')])
        self.assertEqual(news.save_articles_to_db(self.db, [dict(category='MMA', title='Duplicate', link='https://example.com/a', published_date='2026-01-01')]), 0)

    def test_failed_migration_preserves_original(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('CREATE TABLE articles(title TEXT)')
            conn.execute("INSERT INTO articles VALUES ('preserve me')")
        with self.assertRaises(sqlite3.OperationalError):
            news.init_db(self.db)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT title FROM articles').fetchone()[0], 'preserve me')
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 0)
            self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='articles_migrated'").fetchone())

    def test_fresh_homepage_and_escaping(self):
        app = create_app(self.db)
        self.assertEqual(app.test_client().get('/').status_code, 200)
        news.save_articles_to_db(self.db, [dict(category='MMA', title='<script>bad()</script>', link='https://example.com', published_date='2026-01-01')])
        html = app.test_client().get('/').get_data(as_text=True)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>bad()', html)

    def test_source_failure_does_not_lose_successful_source(self):
        news.init_db(self.db)
        article = dict(category='MMA', title='Good', link='https://example.com', published_date='2026-01-01')
        def fetch(url, category):
            if url == 'bad':
                raise RuntimeError('test failure')
            return [article]
        with patch('news.fetch_news_rss', side_effect=fetch), patch('ai_grouping.update_ai_groups'):
            self.assertEqual(news.aggregate_news(self.db, {'MMA': ['bad', 'good']}), 1)


class FeedTests(unittest.TestCase):
    @patch('news.download_feed', return_value=b'feed')
    @patch('news.feedparser.parse')
    def test_bad_entries_duplicates_and_timezone_order(self, parse, download):
        parse.return_value = {'entries': [
            {'title': 'Missing link'},
            {'link': None, 'title': 'Bad'},
            {'title': 'Unsafe', 'link': 'javascript:alert(1)'},
            {'title': 'Earlier', 'link': 'https://example.com/1', 'published': '2026-01-01T10:00:00Z'},
            {'title': 'Later', 'link': {'href': 'https://example.com/2'}, 'updated': '2026-01-01T09:00:00-08:00'},
            {'title': 'Duplicate', 'link': 'https://example.com/1'},
        ]}
        result = news.fetch_news_rss('https://example.com/feed', 'MMA')
        self.assertEqual([a['title'] for a in result], ['Later', 'Earlier'])

    @patch('news.download_feed')
    def test_real_rss_and_atom_parsing(self, download):
        for xml in [
            b'<rss version="2.0"><channel><title>Test</title><item><title>Story</title><link>https://example.com/story</link><pubDate>Wed, 18 Dec 2024 23:53:25 GMT</pubDate></item></channel></rss>',
            b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Test</title><entry><title>Story</title><link href="https://example.com/story"/><updated>2024-12-18T23:53:25Z</updated></entry></feed>',
        ]:
            download.return_value = xml
            result = news.fetch_news_rss('https://example.com/feed', 'MMA')
            self.assertEqual(result[0]['published_date'], '2024-12-18T23:53:25+00:00')

    @patch('news.time.sleep')
    @patch('news.urlopen')
    def test_transient_retry_then_success(self, open_url, sleep):
        response = MagicMock()
        response.__enter__.return_value.read1.side_effect = [b'feed', b'']
        open_url.side_effect = [URLError('temporary'), response]
        self.assertEqual(news.download_feed('https://example.com'), b'feed')
        self.assertEqual(open_url.call_count, 2)
        self.assertEqual(open_url.call_args.kwargs['timeout'], news.FETCH_TIMEOUT)

    @patch('news.time.sleep')
    @patch('news.urlopen')
    def test_retry_exhaustion_and_permanent_failure(self, open_url, sleep):
        open_url.side_effect = TimeoutError('timeout')
        self.assertEqual(news.fetch_news_rss('https://example.com', 'MMA'), [])
        self.assertEqual(open_url.call_count, news.FETCH_ATTEMPTS)
        open_url.reset_mock()
        open_url.side_effect = HTTPError('https://example.com', 404, 'missing', {}, None)
        with self.assertRaises(HTTPError):
            news.download_feed('https://example.com')
        self.assertEqual(open_url.call_count, 1)

    @patch('news.urlopen')
    def test_oversized_feed_rejected(self, open_url):
        open_url.return_value.__enter__.return_value.read1.return_value = b'x' * (64 * 1024)
        with self.assertRaisesRegex(ValueError, 'size limit'):
            news.download_feed('https://example.com')

    @patch('news.time.sleep')
    @patch('news.time.monotonic')
    @patch('news.urlopen')
    def test_download_deadline(self, open_url, monotonic, sleep):
        monotonic.side_effect = [0, 11, 20, 31, 40, 51]
        with self.assertRaises(TimeoutError):
            news.download_feed('https://example.com')
        self.assertEqual(open_url.call_count, 3)


if __name__ == '__main__':
    unittest.main()
