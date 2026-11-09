"""Processing status + cuts summary, for the CLI workflow.

Usage:
    python scripts/status.py               # every podcast, one line each
    python scripts/status.py <podcast_id>  # per-episode detail with cuts
    python scripts/status.py -e <episode_id>  # every cut with its transcript
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.database import init_db, get_session
from app.models import Podcast, Episode, RemovedSegment
from processor.rss_generator import generate_feed_url


def ts(s):
    s = int(s or 0)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def cuts(session, ep):
    return session.query(RemovedSegment).filter_by(episode_id=ep.id) \
        .order_by(RemovedSegment.start_time).all()


def main():
    init_db()
    session = get_session()
    args = sys.argv[1:]

    if args[:1] == ['-e']:
        ep = session.get(Episode, int(args[1]))
        print(f"[{ep.id}] {ep.title}  processed={ep.processed}")
        for c in cuts(session, ep):
            print(f"\n  {ts(c.start_time)}–{ts(c.end_time)} ({c.end_time - c.start_time:.0f}s)"
                  f"  {c.method}  conf={c.confidence}  n={c.n_matches}")
            if c.transcript_excerpt:
                print(f"    \"{c.transcript_excerpt.strip()}\"")
        return

    if args:
        p = session.get(Podcast, int(args[0]))
        print(f"[{p.id}] {p.title}  status={p.processing_status}\n  {generate_feed_url(p.id)}")
        for ep in session.query(Episode).filter_by(podcast_id=p.id) \
                .order_by(Episode.pub_date.desc()).all():
            state = 'done' if ep.processed else ('downloaded' if ep.original_audio_path else 'pending')
            cs = cuts(session, ep)
            total = sum(c.end_time - c.start_time for c in cs)
            print(f"  [{ep.id}] {ep.pub_date:%Y-%m-%d} {state:10} "
                  f"{len(cs):2} cuts {ts(total):>6}  {ep.title}")
        return

    for p in session.query(Podcast).order_by(Podcast.id).all():
        n = session.query(Episode).filter_by(podcast_id=p.id).count()
        done = session.query(Episode).filter_by(podcast_id=p.id, processed=True).count()
        print(f"[{p.id}] {p.title} — {done}/{n} processed, {p.processing_status}"
              f"\n  {generate_feed_url(p.id)}")


if __name__ == '__main__':
    main()
