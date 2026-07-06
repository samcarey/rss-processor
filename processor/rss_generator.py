from feedgen.feed import FeedGenerator
from datetime import datetime, timezone
import logging
import yaml
import os

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_storage_path(path):
    """Resolve a config-relative path (./data/...) against the project root,
    so it works regardless of the process's working directory."""
    if not os.path.isabs(path):
        return os.path.join(PROJECT_ROOT, path)
    return path


def load_config():
    """Load configuration"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def generate_feed(podcast, episodes):
    """
    Generate RSS feed XML for a podcast with processed episodes.

    Args:
        podcast: Podcast model instance
        episodes: List of Episode model instances (should be processed episodes only)

    Returns:
        str: RSS feed XML
    """
    try:
        config = load_config()
        base_url = config['web']['base_url']

        logger.info(f"Generating RSS feed for podcast: {podcast.title}")

        # Create feed generator
        fg = FeedGenerator()
        fg.title(podcast.title or 'Unknown Podcast')
        fg.description(podcast.description or '')
        fg.link(href=generate_feed_url(podcast.id), rel='self')
        fg.link(href=podcast.feed_url, rel='alternate')
        fg.language('en')

        if podcast.author:
            fg.author({'name': podcast.author})

        if podcast.image_url:
            fg.image(podcast.image_url)

        # Add podcast-specific elements
        fg.load_extension('podcast')
        if podcast.author:
            fg.podcast.itunes_author(podcast.author)
        if podcast.image_url:
            fg.podcast.itunes_image(podcast.image_url)

        # Add episodes
        for episode in episodes:
            # Only include processed episodes with valid audio files
            if not episode.processed or not episode.processed_audio_path:
                continue

            audio_path = resolve_storage_path(episode.processed_audio_path)
            if not os.path.exists(audio_path):
                logger.warning(f"Processed audio file not found: {audio_path}")
                continue

            fe = fg.add_entry()
            fe.id(episode.guid)
            fe.title(episode.title or 'Unknown Episode')
            fe.description(episode.description or '')

            if episode.pub_date:
                # feedgen requires timezone-aware datetimes; pub_dates are stored naive UTC
                pub_date = episode.pub_date
                if pub_date.tzinfo is None:
                    pub_date = pub_date.replace(tzinfo=timezone.utc)
                fe.published(pub_date)

            # Generate URL for processed audio
            audio_url = f"{base_url}/audio/{episode.id}"
            file_size = os.path.getsize(audio_path)

            fe.enclosure(audio_url, str(file_size), 'audio/mpeg')

            # Add iTunes-specific episode data
            if episode.duration_seconds:
                fe.podcast.itunes_duration(int(episode.duration_seconds))

        rss_feed = fg.rss_str(pretty=True)
        logger.info(f"Generated RSS feed with {len(episodes)} episodes")

        return rss_feed

    except Exception as e:
        logger.error(f"Failed to generate RSS feed for podcast {podcast.id}: {e}")
        raise


def generate_feed_url(podcast_id):
    """Generate the URL for a podcast's RSS feed"""
    config = load_config()
    base_url = config['web']['base_url']
    return f"{base_url}/feed/{podcast_id}.xml"
