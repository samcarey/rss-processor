"""Unsubscribe a podcast and delete all its data and files.

Usage:
    python scripts/remove_feed.py <podcast_id> [...]
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import yaml

from app.database import init_db, get_session
from app.models import Podcast
from processor.subscribe import remove_podcast


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    with open(os.path.join(ROOT, 'config.yaml')) as f:
        config = yaml.safe_load(f)
    init_db()
    session = get_session()
    for arg in sys.argv[1:]:
        podcast = session.get(Podcast, int(arg))
        if not podcast:
            print(f"[{arg}] not found")
            continue
        title = podcast.title
        n = remove_podcast(session, config, podcast)
        print(f"Removed [{arg}] {title} ({n} files deleted)")


if __name__ == '__main__':
    main()
