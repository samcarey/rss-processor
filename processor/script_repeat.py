"""Repeated word-sequence (script) detection across episodes.

Catches ads whose SCRIPT repeats even when the recording differs (host-read
ads re-recorded per episode), and independently corroborates audio-repeat
evidence for ads without sponsor-language markers. Word timestamps are
interpolated inside whisper segments — accurate to ~1-2s, which is fine
because script evidence feeds the same merge/snap pipeline as audio evidence.
"""
import re
from collections import defaultdict

SHINGLE = 10       # words per shingle
MIN_RUN_WORDS = 18  # minimal common run to count as evidence


def words_with_times(tx_segments):
    """Flatten transcript segments to (word, approx_time) pairs."""
    out = []
    for s, e, t in tx_segments:
        toks = re.findall(r"[a-z0-9']+", t.lower())
        for i, w in enumerate(toks):
            out.append((w, s + (e - s) * i / max(len(toks), 1)))
    return out


def shingle_index(words):
    """word-shingle -> list of word positions."""
    toks = [w for w, _ in words]
    idx = defaultdict(list)
    for i in range(len(toks) - SHINGLE):
        idx[" ".join(toks[i:i + SHINGLE])].append(i)
    return idx


def matched_intervals(target_words, other_words):
    """Time intervals in the target whose word sequence repeats in other.

    Returns list of (start_s, end_s) in target time, maximal runs only.
    """
    toks_t = [w for w, _ in target_words]
    toks_o = [w for w, _ in other_words]
    idx_o = defaultdict(list)
    for i in range(len(toks_o) - SHINGLE):
        idx_o[" ".join(toks_o[i:i + SHINGLE])].append(i)

    covered = set()
    intervals = []
    for i in range(len(toks_t) - SHINGLE):
        if i in covered:
            continue
        sh = " ".join(toks_t[i:i + SHINGLE])
        for j in idx_o.get(sh, ()):
            a, b = i, j
            while a > 0 and b > 0 and toks_t[a - 1] == toks_o[b - 1]:
                a -= 1; b -= 1
            c, d = i + SHINGLE, j + SHINGLE
            while c < len(toks_t) and d < len(toks_o) and toks_t[c] == toks_o[d]:
                c += 1; d += 1
            if c - a >= MIN_RUN_WORDS:
                covered.update(range(a, c))
                intervals.append((target_words[a][1], target_words[c - 1][1]))
            break
    # merge overlapping
    intervals.sort()
    merged = []
    for s, e in intervals:
        if merged and s <= merged[-1][1] + 2.0:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]
