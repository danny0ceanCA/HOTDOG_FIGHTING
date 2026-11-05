"""Web app and single-process production launcher."""
import argparse
import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, render_template

from news import aggregate_news, init_db
from stories import group_stories
from ai_grouping import init_schema, update_ai_groups, name_ai_groups
from ufc_events import init_events, refresh_events, cached_events
from event_coverage import init_coverage, update_event_coverage, event_homepage, coverage_sections


def refresh_event_data(db_file, force=False):
    result = refresh_events(db_file, force=force)
    update_event_coverage(db_file)
    return result


def create_app(db_file=None):
    app = Flask(__name__)
    app.config['DB_FILE'] = str(db_file or os.getenv('DB_FILE') or Path(__file__).with_name('news.db'))
    init_db(app.config['DB_FILE'])
    init_schema(app.config['DB_FILE'])
    init_events(app.config['DB_FILE'])
    init_coverage(app.config['DB_FILE'])

    @app.route('/api/events')
    def events():
        return cached_events(app.config['DB_FILE'])

    @app.route('/')
    def home():
        with closing(sqlite3.connect(app.config['DB_FILE'], timeout=30)) as conn:
            def articles(category):
                rows = conn.execute('''SELECT title, link, published_date FROM articles
                    WHERE category = ? ORDER BY published_date DESC, id DESC LIMIT 400''', (category,)).fetchall()
                assignments = dict(conn.execute('''SELECT a.link, s.story_id FROM articles a
                    JOIN ai_story_assignments s ON s.article_id=a.id WHERE a.category=?''', (category,)))
                titles = dict(conn.execute('SELECT story_id, title FROM ai_story_titles'))
                return group_stories(rows, assignments=assignments, titles=titles)
            hubs, other_mma = event_homepage(app.config['DB_FILE'], conn)
            upcoming, groups, timeline = coverage_sections(hubs, other_mma)
            return render_template('home.html', event_hubs=upcoming, mma_articles=other_mma,
                                   mma_groups=groups, mma_timeline=timeline,
                                   boxing_articles=articles('Boxing'))

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--init-db', action='store_true', help='Migrate the database and exit')
    parser.add_argument('--refresh', action='store_true', help='Fetch feeds once and exit')
    parser.add_argument('--group-stories', action='store_true', help='AI-review pending headlines once and exit')
    parser.add_argument('--name-stories', action='store_true', help='Name existing story groups once and exit')
    parser.add_argument('--refresh-events', action='store_true', help='Refresh official UFC event cards and exit')
    parser.add_argument('--events-status', action='store_true', help='Show cached event source status and exit')
    parser.add_argument('--match-events', action='store_true', help='Match topic groups to cached UFC cards and exit')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    app = create_app()
    if args.init_db:
        return
    if args.match_events:
        print(update_event_coverage(app.config['DB_FILE']))
        return
    if args.refresh_events:
        print(refresh_event_data(app.config['DB_FILE'], force=True))
        return
    if args.events_status:
        import json
        print(json.dumps(cached_events(app.config['DB_FILE']), indent=2, ensure_ascii=False))
        return
    if args.name_stories:
        name_ai_groups(app.config['DB_FILE'])
        return
    if args.group_stories:
        update_ai_groups(app.config['DB_FILE'])
        return
    if args.refresh:
        aggregate_news(app.config['DB_FILE'])
        return
    from waitress import serve
    scheduler = BackgroundScheduler(timezone='UTC')
    scheduler.add_job(refresh_event_data, 'interval', hours=6, args=[app.config['DB_FILE']],
                      next_run_time=datetime.now(timezone.utc), max_instances=1, coalesce=True)
    scheduler.add_job(aggregate_news, 'interval', minutes=int(os.getenv('REFRESH_MINUTES', '90')),
                      args=[app.config['DB_FILE']], next_run_time=datetime.now(timezone.utc),
                      max_instances=1, coalesce=True)
    scheduler.start()
    try:
        serve(app, host='0.0.0.0', port=int(os.getenv('PORT', '8000')))
    finally:
        scheduler.shutdown(wait=True)


if __name__ == '__main__':
    main()
