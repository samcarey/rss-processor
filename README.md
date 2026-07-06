# RSS Podcast Processor

Automatically download podcasts, detect and remove ads/promos/branding, and serve cleaned RSS feeds.

## Features

- Parse and monitor RSS podcast feeds; download new episodes automatically
- Ad detection fusing three evidence streams:
  - whole-episode audio fingerprint matching (chromaprint) — finds the same
    ad recording at any offset in any pair of episodes, ~0.1s resolution
  - whisper.cpp transcripts — sponsor-language scoring, repeated-script
    detection, ad-break bridging, post-outro tail removal
  - transcript speech boundaries for precise cut edges
- Preserves live host speech, including words spoken over ad-break music
  ("We're back", talked-over transitions) and quoted clips reused across
  episodes — only prerecorded ad/promo/branding audio is cut
- Generate cleaned RSS feeds for podcast apps
- Web interface with per-cut review: what was removed, why, the transcript
  of the removed audio, and a "hear the splice" audition button
- Background worker for continuous processing

## Quick Start

**Automated setup (recommended):**

```bash
./setup.sh
```

This script will:
- Check for and install system dependencies (chromaprint, ffmpeg)
- Create a Python virtual environment
- Install all Python dependencies
- Initialize the database
- Verify the installation

The script is idempotent and safe to run multiple times.

**Manual setup:**

1. **Install system dependencies (macOS):**
   ```bash
   brew install chromaprint ffmpeg whisper-cpp
   # whisper model (~465 MB):
   curl -L -o data/models/ggml-small.en.bin \
     https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin
   ```

2. **Create virtual environment and install dependencies:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Initialize the database:**
   ```bash
   python -c "from app.database import init_db; init_db()"
   ```

**Configuration (optional):**

Edit `config.yaml` to set your Tailscale hostname in `web.base_url` for remote access:
```yaml
web:
  base_url: https://mini4.tail350dd8.ts.net:9444
```

## Usage

**Note:** Always activate the virtual environment first:
```bash
source venv/bin/activate
```

### Running the Web Interface

```bash
python run_web.py
```

Access at http://localhost:5002 (or your Tailscale hostname)

### Running the Background Worker

```bash
python run_worker.py
```

For production use, run both the web interface and worker. The web interface lets you manage podcasts, while the worker automatically downloads and processes new episodes.

### Installing as a System Service (macOS)

1. **Edit the launchd plist file** (`com.rssprocessor.worker.plist`) to set the correct paths

2. **Copy to LaunchAgents:**
   ```bash
   cp com.rssprocessor.worker.plist ~/Library/LaunchAgents/
   ```

3. **Load the service:**
   ```bash
   launchctl load ~/Library/LaunchAgents/com.rssprocessor.worker.plist
   ```

4. **Check status:**
   ```bash
   launchctl list | grep rssprocessor
   ```

## Architecture

- `app/` - Flask web application
- `processor/` - RSS parsing, audio processing, and fingerprinting
- `daemon/` - Background worker daemon
- `data/` - Storage for episodes, processed audio, and database

## How It Works

1. **Feed Monitoring**: Worker checks RSS feeds every 30 minutes for new episodes
2. **Download**: New episodes are downloaded to `data/original/`
3. **Fingerprint + Transcribe**: Whole-episode raw chromaprint (cached as
   `.npy`) and whisper.cpp transcript (cached as JSON)
4. **Ad Detection** (`processor/ad_detector.py`): audio-repeat matching
   against every other episode of the podcast, with compilation/re-run
   exclusion, transcript confirmation of low-evidence regions, ad-break
   bridging, edge extension, speech-boundary snapping, and talk-over
   preservation of live host speech
5. **Segment Removal**: Detected regions are cut with pydub; each removed
   segment stores its method, confidence, match count, and transcript excerpt
6. **RSS Generation**: Cleaned episodes are served via custom RSS feeds
7. **Web Access**: Browse podcasts, episodes, and review each cut (with
   removed-audio playback and splice audition) via web UI

Reprocess episodes after detector changes with
`python scripts/reprocess.py [episode ids]` — cached fingerprints and
transcripts make recomputation cheap.

## Configuration

See `config.yaml` for all configuration options including:
- Storage paths
- Worker check interval
- Backfill duration for new podcasts
- Transcription model/threads and ad-detection overrides
  (defaults in `processor/ad_detector.py` DEFAULTS)

## Database Schema

- **Podcast**: RSS feed metadata
- **Episode**: Individual episode data and processing status
- **RemovedSegment**: Tracked segments removed from episodes with references to source episodes

## License

MIT
