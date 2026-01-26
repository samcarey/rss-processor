from pydub import AudioSegment
import logging
import os
import tempfile
import subprocess
import json

logger = logging.getLogger(__name__)


def generate_fingerprints(audio_path, window_seconds=10, overlap_seconds=5):
    """
    Generate audio fingerprints for sliding windows.

    Args:
        audio_path: Path to audio file
        window_seconds: Size of each fingerprint window (default 10s)
        overlap_seconds: Overlap between windows (default 5s)

    Returns:
        list: List of dicts with keys:
            - start_time: Start time in seconds
            - end_time: End time in seconds
            - fingerprint: Chromaprint fingerprint string

    Raises:
        Exception: If fingerprinting fails
    """
    try:
        logger.info(f"Generating fingerprints for: {audio_path}")

        # Load audio file
        audio = AudioSegment.from_file(audio_path)
        duration_seconds = len(audio) / 1000.0

        fingerprints = []
        current_time = 0
        step_seconds = window_seconds - overlap_seconds

        while current_time < duration_seconds:
            end_time = min(current_time + window_seconds, duration_seconds)

            # Skip if window is too short
            if end_time - current_time < window_seconds / 2:
                break

            # Extract segment
            start_ms = int(current_time * 1000)
            end_ms = int(end_time * 1000)
            segment = audio[start_ms:end_ms]

            # Generate fingerprint for this segment
            fingerprint = _fingerprint_segment(segment)

            if fingerprint:
                fingerprints.append({
                    'start_time': current_time,
                    'end_time': end_time,
                    'fingerprint': fingerprint
                })

            current_time += step_seconds

        logger.info(f"Generated {len(fingerprints)} fingerprints")
        return fingerprints

    except Exception as e:
        logger.error(f"Failed to generate fingerprints for {audio_path}: {e}")
        raise


def _fingerprint_segment(segment):
    """
    Generate chromaprint fingerprint for an audio segment.

    Args:
        segment: pydub AudioSegment

    Returns:
        str: Fingerprint string, or None if failed
    """
    tmp_path = None
    try:
        # Export segment to temporary WAV file for chromaprint
        # Chromaprint works best with raw PCM audio
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_file:
            tmp_path = tmp_file.name

        # Export as WAV with consistent format
        segment.set_frame_rate(22050).set_channels(1).export(
            tmp_path,
            format='wav',
            parameters=['-ar', '22050', '-ac', '1']
        )

        # Generate fingerprint using fpcalc command directly
        result = subprocess.run(
            ['fpcalc', '-json', tmp_path],
            capture_output=True,
            text=True,
            check=True
        )

        # Parse JSON output
        data = json.loads(result.stdout)
        fp = data.get('fingerprint')

        # Clean up temp file
        os.remove(tmp_path)

        return fp

    except subprocess.CalledProcessError as e:
        logger.error(f"fpcalc command failed: {e.stderr}")
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        return None
    except Exception as e:
        logger.error(f"Failed to fingerprint segment: {e}")
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        return None


def compare_fingerprints(fp1, fp2, threshold=0.8):
    """
    Compare two fingerprints and return similarity score.

    Optimized: First checks for exact match (fast), then does full comparison (slow).

    Args:
        fp1: First fingerprint string
        fp2: Second fingerprint string
        threshold: Similarity threshold (0-1)

    Returns:
        float: Similarity score (0-1), or 0 if comparison fails
    """
    try:
        # Fast path: exact match check
        if fp1 == fp2:
            return 1.0

        # Only do expensive comparison if threshold is below 1.0
        # For most podcast use cases, we want exact matches anyway
        if threshold >= 0.95:
            # Don't bother with expensive comparison for near-exact matches
            return 0.0

        import acoustid

        # acoustid.compare_fingerprints expects (duration, fingerprint_bytes) tuples
        # We use a dummy duration of 10 seconds since we're comparing segments
        fp1_bytes = fp1.encode('ascii') if isinstance(fp1, str) else fp1
        fp2_bytes = fp2.encode('ascii') if isinstance(fp2, str) else fp2

        similarity = acoustid.compare_fingerprints((10, fp1_bytes), (10, fp2_bytes))

        return similarity if similarity >= threshold else 0.0

    except Exception as e:
        logger.error(f"Failed to compare fingerprints: {e}")
        return 0.0


def find_matching_segments(target_fingerprints, existing_fingerprints, similarity_threshold=0.98):
    """
    Find matching segments between target and existing fingerprints.

    Args:
        target_fingerprints: List of fingerprint dicts for the new episode
        existing_fingerprints: List of (episode_id, fingerprint_dict) tuples from database
        similarity_threshold: Minimum similarity to consider a match

    Returns:
        list: List of matches with keys:
            - start_time: Start time in target episode
            - end_time: End time in target episode
            - matched_episode_id: ID of episode with matching segment
            - matched_start_time: Start time in matched episode
            - similarity: Similarity score
    """
    matches = []

    for target_fp in target_fingerprints:
        for existing_episode_id, existing_fp in existing_fingerprints:
            similarity = compare_fingerprints(
                target_fp['fingerprint'],
                existing_fp['fingerprint'],
                similarity_threshold
            )

            if similarity >= similarity_threshold:
                matches.append({
                    'start_time': target_fp['start_time'],
                    'end_time': target_fp['end_time'],
                    'matched_episode_id': existing_episode_id,
                    'matched_start_time': existing_fp['start_time'],
                    'similarity': similarity
                })

    # Merge consecutive matches into longer segments
    merged_matches = _merge_consecutive_matches(matches)

    return merged_matches


def _merge_consecutive_matches(matches):
    """
    Merge consecutive matching segments into longer segments.
    Also deduplicates - if the same time range matches multiple episodes,
    only keeps one match (the first/best one).

    Args:
        matches: List of match dicts

    Returns:
        list: Merged and deduplicated matches
    """
    if not matches:
        return []

    # First, deduplicate by target time range
    # Group matches by (start_time, end_time) and keep only one per group
    seen_ranges = {}
    for match in matches:
        key = (match['start_time'], match['end_time'])
        if key not in seen_ranges:
            seen_ranges[key] = match

    # Now merge consecutive segments
    sorted_matches = sorted(seen_ranges.values(), key=lambda x: x['start_time'])

    if not sorted_matches:
        return []

    merged = []
    current = sorted_matches[0].copy()

    for match in sorted_matches[1:]:
        # Check if they should be merged:
        # 1. Must be consecutive/overlapping in the TARGET episode
        # 2. Must match the SAME source episode
        # 3. Must be consecutive/overlapping in the SOURCE episode
        target_consecutive = match['start_time'] <= current['end_time'] + 1
        same_source = match['matched_episode_id'] == current['matched_episode_id']

        # Calculate expected source position if they're truly consecutive
        source_consecutive = False
        if same_source and 'matched_start_time' in match and 'matched_start_time' in current:
            # Check if the source segments are also consecutive
            # The match should start at roughly where the current segment ends in the source
            expected_source_start = current['matched_start_time'] + (current['end_time'] - current['start_time'])
            source_gap = abs(match['matched_start_time'] - expected_source_start)
            source_consecutive = source_gap <= 10  # Allow 10s gap due to sliding window overlap

        if target_consecutive and same_source and source_consecutive:
            # Extend current match to include this one
            current['end_time'] = max(current['end_time'], match['end_time'])
            # Keep the matched_episode_id and matched_start_time from the first match
        else:
            # Save current and start new match
            merged.append(current)
            current = match.copy()

    merged.append(current)

    return merged
