"""Episode transcription via whisper.cpp and transcript access helpers.

Transcripts are whisper-cli full-JSON files stored under storage.transcripts
as episode_<id>.json. Transcription is Metal-accelerated and runs at roughly
15-20x realtime with the small.en model on this machine.
"""
import json
import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)

WHISPER_BIN = "/opt/homebrew/bin/whisper-cli"
FFMPEG_BIN = "/opt/homebrew/bin/ffmpeg"


def transcript_path(config, episode_id):
    from processor.rss_generator import resolve_storage_path
    d = resolve_storage_path(config['storage'].get('transcripts', './data/transcripts'))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"episode_{episode_id}.json")


def transcribe(audio_path, out_json_path, config):
    """Transcribe an audio file; writes whisper full-JSON to out_json_path."""
    from processor.rss_generator import resolve_storage_path
    model = resolve_storage_path(
        config.get('transcription', {}).get('model_path', './data/models/ggml-small.en.bin'))
    threads = str(config.get('transcription', {}).get('threads', 6))
    if not os.path.exists(model):
        raise FileNotFoundError(f"whisper model not found: {model}")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav = tmp.name
    try:
        subprocess.run(
            [FFMPEG_BIN, "-nostdin", "-y", "-loglevel", "error",
             "-i", audio_path, "-ar", "16000", "-ac", "1", wav],
            check=True,
        )
        out_prefix = out_json_path[:-5] if out_json_path.endswith(".json") else out_json_path
        subprocess.run(
            [WHISPER_BIN, "-m", model, "-f", wav, "-ojf", "-of", out_prefix,
             "-np", "-t", threads],
            check=True, capture_output=True,
        )
    finally:
        if os.path.exists(wav):
            os.remove(wav)
    logger.info(f"Transcribed {audio_path} -> {out_json_path}")
    return out_json_path


def load_segments(json_path):
    """Load transcript as a list of (start_s, end_s, text) tuples."""
    if not json_path or not os.path.exists(json_path):
        return None
    with open(json_path) as f:
        data = json.load(f)
    segs = []
    for s in data.get("transcription", []):
        text = s["text"].strip()
        if text:
            segs.append((s["offsets"]["from"] / 1000.0,
                         s["offsets"]["to"] / 1000.0, text))
    return segs


def text_between(segs, t0, t1):
    """Concatenated transcript text overlapping [t0, t1]."""
    if not segs:
        return ""
    return " ".join(t for s, e, t in segs if e > t0 and s < t1)
