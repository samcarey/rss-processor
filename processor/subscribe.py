"""Add a podcast by feed URL (shared by scripts/add_feed.py and the legacy UI)."""
from datetime import datetime, timedelta

from app.models import Podcast, Episode
from processor.rss_parser import parse_feed
from processor.versions import excluded


def add_podcast(session, feed_url, backfill_days):
    """Insert the podcast + its in-window episodes. Returns (podcast, created).

    Idempotent: for an existing podcast (same feed_url) only in-window
    episodes it doesn't have yet are inserted -- so re-adding with a larger
    backfill_days widens the window (more episodes = more audio-repeat
    evidence for a new show). Downloading/processing stays the worker's job.
    """
    existing = session.query(Podcast).filter_by(feed_url=feed_url).first()
    feed_data = parse_feed(feed_url)
    if existing:
        _insert_episodes(session, existing, feed_data, backfill_days)
        return existing, False

    podcast = Podcast(
        feed_url=feed_url,
        title=feed_data['podcast']['title'],
        description=feed_data['podcast']['description'],
        image_url=feed_data['podcast']['image_url'],
        author=feed_data['podcast']['author'],
    )
    session.add(podcast)
    session.commit()

    _insert_episodes(session, podcast, feed_data, backfill_days)

    from processor.versions import register
    register(feed_url, podcast.title)
    return podcast, True


def _insert_episodes(session, podcast, feed_data, backfill_days):
    # backfill_days is a hard window (see CLAUDE.md behavior contracts)
    cutoff = datetime.utcnow() - timedelta(days=backfill_days)
    have = {g for (g,) in session.query(Episode.guid).filter_by(podcast_id=podcast.id)}
    for ep in feed_data['episodes']:
        if ep.get('pub_date') and ep['pub_date'] < cutoff:
            continue
        if ep['guid'] in have or excluded(podcast.feed_url, ep['title']):
            continue
        session.add(Episode(
            podcast_id=podcast.id,
            guid=ep['guid'],
            title=ep['title'],
            description=ep['description'],
            pub_date=ep['pub_date'],
            original_audio_url=ep['audio_url'],
            duration_seconds=ep.get('duration'),
        ))
    session.commit()


def remove_podcast(session, config, podcast):
    """Delete a podcast (ORM cascade to episodes/segments) and all its files:
    original + processed audio, cut clips, cached fingerprints/transcripts.
    Returns the number of files removed."""
    import logging
    import os
    from processor.matching import fingerprint_path
    from processor.rss_generator import resolve_storage_path
    from processor.transcripts import transcript_path

    files = []
    for ep in podcast.episodes:
        files.extend(p for p in (ep.original_audio_path, ep.processed_audio_path) if p)
        files.extend(s.segment_audio_path for s in ep.removed_segments if s.segment_audio_path)
        files.append(fingerprint_path(config, ep.id))
        files.append(transcript_path(config, ep.id))

    session.delete(podcast)
    session.commit()

    removed = 0
    for path in files:
        path = resolve_storage_path(path)
        try:
            if os.path.exists(path):
                os.remove(path)
                removed += 1
        except OSError as e:
            logging.getLogger(__name__).warning(f"Could not remove {path}: {e}")
    return removed
