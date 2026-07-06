"""Episode processing: fingerprint, transcribe, detect ads, cut, export.

Detection strategy lives in processor/ad_detector.py. This module wires it to
the database and files: it caches the raw fingerprint and transcript for the
episode, gathers the podcast's other cached fingerprints as evidence, removes
the detected segments, and records provenance on each RemovedSegment.
"""
from pydub import AudioSegment
import logging
import os

from app.database import get_session
from app.models import RemovedSegment, Episode
from processor.ad_detector import detect_ads
from processor.matching import raw_fingerprint, fingerprint_path, FP_RATE
from processor.transcripts import transcribe, transcript_path, load_segments
from processor.rss_generator import resolve_storage_path

logger = logging.getLogger(__name__)


def process_episode(episode, config, podcast=None):
    """Process an episode end-to-end. Returns dict with processed_path,
    segments_removed, duration_saved."""
    try:
        logger.info(f"Processing episode: {episode.title}")

        audio_path = resolve_storage_path(episode.original_audio_path or "")
        if not episode.original_audio_path or not os.path.exists(audio_path):
            raise Exception("Original audio file not found")

        session = get_session()

        def progress(pct):
            if podcast:
                podcast.processing_progress = pct
                session.commit()

        # Step 1: raw fingerprint (cached)
        progress(5)
        fp = raw_fingerprint(audio_path, fingerprint_path(config, episode.id))

        # Step 2: transcript (cached); transcription failure is not fatal —
        # detection degrades gracefully without text evidence.
        progress(15)
        tx_path = transcript_path(config, episode.id)
        if not os.path.exists(tx_path):
            try:
                transcribe(audio_path, tx_path, config)
            except Exception as e:
                logger.error(f"Transcription failed for episode {episode.id}: {e}")
        tx = load_segments(tx_path)
        progress(50)

        # Step 3: gather evidence — other episodes of this podcast that
        # already have cached fingerprints.
        others = []
        import numpy as np
        rows = session.query(Episode.id).filter(
            Episode.podcast_id == episode.podcast_id,
            Episode.id != episode.id,
        ).all()
        for (other_id,) in rows:
            fpp = fingerprint_path(config, other_id)
            if os.path.exists(fpp):
                others.append({
                    "id": other_id,
                    "fp": np.load(fpp),
                    "tx": load_segments(transcript_path(config, other_id)),
                })
        logger.info(f"Matching against {len(others)} episodes with fingerprints")

        # Step 4: detect
        target = {
            "id": episode.id, "fp": fp, "tx": tx,
            "file_duration": len(fp) / FP_RATE + 2.0,
            "feed_duration": episode.duration_seconds,
        }
        cuts, report = detect_ads(target, others, config)
        logger.info(f"Detected {len(cuts)} ad regions, {report['total_cut']:.0f}s "
                    f"(feed-duration budget: {report['expected_from_feed']}s)")
        progress(70)

        # Step 5: cut and export
        if cuts:
            processed_path, removed_segments = remove_segments(episode, cuts, config, session)
        else:
            processed_path = _copy_original_to_processed(episode, config)
            removed_segments = []
        progress(100)

        duration_saved = sum(seg['duration'] for seg in removed_segments)
        logger.info(f"Processing complete. Removed {duration_saved:.1f} seconds")
        return {
            'processed_path': processed_path,
            'segments_removed': removed_segments,
            'duration_saved': duration_saved,
        }

    except Exception as e:
        logger.error(f"Failed to process episode {episode.id}: {e}")
        raise


def remove_segments(episode, cuts, config, session):
    """Cut segments from the audio; save each removed span and provenance."""
    try:
        audio_path = resolve_storage_path(episode.original_audio_path)
        audio = AudioSegment.from_file(audio_path)

        seg_dir = resolve_storage_path(config['storage']['segments'])
        os.makedirs(seg_dir, exist_ok=True)

        removed_segments = []
        # remove back-to-front so earlier offsets stay valid
        for cut in sorted(cuts, key=lambda c: c['start'], reverse=True):
            start_ms = int(cut['start'] * 1000)
            end_ms = int(cut['end'] * 1000)
            logger.info(f"Removing {cut['start']:.1f}s - {cut['end']:.1f}s [{cut['method']}]")

            removed_audio = audio[start_ms:end_ms]
            segment_filename = f"episode_{episode.id}_segment_{cut['start']:.0f}_{cut['end']:.0f}.mp3"
            segment_path = os.path.join(seg_dir, segment_filename)
            removed_audio.export(segment_path, format='mp3')

            audio = audio[:start_ms] + audio[end_ms:]

            session.add(RemovedSegment(
                episode_id=episode.id,
                start_time=cut['start'],
                end_time=cut['end'],
                duration=cut['end'] - cut['start'],
                segment_audio_path=os.path.join(config['storage']['segments'], segment_filename),
                method=cut.get('method'),
                confidence=cut.get('confidence'),
                n_matches=cut.get('n_matches'),
                transcript_excerpt=cut.get('excerpt'),
            ))
            removed_segments.append({
                'start_time': cut['start'],
                'end_time': cut['end'],
                'duration': cut['end'] - cut['start'],
                'segment_path': segment_path,
            })

        processed_dir = resolve_storage_path(config['storage']['processed'])
        os.makedirs(processed_dir, exist_ok=True)
        processed_filename = f"episode_{episode.id}_processed.mp3"
        processed_path = os.path.join(processed_dir, processed_filename)
        audio.export(processed_path, format='mp3', bitrate='128k')
        logger.info(f"Exported processed audio to: {processed_path}")

        session.commit()
        return os.path.join(config['storage']['processed'], processed_filename), removed_segments

    except Exception as e:
        session.rollback()
        logger.error(f"Failed to remove segments: {e}")
        raise


def _copy_original_to_processed(episode, config):
    """Copy original file to processed directory when nothing is removed."""
    import shutil

    processed_dir = resolve_storage_path(config['storage']['processed'])
    os.makedirs(processed_dir, exist_ok=True)
    processed_filename = f"episode_{episode.id}_processed.mp3"
    processed_path = os.path.join(processed_dir, processed_filename)

    audio_path = resolve_storage_path(episode.original_audio_path)
    if audio_path.endswith('.mp3'):
        shutil.copy2(audio_path, processed_path)
    else:
        audio = AudioSegment.from_file(audio_path)
        audio.export(processed_path, format='mp3', bitrate='128k')

    return os.path.join(config['storage']['processed'], processed_filename)
