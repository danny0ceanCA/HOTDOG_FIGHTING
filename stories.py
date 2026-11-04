"""Conservative headline clustering without external services or article scraping."""
import html
import re
import unicodedata
from datetime import datetime
from urllib.parse import urlsplit

STOP = set('a an the to of in on at for from with and or as by is are was be his her he she it its after before over into says said ufc mma boxing news video watch full'.split())
PHASES = {
    'announcement': {'announce', 'announces', 'announced', 'books', 'booked', 'scheduled', 'set', 'confirmed'},
    'cancellation': {'cancelled', 'canceled', 'cancels', 'withdraws', 'withdrawal', 'postponed', 'scrapped'},
    'result': {'wins', 'beats', 'defeats', 'stops', 'knocks', 'taps', 'submitted', 'submits', 'results', 'result', 'ko', 'tko'},
    'preview': {'prediction', 'predictions', 'preview', 'odds', 'picks'},
    'reaction': {'reacts', 'reaction', 'reactions', 'responds', 'slams', 'criticizes'},
}
PUBLISHERS = {
    'bloodyelbow.com': 'Bloody Elbow', 'lowkickmma.com': 'LowKick MMA',
    'mmamania.com': 'MMA Mania', 'ufc.com': 'UFC', 'mmafighting.com': 'MMA Fighting',
    'mmaweekly.com': 'MMA Weekly', 'mmajunkie.usatoday.com': 'MMA Junkie',
    'badlefthook.com': 'Bad Left Hook', 'boxingscene.com': 'BoxingScene',
    'worldboxingnews.net': 'World Boxing News',
}


def features(title):
    text = unicodedata.normalize('NFKD', html.unescape(title)).encode('ascii', 'ignore').decode().lower()
    words = set(re.findall(r'[a-z0-9]+', text))
    phases = {name for name, terms in PHASES.items() if words & terms}
    return words - STOP, phases


def same_story(left, right):
    if abs((left['date'] - right['date']).total_seconds()) > 48 * 3600:
        return False
    a, pa = left['features']
    b, pb = right['features']
    # Distinct developments and fight numbers must remain separate.
    if pa != pb or {w for w in a if w.isdigit()} != {w for w in b if w.isdigit()}:
        return False
    shared = len(a & b)
    return shared >= 3 and 2 * shared / max(1, len(a) + len(b)) >= 0.64


def group_stories(rows, limit=50, assignments=None, titles=None):
    """Rows must be newest first. Match every member to prevent topic chaining."""
    groups = []
    assignments = assignments or {}
    for title, link, published in rows:
        host = (urlsplit(link).hostname or '').removeprefix('www.')
        article = dict(title=html.unescape(title), link=link, published=published,
                       date=datetime.fromisoformat(published), publisher=PUBLISHERS.get(host, host),
                       features=features(title))
        story_id = assignments.get(link)
        for group in groups:
            if ((story_id is not None and group.get('ai_story_id') == story_id) or
                (story_id is None and group.get('ai_story_id') is None and
                 all(same_story(article, member) for member in group['articles']))):
                group['articles'].append(article)
                break
        else:
            groups.append({'lead': article, 'articles': [article], 'ai_story_id': story_id})
    for group in groups:
        group['publisher_count'] = len({a['publisher'] for a in group['articles']})
        group['title'] = (titles or {}).get(group['ai_story_id']) if len(group['articles']) > 1 else None
    return groups[:limit]
