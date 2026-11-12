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
from ai_grouping import init_schema
from ufc_events import init_events, refresh_events, cached_events
from event_coverage import init_coverage, update_event_coverage, article_feed


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
            return render_template('home.html',
                mma_articles=article_feed(app.config['DB_FILE'], conn, 'MMA'),
                boxing_articles=article_feed(app.config['DB_FILE'], conn, 'Boxing'))

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--init-db', action='store_true', help='Migrate the database and exit')
    parser.add_argument('--refresh', action='store_true', help='Fetch feeds once and exit')
    parser.add_argument('--refresh-events', action='store_true', help='Refresh official UFC event cards and exit')
    parser.add_argument('--events-status', action='store_true', help='Show cached event source status and exit')
    parser.add_argument('--match-events', action='store_true', help='Tag individual articles with cached UFC events and exit')
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
