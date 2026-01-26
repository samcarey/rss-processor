from pydub import AudioSegment
import os
import logging
from app.database import get_session
from app.models import AudioFingerprint, RemovedSegment, Episode
from processor.audio_fingerprint import generate_fingerprints, find_matching_segments

logger = logging.getLogger(__name__)


def process_episode(episode, config, podcast=None):
    """
    Process an episode: generate fingerprints, detect duplicates, and remove segments.

    Args:
        episode: Episode model instance
        config: Application configuration dict
        podcast: Optional Podcast model instance for progress tracking

    Returns:
        dict: Processing results with keys:
            - processed_path: Path to processed audio file
            - segments_removed: List of removed segment dicts
            - duration_saved: Total seconds removed

    Raises:
        Exception: If processing fails
    """
    try:
        logger.info(f"Processing episode: {episode.title}")

        if not episode.original_audio_path or not os.path.exists(episode.original_audio_path):
            raise Exception("Original audio file not found")

        session = get_session()

        # Step 1: Generate fingerprints for this episode (0-40%)
        if podcast:
            podcast.processing_progress = 10
            session.commit()
        logger.info("Generating fingerprints...")
        window_seconds = config['audio']['fingerprint_window_seconds']
        fingerprints = generate_fingerprints(
            episode.original_audio_path,
            window_seconds=window_seconds
        )
        if podcast:
            podcast.processing_progress = 40
            session.commit()

        # Step 2: Find duplicate segments by comparing with previous episodes (40-60%)
        logger.info("Searching for duplicate segments...")
        duplicate_segments = find_duplicate_segments(episode, fingerprints, config, session)
        if podcast:
            podcast.processing_progress = 60
            session.commit()

        # Step 3: Remove duplicate segments if any found (60-80%)
        if duplicate_segments:
            logger.info(f"Found {len(duplicate_segments)} duplicate segments to remove")
            processed_path, removed_segments = remove_segments(
                episode,
                duplicate_segments,
                config,
                session
            )
        else:
            logger.info("No duplicate segments found, copying original")
            # No duplicates, just copy the original file
            processed_path = _copy_original_to_processed(episode, config)
            removed_segments = []
        if podcast:
            podcast.processing_progress = 80
            session.commit()

        # Step 4: Store fingerprints for future comparisons (80-100%)
        logger.info("Storing fingerprints...")
        _store_fingerprints(episode, fingerprints, session)
        if podcast:
            podcast.processing_progress = 100
            session.commit()

        # Calculate total time saved
        duration_saved = sum(seg['duration'] for seg in removed_segments)

        logger.info(f"Processing complete. Removed {duration_saved:.1f} seconds")

        return {
            'processed_path': processed_path,
            'segments_removed': removed_segments,
            'duration_saved': duration_saved
        }

    except Exception as e:
        logger.error(f"Failed to process episode {episode.id}: {e}")
        raise


def find_duplicate_segments(episode, fingerprints, config, session):
    """
    Find duplicate segments by comparing fingerprints with previous episodes.

    Args:
        episode: Episode model instance
        fingerprints: List of fingerprint dicts for this episode
        config: Application configuration
        session: Database session

    Returns:
        list: List of duplicate segment dicts with keys:
            - start_time, end_time, matched_episode_id, matched_start_time
    """
    min_duration = config['audio']['min_duplicate_duration_seconds']

    # Get all fingerprints from earlier episodes in the same podcast
    existing_fingerprints = session.query(AudioFingerprint).join(
        AudioFingerprint.episode
    ).filter(
        AudioFingerprint.episode.has(podcast_id=episode.podcast_id),
        Episode.pub_date < episode.pub_date
    ).all()

    if not existing_fingerprints:
        logger.info("No previous episodes to compare against")
        return []

    # Convert to format expected by matching function
    existing_fp_data = [
        (fp.episode_id, {
            'start_time': fp.start_time,
            'end_time': fp.end_time,
            'fingerprint': fp.fingerprint
        })
        for fp in existing_fingerprints
    ]

    # Find matches
    matches = find_matching_segments(fingerprints, existing_fp_data)

    # Filter out segments shorter than minimum duration
    duplicate_segments = [
        match for match in matches
        if (match['end_time'] - match['start_time']) >= min_duration
    ]

    logger.info(f"Found {len(duplicate_segments)} segments >= {min_duration}s")

    return duplicate_segments


def remove_segments(episode, segments_to_remove, config, session):
    """
    Remove duplicate segments from audio and export processed file.

    Args:
        episode: Episode model instance
        segments_to_remove: List of segment dicts to remove
        config: Application configuration
        session: Database session

    Returns:
        tuple: (processed_file_path, list of removed segment dicts)
    """
    try:
        # Load original audio
        audio = AudioSegment.from_file(episode.original_audio_path)

        # Sort segments by start time (reverse order for removal)
        sorted_segments = sorted(segments_to_remove, key=lambda x: x['start_time'], reverse=True)

        removed_segments = []

        # Remove each segment and save it
        for segment in sorted_segments:
            start_ms = int(segment['start_time'] * 1000)
            end_ms = int(segment['end_time'] * 1000)

            logger.info(f"Removing segment {segment['start_time']:.1f}s - {segment['end_time']:.1f}s")

            # Extract the segment to save it
            removed_audio = audio[start_ms:end_ms]

            # Save removed segment
            segment_filename = f"episode_{episode.id}_segment_{segment['start_time']:.0f}_{segment['end_time']:.0f}.mp3"
            segment_path = os.path.join(config['storage']['segments'], segment_filename)
            removed_audio.export(segment_path, format='mp3')

            # Remove from main audio
            audio = audio[:start_ms] + audio[end_ms:]

            # Record removed segment in database
            removed_seg = RemovedSegment(
                episode_id=episode.id,
                start_time=segment['start_time'],
                end_time=segment['end_time'],
                duration=segment['end_time'] - segment['start_time'],
                matched_episode_id=segment['matched_episode_id'],
                matched_start_time=segment.get('matched_start_time'),
                segment_audio_path=segment_path
            )
            session.add(removed_seg)

            removed_segments.append({
                'start_time': segment['start_time'],
                'end_time': segment['end_time'],
                'duration': segment['end_time'] - segment['start_time'],
                'segment_path': segment_path
            })

        # Export processed audio
        processed_filename = f"episode_{episode.id}_processed.mp3"
        processed_path = os.path.join(config['storage']['processed'], processed_filename)
        audio.export(processed_path, format='mp3', bitrate='128k')

        logger.info(f"Exported processed audio to: {processed_path}")

        session.commit()

        return processed_path, removed_segments

    except Exception as e:
        session.rollback()
        logger.error(f"Failed to remove segments: {e}")
        raise


def _copy_original_to_processed(episode, config):
    """Copy original file to processed directory when no segments are removed"""
    import shutil

    processed_filename = f"episode_{episode.id}_processed.mp3"
    processed_path = os.path.join(config['storage']['processed'], processed_filename)

    # If original is already MP3, just copy it
    if episode.original_audio_path.endswith('.mp3'):
        shutil.copy2(episode.original_audio_path, processed_path)
    else:
        # Convert to MP3
        audio = AudioSegment.from_file(episode.original_audio_path)
        audio.export(processed_path, format='mp3', bitrate='128k')

    return processed_path


def _store_fingerprints(episode, fingerprints, session):
    """Store fingerprints in database for future comparisons"""
    for fp in fingerprints:
        fingerprint_record = AudioFingerprint(
            episode_id=episode.id,
            start_time=fp['start_time'],
            end_time=fp['end_time'],
            fingerprint=fp['fingerprint']
        )
        session.add(fingerprint_record)

    session.commit()
    logger.info(f"Stored {len(fingerprints)} fingerprints")
