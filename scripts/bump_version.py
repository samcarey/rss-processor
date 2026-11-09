"""Bump the processor version of one or more input feeds.

Run whenever the way a feed is processed changes (then reprocess it and
commit; the commit hook stamps the new versions into the message).

Usage:
    python scripts/bump_version.py <podcast_id> [...] -m "what changed"
    python scripts/bump_version.py all -m "what changed"
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.database import init_db, get_session
from app.models import Podcast
from processor.versions import bump


def main():
    args = sys.argv[1:]
    if '-m' not in args or args.index('-m') == len(args) - 1:
        sys.exit(__doc__)
    i = args.index('-m')
    note, ids = args[i + 1], args[:i]
    if not ids:
        sys.exit(__doc__)
    init_db()
    session = get_session()
    podcasts = (session.query(Podcast).all() if ids == ['all']
                else [session.get(Podcast, int(x)) for x in ids])
    for p in podcasts:
        if p is None:
            sys.exit("unknown podcast id")
        v = bump(p.feed_url, p.title, note)
        print(f"[{p.id}] {p.title} -> v{v}")


if __name__ == '__main__':
    main()
