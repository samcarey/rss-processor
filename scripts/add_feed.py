"""Subscribe to a podcast feed and print its cleaned-feed URL.

The primary workflow (the web UI is obsolete): user pastes an RSS feed URL,
this inserts it (idempotent), nudges the worker to start immediately if it's
idle, and prints the URL to subscribe to in a podcast app.

Usage:
    python scripts/add_feed.py <feed_url> [<feed_url> ...]
    python scripts/add_feed.py --no-kick <feed_url>   # don't restart worker
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import yaml

from app.database import init_db, get_session
from app.models import Podcast, Episode
from processor.rss_generator import generate_feed_url
from processor.subscribe import add_podcast

WORKER = 'gui/501/com.rssprocessor.worker'


def main():
    args = sys.argv[1:]
    kick = '--no-kick' not in args
    urls = [a for a in args if a != '--no-kick']
    if not urls:
        sys.exit(__doc__)
    with open(os.path.join(ROOT, 'config.yaml')) as f:
        config = yaml.safe_load(f)
    init_db()
    session = get_session()

    added = False
    for url in urls:
        podcast, created = add_podcast(session, url.strip(),
                                       config['worker']['backfill_days'])
        added |= created
        n = session.query(Episode).filter_by(podcast_id=podcast.id).count()
        done = session.query(Episode).filter_by(podcast_id=podcast.id,
                                                processed=True).count()
        print(f"{'ADDED' if created else 'EXISTS'} [{podcast.id}] {podcast.title}"
              f" — {done}/{n} episodes processed")
        print(f"  {generate_feed_url(podcast.id)}")

    # The worker runs a cycle on startup; restarting it skips the up-to-30-min
    # wait. Only when idle -- a restart aborts in-flight episode work.
    if added and kick:
        busy = session.query(Podcast).filter(
            Podcast.processing_status.in_(['downloading', 'processing'])).count()
        if busy:
            print("Worker busy; new episodes will be picked up after the current run.")
        else:
            subprocess.run(['launchctl', 'kickstart', '-k', WORKER], check=False)
            print("Worker restarted; processing starts now.")


if __name__ == '__main__':
    main()
