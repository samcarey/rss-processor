import feedparser
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def parse_feed(url):
    """
    Parse RSS feed and return podcast metadata and episodes.

    Args:
        url: RSS feed URL

    Returns:
        dict with keys:
            - podcast: dict with feed metadata (title, description, image_url, author)
            - episodes: list of dicts with episode data (guid, title, description, pub_date, audio_url)

    Raises:
        Exception: If feed parsing fails
    """
    try:
        logger.info(f"Parsing feed: {url}")
        feed = feedparser.parse(url)

        if feed.bozo:
            logger.warning(f"Feed has errors: {feed.bozo_exception}")

        if not feed.entries:
            raise Exception("Feed has no entries")

        # Extract podcast metadata
        podcast = {
            'title': feed.feed.get('title', 'Unknown Podcast'),
            'description': feed.feed.get('description', ''),
            'image_url': _extract_image_url(feed.feed),
            'author': feed.feed.get('author', feed.feed.get('itunes_author', ''))
        }

        # Extract episodes
        episodes = []
        for entry in feed.entries:
            episode = _parse_entry(entry)
            if episode:
                episodes.append(episode)

        logger.info(f"Parsed {len(episodes)} episodes from {podcast['title']}")
        return {
            'podcast': podcast,
            'episodes': episodes
        }

    except Exception as e:
        logger.error(f"Failed to parse feed {url}: {e}")
        raise


def _extract_image_url(feed_data):
    """Extract image URL from feed data"""
    # Try different possible image locations
    if hasattr(feed_data, 'image') and 'href' in feed_data.image:
        return feed_data.image.href
    elif 'itunes_image' in feed_data:
        if isinstance(feed_data.itunes_image, dict):
            return feed_data.itunes_image.get('href', '')
        return feed_data.itunes_image
    return ''


def _parse_entry(entry):
    """Parse a single feed entry into episode data"""
    try:
        # Find audio enclosure
        audio_url = None
        duration = None

        if hasattr(entry, 'enclosures'):
            for enclosure in entry.enclosures:
                if enclosure.get('type', '').startswith('audio/'):
                    audio_url = enclosure.get('href', enclosure.get('url'))
                    break

        # Some feeds use 'links' instead
        if not audio_url and hasattr(entry, 'links'):
            for link in entry.links:
                if link.get('type', '').startswith('audio/'):
                    audio_url = link.get('href')
                    break

        if not audio_url:
            logger.warning(f"No audio URL found for entry: {entry.get('title', 'Unknown')}")
            return None

        # Parse publication date
        pub_date = None
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            pub_date = datetime(*entry.published_parsed[:6])
        elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
            pub_date = datetime(*entry.updated_parsed[:6])

        # Extract duration if available
        if hasattr(entry, 'itunes_duration'):
            duration = _parse_duration(entry.itunes_duration)

        return {
            'guid': entry.get('id', entry.get('link', audio_url)),
            'title': entry.get('title', 'Unknown Episode'),
            'description': entry.get('description', entry.get('summary', '')),
            'pub_date': pub_date,
            'audio_url': audio_url,
            'duration': duration
        }

    except Exception as e:
        logger.error(f"Failed to parse entry: {e}")
        return None


def _parse_duration(duration_str):
    """Parse duration string to seconds"""
    try:
        if isinstance(duration_str, int):
            return float(duration_str)

        # Handle HH:MM:SS or MM:SS format
        parts = str(duration_str).split(':')
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 1:
            return int(parts[0])
    except:
        pass
    return None
