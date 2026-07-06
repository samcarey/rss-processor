"""Full-resolution cross-episode audio matching via raw chromaprint.

fpcalc -raw yields one 32-bit perceptual hash per ~0.124s of audio. The same
ad audio inserted in two episodes (at arbitrary offsets, re-encoded) shows up
as a diagonal run of low hamming distance at a fixed offset. Candidate offsets
come from exact-value voting; runs are verified by smoothed bit-error rate.

Fingerprints are cached as .npy files under storage.fingerprints (keyed by
episode id) so matching never re-decodes audio.
"""
import json
import logging
import os
import subprocess
from collections import defaultdict

import numpy as np

logger = logging.getLogger(__name__)

FP_RATE = 8.06  # chromaprint items per second (11025 Hz / 1365-sample step)


def fingerprint_path(config, episode_id):
    from processor.rss_generator import resolve_storage_path
    d = resolve_storage_path(config['storage'].get('fingerprints', './data/fingerprints'))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"episode_{episode_id}.npy")


def raw_fingerprint(audio_path, cache_path=None):
    """Whole-file raw chromaprint as a uint32 array, optionally cached."""
    if cache_path and os.path.exists(cache_path):
        return np.load(cache_path)
    result = subprocess.run(
        ["fpcalc", "-raw", "-json", "-length", "0", audio_path],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    fp = np.array(data["fingerprint"], dtype=np.int64).astype(np.uint32)
    if cache_path:
        np.save(cache_path, fp)
    return fp


def _candidate_offsets(fp_a, fp_b, min_votes=8):
    """Offsets (i_a - i_b) where many exact 32-bit values coincide."""
    positions_b = defaultdict(list)
    for j, v in enumerate(fp_b):
        positions_b[v].append(j)
    votes = defaultdict(int)
    for i, v in enumerate(fp_a):
        for j in positions_b.get(v, ()):
            votes[i - j] += 1
    # cluster neighboring offsets (mp3 padding jitter) into one candidate
    good = sorted(votes)
    clusters = []
    for o in good:
        if clusters and o - clusters[-1][-1] <= 2:
            clusters[-1].append(o)
        else:
            clusters.append([o])
    out = []
    for cl in clusters:
        total = sum(votes[o] for o in cl)
        if total >= min_votes:
            center = max(cl, key=lambda o: votes[o])
            out.append((center, total))
    out.sort(key=lambda t: -t[1])
    return out


def _ber_runs(fp_a, fp_b, offset, max_ber=0.30, smooth=17, min_run_s=5.0):
    """Low-BER runs along the diagonal at `offset` (+/-1 jitter tolerated).

    Returns list of (start_a_s, end_a_s, start_b_s, mean_ber).
    """
    lo = max(0, offset)
    hi = min(len(fp_a), len(fp_b) + offset)
    if hi - lo < smooth:
        return []
    a = fp_a[lo:hi]
    n = len(a)
    dists = np.full(n, 32, dtype=np.uint8)
    for jit in (-1, 0, 1):
        b_start = lo - offset + jit
        b_end = b_start + n
        if b_start < 0 or b_end > len(fp_b):
            continue
        d = np.bitwise_count(a ^ fp_b[b_start:b_end])
        dists = np.minimum(dists, d)
    ber = dists.astype(np.float32) / 32.0
    sm = np.convolve(ber, np.ones(smooth) / smooth, mode="same")
    below = sm < max_ber
    runs = []
    start = None
    for i, ok in enumerate(np.append(below, False)):
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            if (i - start) / FP_RATE >= min_run_s:
                seg_ber = float(ber[start:i].mean())
                if seg_ber < max_ber:
                    runs.append((
                        (lo + start) / FP_RATE,
                        (lo + i) / FP_RATE,
                        (lo + start - offset) / FP_RATE,
                        seg_ber,
                    ))
            start = None
    return runs


def match_pair(fp_a, fp_b, min_votes=8, min_run_s=5.0, max_ber=0.30):
    """All matched segments between two fingerprints.

    Returns list of (start_a_s, end_a_s, start_b_s, mean_ber), non-overlapping
    in A (lowest-BER wins), sorted by start.
    """
    segs = []
    for offset, _votes in _candidate_offsets(fp_a, fp_b, min_votes)[:40]:
        segs.extend(_ber_runs(fp_a, fp_b, offset, max_ber=max_ber, min_run_s=min_run_s))
    segs.sort(key=lambda s: s[3])
    kept = []
    for s in segs:
        if all(s[1] <= k[0] + 0.5 or s[0] >= k[1] - 0.5 for k in kept):
            kept.append(s)
    kept.sort()
    return kept


def merge_intervals(intervals, gap=2.0):
    """Union of (start, end) intervals, merging gaps <= gap seconds."""
    if not intervals:
        return []
    intervals = sorted(intervals)
    out = [list(intervals[0])]
    for s, e in intervals[1:]:
        if s <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]
