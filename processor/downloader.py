import requests
import os
import logging
from pydub import AudioSegment
import hashlib

logger = logging.getLogger(__name__)


def download_episode(episode, storage_path):
    """
    Download an episode's audio file.

    Args:
        episode: Episode model instance
        storage_path: Base path for storing original audio files

    Returns:
        str: Path to downloaded file, or None if download failed

    Raises:
        Exception: If download fails
    """
    try:
        if not episode.original_audio_url:
            raise Exception("Episode has no audio URL")

        logger.info(f"Downloading episode: {episode.title}")

        # Generate filename from episode ID and URL
        file_ext = _get_file_extension(episode.original_audio_url)
        filename = f"episode_{episode.id}_{_hash_url(episode.original_audio_url)}{file_ext}"
        file_path = os.path.join(storage_path, filename)

        # Check if already downloaded
        if os.path.exists(file_path):
            logger.info(f"File already exists: {file_path}")
            return file_path

        # Download with progress tracking
        response = requests.get(episode.original_audio_url, stream=True, timeout=300)
        response.raise_for_status()

        total_size = int(response.headers.get('content-length', 0))
        downloaded_size = 0

        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded_size += len(chunk)

                    if total_size > 0:
                        progress = (downloaded_size / total_size) * 100
                        if downloaded_size % (1024 * 1024) == 0:  # Log every MB
                            logger.info(f"Download progress: {progress:.1f}%")

        logger.info(f"Downloaded: {file_path}")

        # Validate and convert to MP3 if necessary
        validated_path = _validate_audio(file_path)

        return validated_path

    except Exception as e:
        logger.error(f"Failed to download episode {episode.id}: {e}")
        # Clean up partial download
        if 'file_path' in locals() and os.path.exists(file_path):
            os.remove(file_path)
        raise


def _get_file_extension(url):
    """Extract file extension from URL"""
    # Try to get extension from URL
    path = url.split('?')[0]  # Remove query parameters
    if '.' in path:
        ext = os.path.splitext(path)[1].lower()
        if ext in ['.mp3', '.m4a', '.wav', '.ogg', '.aac']:
            return ext
    return '.mp3'  # Default to mp3


def _hash_url(url):
    """Generate a short hash of the URL for filename"""
    return hashlib.md5(url.encode()).hexdigest()[:8]


def _validate_audio(file_path):
    """
    Validate audio file and convert to MP3 if necessary.

    Args:
        file_path: Path to audio file

    Returns:
        str: Path to validated/converted audio file
    """
    try:
        # Try to load with pydub
        audio = AudioSegment.from_file(file_path)

        # If not MP3, convert it
        if not file_path.endswith('.mp3'):
            logger.info(f"Converting {file_path} to MP3")
            mp3_path = os.path.splitext(file_path)[0] + '.mp3'
            audio.export(mp3_path, format='mp3', bitrate='128k')

            # Remove original file
            os.remove(file_path)
            logger.info(f"Converted to MP3: {mp3_path}")
            return mp3_path

        return file_path

    except Exception as e:
        logger.error(f"Failed to validate audio file {file_path}: {e}")
        raise


def resume_download(file_path, url):
    """
    Resume a partial download (future enhancement).

    Args:
        file_path: Path to partial file
        url: Download URL

    Returns:
        bool: True if resumed successfully
    """
    # TODO: Implement resume functionality using Range headers
    # For now, we'll just re-download
    if os.path.exists(file_path):
        os.remove(file_path)
    return False
