"""Reprocess episodes with the current ad-detection pipeline.

Recomputes cuts from cached fingerprints/transcripts (creating them if
missing), replaces the processed audio, removed segments, and their files.
Run after detection improvements, or periodically so early episodes benefit
from repeat evidence that only arrived with later episodes.

Usage:
    python scripts/reprocess.py            # all downloaded episodes
    python scripts/reprocess.py 1423 1430  # specific episode ids
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from app.database import init_db, get_session
from app.models import Episode, RemovedSegment
from processor.audio_processor import process_episode
from processor.rss_generator import resolve_storage_path


def main():
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'config.yaml')) as f:
        config = yaml.safe_load(f)
    init_db()
    session = get_session()

    only = {int(a) for a in sys.argv[1:]} if len(sys.argv) > 1 else None
    q = session.query(Episode).filter(Episode.original_audio_path.isnot(None))
    episodes = [e for e in q.order_by(Episode.pub_date).all()
                if not only or e.id in only]
    print(f"Reprocessing {len(episodes)} episodes")

    for ep in episodes:
        audio = resolve_storage_path(ep.original_audio_path)
        if not os.path.exists(audio):
            print(f"  ep {ep.id}: original audio missing, skipping")
            continue
        # wipe previous cuts + files
        old = session.query(RemovedSegment).filter_by(episode_id=ep.id).all()
        for seg in old:
            if seg.segment_audio_path:
                p = resolve_storage_path(seg.segment_audio_path)
                if os.path.exists(p):
                    os.remove(p)
            session.delete(seg)
        session.commit()

        print(f"== ep {ep.id}: {ep.title[:60]}")
        try:
            result = process_episode(ep, config)
        except Exception as e:
            print(f"  FAILED: {e}")
            session.rollback()
            continue
        ep.processed_audio_path = result['processed_path']
        ep.processed = True
        session.commit()
        print(f"  removed {result['duration_saved']:.0f}s in "
              f"{len(result['segments_removed'])} segments")

    print("done")


if __name__ == "__main__":
    main()
