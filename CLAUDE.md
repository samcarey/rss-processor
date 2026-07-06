# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

Self-hosted podcast ad-remover. It watches RSS feeds, downloads new episodes,
detects ad/promo segments, cuts them out, and re-serves a cleaned RSS feed
that podcast apps subscribe to.

## Ad detection (processor/ad_detector.py)

Fusion of audio-repeat evidence and transcript analysis. Read the module
docstring first; the short version:

1. **Audio repeats**: whole-episode raw chromaprint (`fpcalc -raw`, one
   uint32 per ~0.124s, cached as `.npy`) matched pairwise between episodes by
   offset-voting + bit-error-rate runs (`processor/matching.py`). Finds the
   same inserted ad anywhere in any two episodes at ~0.1s resolution. Grid
   windows and exact string equality (the old approach) CANNOT do this — the
   old detector only ever matched pre-rolls, where grids align by luck.
2. **Compilation exclusion**: "Weekly" episodes literally contain the week's
   dailies. Any pair whose matched total exceeds `max_pair_fraction` of the
   shorter episode is discarded wholesale; single runs > `max_ad_run_seconds`
   likewise (content reuse, not ads).
3. **Transcripts** (whisper.cpp `small.en`, Metal, ~15-20x realtime; cached
   JSON): confirm low-evidence regions (n < `confirm_below_matches` matched
   episodes — quoted news clips reused across 2-3 episodes look identical to
   ads otherwise!), find standalone unrepeated ad blocks via sponsor-language
   markers (`processor/ad_language.py`), bridge ad-text gaps between cuts,
   extend edges across unrepeated ads, and cut the post-outro tail to EOF.
4. **Boundary snapping**: cut edges snap to transcript speech boundaries.
   Ad-break transition stings smear chromaprint matches ~1s past speech onset
   (music decay dominates the chroma) — snapping to the next speech onset
   fixes that. Whisper timestamps bleed across long music, so segment ends
   are less trustworthy than starts.
5. **Live host speech is preserved** (talk-over pass): the music bed
   dominates chromaprint, so matched sting regions swallow hosts talking
   over the fade. Edge segments are released when their words are
   episode-unique OR match the break-formula whitelist ("We're back" etc. —
   spoken live each episode even though the text repeats). Two approaches
   were tried and REJECTED: "short repeated text is live" (released
   prerecorded ad taglines like "Toyota, let's go places"), and a span-BER
   same-recording test (sting music dominates chroma; different speech
   moves BER less than encode noise, 0.044 vs 0.031). Relatedly,
   script-repeat evidence requires the matched span to read as ad copy —
   hosts re-speak their show-intro formula near-verbatim weekly, and only
   ad-language separates that from a host-READ ad script.
5. **Sanity check**: feeds declare the ad-free duration; the stitched file is
   longer. `total_cut ≈ file_duration - feed_duration + ~40s` (jingle/outro/
   sting cuts are extra, by design — repetitive non-ad audio is cut too).
   Logged per episode as the "feed-duration budget".

Every RemovedSegment stores `method` (provenance chain like
`audio_repeat+bridge+extend`), `confidence`, `n_matches`, and
`transcript_excerpt` — the episode page shows all of it, with per-cut playback
of the removed audio AND a "hear the splice" button that seeks the processed
audio 5s before the cut point.

After detection changes (or to let early episodes benefit from evidence that
arrived later), run `python scripts/reprocess.py [episode ids]` — cached
fingerprints/transcripts make recomputation cheap; only re-export costs.

## Architecture

- `app/` — Flask web UI (`run_web.py`). Routes in `app/routes.py`, SQLAlchemy
  models in `app/models.py` (Podcast → Episode → RemovedSegment, ORM-level
  cascades — deletes must go through the ORM, not raw SQL, and SQLite FKs are
  NOT enforced). Additive schema migrations live in `app/database.py`.
- `daemon/` — background worker (`run_worker.py`). Every
  `worker.check_interval_seconds` it: downloads pending episodes, processes
  downloaded-but-unprocessed ones, then re-parses every feed.
- `processor/` — feed parsing (rss_parser), download (downloader),
  matching.py / transcripts.py / ad_language.py / ad_detector.py (see above),
  audio_processor.py (wires detection to DB+files, cuts with pydub),
  cleaned-feed generation (rss_generator, feedgen).
- `data/` (gitignored) — SQLite DB (WAL mode) + `original/`, `processed/`,
  `segments/`, `fingerprints/` (.npy), `transcripts/` (.json), `models/`
  (whisper ggml). Paths are stored config-relative (`./data/...`); resolve
  them with `processor.rss_generator.resolve_storage_path` — never assume cwd.
- Web and worker are separate processes sharing the SQLite DB. The web UI can
  also run one worker cycle in a background thread (`/api/trigger_worker`), so
  restarting the web server can kill an in-flight processing run (the worker
  re-picks it up).

## Running / deployment (mini4)

Web + worker run as launchd agents (`com.rssprocessor.web` /
`com.rssprocessor.worker` in `~/Library/LaunchAgents`, RunAtLoad +
KeepAlive — they survive reboots and crashes). Restart with
`launchctl kickstart -k gui/501/com.rssprocessor.web` (or `.worker`);
plists live in the repo root. For ad-hoc runs:

```bash
source venv/bin/activate   # python 3.14 venv
python run_web.py          # web UI on 0.0.0.0:5002
python run_worker.py       # background worker
```

System deps: `brew install ffmpeg chromaprint whisper-cpp`; whisper model at
`data/models/ggml-small.en.bin` (from huggingface ggerganov/whisper.cpp).
Binaries are invoked by absolute path (`/opt/homebrew/bin/...`) because
detached/nohup shells here have an unusable PATH — keep it that way.

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
- Detection compares against ALL other episodes of the same podcast that have
  cached fingerprints (not just earlier ones) — an ad is cut from every
  episode carrying it, including its first appearance.
- Transcription failure is non-fatal: detection degrades to pure audio-repeat
  evidence (with the stricter n >= confirm_below_matches bar).
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

## Current state (2026-07-06)

- Detection pipeline validated cut-by-cut against transcripts across the
  full 41-episode corpus (It Could Happen Here + 99% Invisible); every
  removed excerpt reads as ad/promo/branding and every splice resumes on
  live content. Budget check holds corpus-wide.
- Web UI + worker verified with Playwright (including the per-cut review
  players and splice audition at iPhone viewport).
- 99% Invisible's feed carries no inserted ads (file == feed duration);
  zero cuts there is correct, not a bug.
- Validation method for any detector change: run detection, print
  BEFORE/INSIDE/AFTER transcript context per cut, read every cut, compare
  totals to the feed-duration budget, then `scripts/reprocess.py` the
  changed episodes.
