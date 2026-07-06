# RSS Podcast Processor

Automatically download podcasts, detect and remove duplicate audio segments (ads, intros, outros), and serve cleaned RSS feeds.

## Features

- Parse and monitor RSS podcast feeds
- Download new episodes automatically
- Detect duplicate audio segments across episodes using audio fingerprinting
- Remove segments longer than 10 seconds
- Generate cleaned RSS feeds for podcast apps
- Web interface for managing podcasts and viewing removed segments
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
   brew install chromaprint ffmpeg
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
3. **Fingerprinting**: Audio is analyzed using chromaprint in 10-second windows
4. **Duplicate Detection**: Fingerprints are compared against previous episodes
5. **Segment Removal**: Duplicate segments >10s are removed using pydub
6. **RSS Generation**: Cleaned episodes are served via custom RSS feeds
7. **Web Access**: Browse podcasts, episodes, and removed segments via web UI

## Configuration

See `config.yaml` for all configuration options including:
- Storage paths
- Worker check interval
- Backfill duration for new podcasts
- Audio fingerprinting parameters

## Database Schema

- **Podcast**: RSS feed metadata
- **Episode**: Individual episode data and processing status
- **RemovedSegment**: Tracked segments removed from episodes with references to source episodes

## License

MIT
