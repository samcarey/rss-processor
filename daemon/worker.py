import time
import logging
import signal
import sys
from datetime import datetime, timedelta
from app.database import init_db, get_session
from app.models import Podcast, Episode
from processor.rss_parser import parse_feed
from processor.downloader import download_episode
from processor.audio_processor import process_episode
import yaml
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('worker.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Global flag for graceful shutdown
shutdown_requested = False


def signal_handler(sig, frame):
    """Handle shutdown signals"""
    global shutdown_requested
    logger.info("Shutdown signal received, finishing current task...")
    shutdown_requested = True


def load_config():
    """Load configuration from config.yaml"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def check_podcast(podcast, config, session):
    """
    Check a podcast for new episodes and process them.

    Args:
        podcast: Podcast model instance
        config: Application configuration
        session: Database session
    """
    try:
        logger.info(f"Checking podcast: {podcast.title}")

        # Parse the RSS feed
        feed_data = parse_feed(podcast.feed_url)

        # Update podcast metadata
        podcast.title = feed_data['podcast']['title']
        podcast.description = feed_data['podcast']['description']
        podcast.image_url = feed_data['podcast']['image_url']
        podcast.author = feed_data['podcast']['author']
        podcast.last_checked_at = datetime.utcnow()

        # Determine backfill cutoff if this is a new podcast
        backfill_cutoff = None
        if not podcast.last_checked_at or len(podcast.episodes) == 0:
            backfill_days = config['worker']['backfill_days']
            backfill_cutoff = datetime.utcnow() - timedelta(days=backfill_days)
            logger.info(f"New podcast, backfilling {backfill_days} days")

        # Process episodes from the feed
        new_episodes = []
        for episode_data in feed_data['episodes']:
            # Check if episode already exists
            existing = session.query(Episode).filter_by(
                podcast_id=podcast.id,
                guid=episode_data['guid']
            ).first()

            if existing:
                continue

            # For new podcasts, only add recent episodes
            if backfill_cutoff and episode_data.get('pub_date'):
                if episode_data['pub_date'] < backfill_cutoff:
                    continue

            # Create new episode
            episode = Episode(
                podcast_id=podcast.id,
                guid=episode_data['guid'],
                title=episode_data['title'],
                description=episode_data['description'],
                pub_date=episode_data['pub_date'],
                original_audio_url=episode_data['audio_url'],
                duration_seconds=episode_data.get('duration')
            )
            session.add(episode)
            new_episodes.append(episode)

        session.commit()

        if new_episodes:
            logger.info(f"Found {len(new_episodes)} new episodes")

            # Sort by pub_date (oldest first) so we can detect duplicates correctly
            new_episodes.sort(key=lambda e: e.pub_date or datetime.min)

            # Set processing status
            podcast.processing_status = 'downloading'
            podcast.total_episodes = len(new_episodes)
            podcast.episodes_processed = 0
            podcast.processing_started_at = datetime.utcnow()
            session.commit()

            # Download and process new episodes
            for idx, episode in enumerate(new_episodes):
                if shutdown_requested:
                    logger.info("Shutdown requested, stopping episode processing")
                    podcast.processing_status = 'idle'
                    session.commit()
                    break

                try:
                    # Update progress
                    podcast.current_episode_title = episode.title
                    podcast.processing_status = 'processing'
                    podcast.processing_progress = 0
                    session.commit()

                    download_and_process_episode(episode, config, session, podcast=podcast)

                    # Update progress
                    podcast.episodes_processed = idx + 1
                    podcast.processing_progress = 0
                    session.commit()

                except Exception as e:
                    logger.error(f"Failed to process episode {episode.id}: {e}")
                    continue

            # Mark complete
            podcast.processing_status = 'idle'
            podcast.current_episode_title = None
            session.commit()

        else:
            logger.info("No new episodes found")

    except Exception as e:
        logger.error(f"Failed to check podcast {podcast.id}: {e}")
        session.rollback()


def download_and_process_episode(episode, config, session, podcast=None):
    """
    Download and process a single episode.

    Args:
        episode: Episode model instance
        config: Application configuration
        session: Database session
        podcast: Optional Podcast model instance for progress tracking
    """
    try:
        logger.info(f"Processing episode: {episode.title}")

        # Download the episode
        logger.info("Downloading audio...")
        audio_path = download_episode(episode, config['storage']['original'])

        if not audio_path:
            raise Exception("Download failed")

        episode.original_audio_path = audio_path
        session.commit()

        # Process the episode (fingerprint and remove duplicates)
        logger.info("Processing audio...")
        result = process_episode(episode, config, podcast=podcast)

        episode.processed_audio_path = result['processed_path']
        episode.processed = True
        session.commit()

        logger.info(f"Episode processed successfully. Saved {result['duration_saved']:.1f}s")

    except Exception as e:
        logger.error(f"Failed to download/process episode {episode.id}: {e}")
        session.rollback()
        raise


def download_pending_episodes(config, session):
    """
    Download episodes that are in the database but don't have audio files yet.

    Args:
        config: Application configuration
        session: Database session
    """
    from datetime import timedelta

    # Find episodes without audio that are within backfill window
    backfill_cutoff = datetime.utcnow() - timedelta(days=config['worker']['backfill_days'])

    pending = session.query(Episode).filter(
        Episode.original_audio_path.is_(None),
        Episode.pub_date >= backfill_cutoff
    ).order_by(Episode.pub_date).all()

    if pending:
        logger.info(f"Found {len(pending)} episodes to download")

        # Group by podcast
        from collections import defaultdict
        episodes_by_podcast = defaultdict(list)
        for ep in pending:
            episodes_by_podcast[ep.podcast_id].append(ep)

        # Download each podcast's episodes
        for podcast_id, episodes in episodes_by_podcast.items():
            podcast = session.query(Podcast).get(podcast_id)

            # Set downloading status
            podcast.processing_status = 'downloading'
            podcast.total_episodes = len(episodes)
            podcast.episodes_processed = 0
            podcast.current_episode_title = None
            podcast.processing_started_at = datetime.utcnow()
            session.commit()

            for idx, episode in enumerate(episodes):
                if shutdown_requested:
                    podcast.processing_status = 'idle'
                    session.commit()
                    break

                try:
                    logger.info(f"Downloading: {episode.title}")
                    podcast.current_episode_title = episode.title
                    session.commit()

                    # Download the episode
                    audio_path = download_episode(episode, config['storage']['original'])
                    episode.original_audio_path = audio_path

                    podcast.episodes_processed = idx + 1
                    session.commit()

                    logger.info(f"Downloaded successfully")

                except Exception as e:
                    logger.error(f"Failed to download episode {episode.id}: {e}")
                    session.rollback()
                    continue

            # Reset status
            podcast.processing_status = 'idle'
            podcast.current_episode_title = None
            session.commit()


def process_unprocessed_episodes(config, session):
    """
    Process any episodes that were downloaded but not yet processed.

    Args:
        config: Application configuration
        session: Database session
    """
    # Find episodes that have audio files but aren't processed
    unprocessed = session.query(Episode).filter(
        Episode.original_audio_path.isnot(None),
        Episode.processed == False
    ).order_by(Episode.pub_date).all()

    if unprocessed:
        logger.info(f"Found {len(unprocessed)} unprocessed episodes")

        # Group by podcast for progress tracking
        from collections import defaultdict
        episodes_by_podcast = defaultdict(list)
        for ep in unprocessed:
            episodes_by_podcast[ep.podcast_id].append(ep)

        # Process each podcast's episodes
        for podcast_id, episodes in episodes_by_podcast.items():
            podcast = session.query(Podcast).get(podcast_id)

            # Set processing status
            podcast.processing_status = 'processing'
            podcast.total_episodes = len(episodes)
            podcast.episodes_processed = 0
            podcast.processing_started_at = datetime.utcnow()
            session.commit()

            for idx, episode in enumerate(episodes):
                if shutdown_requested:
                    podcast.processing_status = 'idle'
                    session.commit()
                    break

                try:
                    # Update progress
                    podcast.current_episode_title = episode.title
                    podcast.processing_progress = 0
                    session.commit()

                    logger.info(f"Processing previously downloaded episode: {episode.title}")
                    result = process_episode(episode, config, podcast=podcast)

                    episode.processed_audio_path = result['processed_path']
                    episode.processed = True

                    # Update progress
                    podcast.episodes_processed = idx + 1
                    podcast.processing_progress = 0
                    session.commit()

                    logger.info(f"Episode processed. Saved {result['duration_saved']:.1f}s")

                except Exception as e:
                    logger.error(f"Failed to process episode {episode.id}: {e}")
                    session.rollback()
                    continue

            # Mark complete for this podcast
            podcast.processing_status = 'idle'
            podcast.current_episode_title = None
            session.commit()


def check_all_podcasts_once(config, session):
    """
    Run one check cycle for all podcasts.
    This can be called from the web interface to trigger a check immediately.
    """
    logger.info("Running one-time podcast check (triggered manually)")

    # First, download any pending episodes
    download_pending_episodes(config, session)

    # Then, process any unprocessed episodes (that have audio)
    process_unprocessed_episodes(config, session)

    # Check all podcasts for new episodes
    podcasts = session.query(Podcast).all()
    logger.info(f"Checking {len(podcasts)} podcasts for new episodes")

    for podcast in podcasts:
        try:
            check_podcast(podcast, config, session)
        except Exception as e:
            logger.error(f"Error checking podcast {podcast.id}: {e}")
            continue

    logger.info("One-time check complete")


def run_worker():
    """Main worker loop"""
    global shutdown_requested

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("RSS Processor Worker starting...")

    # Initialize database
    init_db()
    config = load_config()

    check_interval = config['worker']['check_interval_seconds']
    logger.info(f"Worker configured with {check_interval}s check interval")

    while not shutdown_requested:
        try:
            session = get_session()

            # First, download any pending episodes
            download_pending_episodes(config, session)

            if shutdown_requested:
                break

            # Then, process any unprocessed episodes (that have audio)
            process_unprocessed_episodes(config, session)

            if shutdown_requested:
                break

            # Check all podcasts for new episodes
            podcasts = session.query(Podcast).all()
            logger.info(f"Checking {len(podcasts)} podcasts for new episodes")

            for podcast in podcasts:
                if shutdown_requested:
                    break

                try:
                    check_podcast(podcast, config, session)
                except Exception as e:
                    logger.error(f"Error checking podcast {podcast.id}: {e}")
                    continue

            # Wait before next check
            if not shutdown_requested:
                logger.info(f"Check complete. Sleeping for {check_interval} seconds")
                for _ in range(check_interval):
                    if shutdown_requested:
                        break
                    time.sleep(1)

        except Exception as e:
            logger.error(f"Worker error: {e}")
            if not shutdown_requested:
                logger.info("Sleeping 60 seconds before retry")
                time.sleep(60)

    logger.info("Worker shutting down")
    sys.exit(0)


if __name__ == '__main__':
    run_worker()
