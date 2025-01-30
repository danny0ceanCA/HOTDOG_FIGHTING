# ------------------------
# 📌 1. IMPORTS & SETUP
# ------------------------
import os
import feedparser  # For parsing RSS feeds
import random  # For random shuffling (not strictly used here, but kept for reference)
from flask import Flask, render_template_string  # For the web interface
import sqlite3  # For the database
from datetime import datetime  # For timestamps
import logging  # For debugging and error tracking
from dateutil import parser  # For parsing published dates
from apscheduler.schedulers.background import BackgroundScheduler  # For periodic updates

# ------------------------
# 📌 2. ENVIRONMENT CONFIGURATION
# ------------------------

# Flask configuration
DEBUG_MODE = os.getenv('DEBUG_MODE', 'False').lower() == 'true'

# Configure logging
logging.basicConfig(
    filename='app.log',
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s:%(message)s'
)

# ------------------------
# 📌 3. CONFIGURATION
# ------------------------

# Define RSS News Sources for MMA and Boxing
NEWS_SOURCES = {
    'MMA': [
        'https://www.bloodyelbow.com/rss/index.xml',
        'https://www.lowkickmma.com/feed/',
        'https://www.mmamania.com/rss/index.xml',
        'https://www.ufc.com/rss/news',
        'https://www.mmaweekly.com/feed'
    ],
    'Boxing': [
        'https://www.badlefthook.com/rss/index.xml',
        'https://www.boxingscene.com/rss/news.xml',
        'https://www.worldboxingnews.net/feed'
    ]
}

# Database file
DB_FILE = 'news.db'

# Number of articles per category to display
ARTICLE_LIMIT = 50  # Show up to 50 articles per category

# ------------------------
# 📌 4. DATABASE SETUP
# ------------------------
def init_db():
    """Initialize the database and set up tables with indexing."""
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT,
                title TEXT,
                link TEXT UNIQUE,
                published_date TEXT,
                scraped_date TEXT
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_category ON articles (category);')
        c.execute('CREATE INDEX IF NOT EXISTS idx_published_date ON articles (published_date);')
        conn.commit()
    logging.info("✅ Database initialized successfully.")

# ------------------------
# 📌 5. NEWS AGGREGATION
# ------------------------
def fetch_news_rss(feed_url, category):
    """Fetch news articles from RSS feeds."""
    try:
        feed = feedparser.parse(feed_url)
        articles = []
        seen_links = set()

        for entry in feed.entries:
            # Ensure we skip duplicates in a single feed fetch
            if entry.link not in seen_links:
                # Handle date parsing
                published_date = entry.get('published', None) or entry.get('updated', None)
                if published_date:
                    try:
                        published_date = parser.parse(published_date).isoformat()
                    except (ValueError, TypeError):
                        published_date = datetime.now().isoformat()
                else:
                    published_date = datetime.now().isoformat()

                articles.append({
                    "category": category,
                    "title": entry.title,
                    "link": entry.link,
                    "published_date": published_date
                })
                seen_links.add(entry.link)

        # Sort articles by published date (newest first)
        articles.sort(key=lambda x: x['published_date'], reverse=True)
        return articles

    except Exception as e:
        logging.error(f"Failed to fetch RSS feed {feed_url}: {e}")
        return []

def save_articles_to_db(articles):
    """Save articles to the database, ignoring duplicates."""
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        for article in articles:
            try:
                c.execute('''
                    INSERT OR IGNORE INTO articles (category, title, link, published_date, scraped_date)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    article['category'],
                    article['title'],
                    article['link'],
                    article['published_date'],
                    datetime.now().isoformat()
                ))
            except sqlite3.IntegrityError:
                logging.info(f"Skipping duplicate article: {article['link']}")
        conn.commit()
    logging.info("✅ Articles saved to database without duplicates.")

def aggregate_news():
    """Aggregate and randomize news from all sources without duplicates."""
    all_articles = []
    processed_links = set()

    for category, sources in NEWS_SOURCES.items():
        for source in sources:
            articles = fetch_news_rss(source, category)
            for article in articles:
                if article['link'] not in processed_links:
                    all_articles.append(article)
                    processed_links.add(article['link'])

    # Save collected articles to the database
    save_articles_to_db(all_articles)
    logging.info("🔄 News aggregation completed successfully.")

# ------------------------
# 📌 6. FLASK WEB SERVER
# ------------------------
app = Flask(__name__)

@app.route('/')
def home():
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()

        # Fetch MMA articles (distinct by title, link)
        c.execute(
            f'''SELECT DISTINCT title, link, published_date FROM articles 
                WHERE category = "MMA" 
                ORDER BY datetime(published_date) DESC 
                LIMIT {ARTICLE_LIMIT}
            '''
        )
        mma_rows = c.fetchall()
        mma_articles = [
            (title, link, f"Published on: {datetime.fromisoformat(published_date).strftime('%m-%d-%Y')}")
            for (title, link, published_date) in mma_rows
        ]

        # Fetch Boxing articles (distinct by title, link)
        c.execute(
            f'''SELECT DISTINCT title, link, published_date FROM articles 
                WHERE category = "Boxing" 
                ORDER BY datetime(published_date) DESC 
                LIMIT {ARTICLE_LIMIT}
            '''
        )
        boxing_rows = c.fetchall()
        boxing_articles = [
            (title, link, f"Published on: {datetime.fromisoformat(published_date).strftime('%m-%d-%Y')}")
            for (title, link, published_date) in boxing_rows
        ]

    return render_template_string('''
    <!DOCTYPE html>
<html>
<head>
    <title>HOTDOG FIGHTING</title>
    <!-- Google Analytics 4 -->
    <script async src="https://www.googletagmanager.com/gtag/js?id=G-4BSML4TG35"></script>
    <script>
        window.dataLayer = window.dataLayer || [];
        function gtag(){dataLayer.push(arguments);}
        gtag('js', new Date());
        gtag('config', 'YOUR_MEASUREMENT_ID');
    </script>
    <!-- Ads Script -->
    <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-2588231783119866" crossorigin="anonymous"></script>
    <style>
        body { font-family: Arial, sans-serif; }
        h1 { text-align: center; font-size: 3em; }
        .news-container { display: flex; gap: 20px; justify-content: space-around; }
        .news-section ul { list-style-type: none; padding: 0; }
        .news-section li { margin-bottom: 8px; }
    </style>
</head>
    <body>
        <h1>HOTDOG FIGHTING</h1>
        <div class="news-container">
            <div class="news-section">
                <h2>MMA News</h2>
                <ul>
                {% for title, link, published_date in mma_articles %}
                    <li>
                        <a href="{{ link }}">{{ title }}</a>
                        <span> ({{ published_date }})</span>
                    </li>
                {% endfor %}
                </ul>
            </div>
            <div class="news-section">
                <h2>Boxing News</h2>
                <ul>
                {% for title, link, published_date in boxing_articles %}
                    <li>
                        <a href="{{ link }}">{{ title }}</a>
                        <span> ({{ published_date }})</span>
                    </li>
                {% endfor %}
                </ul>
            </div>
        </div>
    </body>
    </html>
    ''', mma_articles=mma_articles, boxing_articles=boxing_articles)

# ------------------------
# 📌 7. MAIN ENTRY POINT
# ------------------------
if __name__ == '__main__':
    # 1. Initialize the database
    init_db()

    # 2. Perform an initial fetch (so the site has articles on first load)
    aggregate_news()

    # 3. Set up the scheduler to periodically call aggregate_news()
    scheduler = BackgroundScheduler()
    # Example: Run every 10 minutes
    scheduler.add_job(aggregate_news, 'interval', minutes=10)
    scheduler.start()

    # 4. Run the Flask app
    app.run(host='0.0.0.0', port=8000, debug=False)
