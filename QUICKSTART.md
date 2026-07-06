# Quick Start Guide

## 1. Setup (One Time)

Run the automated setup script:

```bash
./setup.sh
```

This will install all dependencies and set up the environment. It's safe to run multiple times.

## 2. Test Installation (Optional)

Verify everything is working:

```bash
source venv/bin/activate
python test_installation.py
```

## 3. Start the Application

### Easy Mode - Both Services at Once

```bash
./run_both.sh
```

This starts both the web interface and worker in the background. Press Ctrl+C to stop both.

### Manual Mode - Separate Terminals

**Terminal 1 - Web Interface:**

```bash
source venv/bin/activate
python run_web.py
```

Visit http://localhost:5002

**Terminal 2 - Background Worker:**

```bash
source venv/bin/activate
python run_worker.py
```

## 4. Add Your First Podcast

1. Open http://localhost:5002 in your browser
2. Paste an RSS feed URL (e.g., from your favorite podcast)
3. Click "Add Podcast"
4. The worker will automatically download and process episodes

## 5. Subscribe on Your Phone

1. On the podcast detail page, copy the "Cleaned RSS Feed" URL
2. Add this URL to your podcast app
3. Episodes will have ads and duplicates removed!

## Remote Access via Tailscale

1. Edit `config.yaml` and update `web.base_url`:
   ```yaml
   web:
     base_url: https://your-tailscale-hostname:9444
   ```

2. Restart the web interface

3. Access from your phone at `https://your-tailscale-hostname:9444`

## System Service (Optional)

To run the worker automatically on boot:

1. Edit `com.rssprocessor.worker.plist` and update the paths
2. Install:
   ```bash
   cp com.rssprocessor.worker.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.rssprocessor.worker.plist
   ```

## Troubleshooting

**Virtual environment issues:**
```bash
rm -rf venv
./setup.sh
```

**Database issues:**
```bash
rm data/rss-processor.db
source venv/bin/activate
python -c "from app.database import init_db; init_db()"
```

**Port already in use:**
Edit `config.yaml` and change `web.port` to a different number.

## What Happens Automatically

- Worker checks for new episodes every 30 minutes
- New episodes are downloaded and processed
- Duplicate segments (>10 seconds) are detected and removed
- Cleaned episodes are served via custom RSS feeds
- All removed segments are saved for review in the web UI
