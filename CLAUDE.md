# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

Self-hosted podcast ad-remover. It watches RSS feeds, downloads new episodes,
fingerprints the audio (chromaprint/`fpcalc`), removes segments that duplicate
audio heard in earlier episodes of the same podcast (ads, intros, outros), and
re-serves a cleaned RSS feed that podcast apps subscribe to.

## Architecture

- `app/` — Flask web UI (`run_web.py`). Routes in `app/routes.py`, SQLAlchemy
  models in `app/models.py` (Podcast → Episode → RemovedSegment /
  AudioFingerprint, ORM-level cascades — deletes must go through the ORM, not
  raw SQL, and SQLite FKs are NOT enforced).
- `daemon/` — background worker (`run_worker.py`). Every
  `worker.check_interval_seconds` it: downloads pending episodes, processes
  downloaded-but-unprocessed ones, then re-parses every feed.
- `processor/` — feed parsing (feedparser), download (requests+pydub),
  fingerprint/duplicate detection (pyacoustid → `fpcalc`), segment removal
  (pydub), cleaned-feed generation (feedgen).
- `data/` (gitignored) — SQLite DB (WAL mode) + `original/`, `processed/`,
  `segments/` audio. Paths are stored config-relative (`./data/...`); resolve
  them with `processor.rss_generator.resolve_storage_path` — never assume cwd.
- Web and worker are separate processes sharing the SQLite DB. The web UI can
  also run one worker cycle in a background thread (`/api/trigger_worker`), so
  restarting the web server can kill an in-flight processing run (the worker
  re-picks it up).

## Running / deployment (mini4)

```bash
source venv/bin/activate   # python 3.14 venv
python run_web.py          # web UI on 0.0.0.0:5002
python run_worker.py       # background worker
```

- **Ports**: web is on **5002**. 5000 is macOS AirPlay, 5001 is the
  bad-spaceship game server. Don't move it without checking `lsof`.
- **Tailscale**: the tailnet URL is
  `https://mini4.tail350dd8.ts.net:9444/` via
  `tailscale serve --bg --https=9444 http://127.0.0.1:5002`.
  Direct `:5002` from other devices is blocked by the macOS application
  firewall (this venv's python isn't in its allow list; adding it needs sudo).
  9443/8443/10000/443 on this host belong to other services — don't reuse.
- `web.base_url` in `config.yaml` must be the externally reachable URL; it's
  baked into feed enclosure URLs. It's read per-request, no restart needed.
- Flask debug mode is off by default (`FLASK_DEBUG=1` to opt in). Never enable
  it while bound to 0.0.0.0 — the Werkzeug debugger is an RCE for the tailnet.
- Audio routes must keep `conditional=True` (Range/206) or iOS Safari playback
  and scrubbing break.

## Behavior contracts

- `worker.backfill_days` is a hard window: episodes older than it are never
  inserted, whether the podcast is new or the worker was down for months.
  Without this, a stale podcast triggers an unbounded catch-up download
  (there's a 1400-episode daily podcast in the DB — this is not hypothetical).
- `add_podcast` inserts episode rows (within the window) immediately so the
  podcast page isn't empty; downloading/processing stays in the worker.
- Duplicate detection only compares against *earlier* episodes
  (`pub_date <` current) of the *same* podcast, and only removes matches ≥
  `audio.min_duplicate_duration_seconds`.
- feedgen needs timezone-aware datetimes; `pub_date` is stored naive-UTC, so
  attach `timezone.utc` before `fe.published()` (see `rss_generator.py`).

## Testing the UI / pipeline

End-to-end without hour-long downloads: generate a synthetic feed of three
~65s episodes that share an identical 15s "ad" (ffmpeg sine/tremolo segment
between unique noise beds), serve it with `python3 -m http.server` on
localhost, add it through the UI, hit "Check for New Episodes", and assert a
RemovedSegment at 25–40s on episodes 2/3. A Playwright (Python, homebrew
`playwright` CLI / python3.13) script that exercises every page, form,
negative input, delete flow, and Range requests this way lives in the session
scratchpad (`ui_test.py`) — recreate from this recipe if needed.

Gotchas hit before:
- Processes launched from sandboxed shells can bind but silently drop
  non-loopback connections — launch servers unsandboxed.
- Python processes here take ~30–60s to reach listening state; poll, don't
  assume.
- Homebrew ffmpeg can break after `svt-av1` bumps (dyld error); fix with
  `brew upgrade ffmpeg`. pydub depends on it, so the whole pipeline stops.

## Current state / next up

- Web UI + worker flows verified end-to-end 2026-07-06 (24/25 Playwright
  checks; the one "failure" was the intentional bad-feed negative test, whose
  raw-500 page was then replaced by a redirect-with-error-banner).
- Next planned work: audio analysis and editing features (deeper than
  fingerprint-duplicate removal).
