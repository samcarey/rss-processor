"""Ad detection: fuse audio-repeat evidence with transcript analysis.

Pipeline per target episode:
 1. Match raw fingerprints against every other episode of the podcast.
    - a pair whose total matched audio exceeds max_pair_fraction of the
      shorter episode is a compilation/re-run relationship: ALL its evidence
      is discarded (the Weekly episodes literally contain the dailies).
    - a single contiguous run longer than max_ad_run_seconds is content
      reuse, not an ad: discarded.
 2. Merge surviving intervals into regions with a distinct-episode count n.
 3. Keep regions with n >= min_match_episodes outright; keep n=1 regions only
    when the transcript confirms ad/promo language (guards against content
    quotes and coincidental matches).
 4. Add standalone transcript-only ad blocks (dense sponsor language with no
    repeat evidence, e.g. an ad that appears once in the corpus).
 5. Bridge gaps between nearby cuts when the gap text is ad-like (catches
    unrepeated ads sandwiched inside an ad break).
 6. Extend cut edges across adjacent unrepeated ads/promos using a lookahead
    strong-marker walk (ad breaks end with "...wherever you get your
    podcasts" style tails that make this reliable).

Boundary snapping to local energy minima happens at cut time in
audio_processor (where the decoded audio is already in memory).
"""
import logging

from processor.ad_language import ad_score, is_ad_text, is_strong_ad_text, strong_hits
from processor.matching import FP_RATE, match_pair, merge_intervals
from processor.transcripts import text_between

logger = logging.getLogger(__name__)

DEFAULTS = {
    "min_repeat_seconds": 5.0,
    "match_max_ber": 0.30,
    "max_ad_run_seconds": 300.0,
    "max_pair_fraction": 0.35,
    "min_match_episodes": 2,
    # below this many matched episodes, audio evidence alone is ambiguous
    # (quoted clips reused across 2-3 episodes look identical to ads):
    # require the transcript to confirm ad language.
    "confirm_below_matches": 4,
    "bridge_max_gap_seconds": 90.0,
    # a gap this short between two confirmed ad cuts is bridged without a
    # text test: ad breaks are structural units minutes apart, so a sub-90s
    # island between two cuts is more ads (possibly with no recognizable
    # marker phrases), not content.
    "bridge_free_gap_seconds": 90.0,
    "extend_lookahead_seconds": 75.0,
    "extend_cap_seconds": 300.0,
    "min_standalone_seconds": 20.0,
    "min_cut_seconds": 5.0,
    # after the network outro, everything remaining is promos/branding:
    # bridge tail gaps unconditionally and run the cut to end-of-file.
    "outro_markers": ["is a production of cool zone media",
                      "is a production of"],
    "tail_bridge_gap_seconds": 150.0,
    "tail_eof_snap_seconds": 25.0,
    "llm_min_fragment_seconds": 15.0,
    "llm_min_release_seconds": 10.0,
    "llm_veto_window_words": 25,
}


def _params(config):
    p = dict(DEFAULTS)
    p.update(config.get("ad_detection", {}) or {})
    return p


def detect_ads(target, others, config):
    """Detect ad segments in one episode.

    Args:
        target: dict with id, fp (uint32 array), tx (transcript segments or
            None), file_duration, feed_duration (may be None)
        others: list of dicts with id, fp
        config: app config

    Returns:
        (cuts, report) where cuts is a list of dicts
        {start, end, method, n_matches, confidence, excerpt}
        sorted by start, and report summarizes evidence and the
        feed-duration budget check.
    """
    p = _params(config)
    tx = target.get("tx")
    dur = target["file_duration"] or (len(target["fp"]) / FP_RATE)

    # --- 1. pairwise audio-repeat evidence
    evidence = []          # (start, end, other_id, ber)
    excluded_pairs = []
    excluded_ids = set()
    for other in others:
        runs = match_pair(target["fp"], other["fp"],
                          min_run_s=p["min_repeat_seconds"],
                          max_ber=p["match_max_ber"])
        if not runs:
            continue
        total = sum(r[1] - r[0] for r in runs)
        shorter = min(len(target["fp"]), len(other["fp"])) / FP_RATE
        if total > p["max_pair_fraction"] * shorter:
            excluded_pairs.append((other["id"], round(total, 1)))
            excluded_ids.add(other["id"])
            continue
        for sa, ea, _sb, ber in runs:
            if ea - sa > p["max_ad_run_seconds"]:
                logger.info(f"drop over-long run {sa:.0f}-{ea:.0f}s vs ep {other['id']}")
                continue
            evidence.append((sa, ea, other["id"], ber))

    # --- 1b. script-repeat evidence: the same words across episodes catches
    # re-read ads (different recordings) and corroborates marker-less ads.
    # The same compilation guards apply — weeklies share the dailies' text.
    # Each interval must READ as ad copy: hosts also re-speak their intro
    # formula near-verbatim every episode, and live speech is kept.
    if tx:
        from processor.script_repeat import words_with_times, matched_intervals
        t_words = words_with_times(tx)
        for other in others:
            if other["id"] in excluded_ids or not other.get("tx"):
                continue
            o_words = words_with_times(other["tx"])
            if not t_words or not o_words:
                continue
            ivs = matched_intervals(t_words, o_words)
            if not ivs:
                continue
            total = sum(e - s for s, e in ivs)
            shorter = min(t_words[-1][1], o_words[-1][1])
            if total > p["max_pair_fraction"] * shorter:
                continue
            for s, e in ivs:
                if e - s <= p["max_ad_run_seconds"] \
                        and is_ad_text(text_between(tx, s, e)):
                    evidence.append((s, e, other["id"], None))

    # --- 2. merge into regions with distinct-episode counts
    regions = []
    for s, e in merge_intervals([(a, b) for a, b, _, _ in evidence], gap=2.0):
        srcs = {o for a, b, o, _ in evidence if a < e and b > s}
        regions.append({"start": s, "end": e, "n": len(srcs),
                        "srcs": sorted(srcs)})

    # --- 3. filter n=1 regions through the transcript
    confirm_below = p["confirm_below_matches"]
    kept = []
    for r in regions:
        if r["n"] >= max(p["min_match_episodes"], confirm_below):
            r["method"] = "audio_repeat"
            r["confidence"] = 0.9
            kept.append(r)
        elif r["n"] >= p["min_match_episodes"] and tx is None:
            # no transcript available: trust plain repeat evidence
            r["method"] = "audio_repeat"
            r["confidence"] = 0.8
            kept.append(r)
        else:
            inside = text_between(tx, r["start"], r["end"]) if tx else ""
            # pure music (no speech) repeated across episodes is a transition
            # sting / theme, not quoted content: safe to cut without text.
            if not inside and r["n"] >= p["min_match_episodes"]:
                r["method"] = "audio_repeat_music"
                r["confidence"] = 0.85
                kept.append(r)
            elif inside and is_ad_text(inside):
                r["method"] = "audio_repeat+transcript"
                r["confidence"] = 0.8
                kept.append(r)
            else:
                logger.info(f"reject low-evidence region {r['start']:.0f}-{r['end']:.0f}s "
                            f"(n={r['n']}, not ad text): {inside[:80]!r}")

    # --- 4. standalone transcript-only ad blocks
    if tx:
        kept.extend(_standalone_blocks(tx, kept, p))

    # --- 5+6. bridge gaps and extend edges through unrepeated ad text
    # exclude compilation-related episodes: a weekly contains the daily's
    # text verbatim, which would make ALL of the daily's speech look
    # "repeated elsewhere" and defeat talk-over detection.
    other_shingles = set()
    if tx:
        from processor.script_repeat import word_shingle_set
        for other in others:
            if other.get("tx") and other["id"] not in excluded_ids:
                other_shingles |= word_shingle_set(other["tx"])
    cuts = _assemble(kept, tx, dur, p, evidence, other_shingles)

    # --- 7. LLM adjudication: the lexical rules above generate candidates;
    # a local LLM reads each cut in context and releases show content.
    if tx and (config.get("llm_judge") or {}).get("enabled", False):
        cuts = review_cuts(cuts, tx, evidence, dur, p, config,
                           target.get("show"), target.get("title"), other_shingles)

    total_cut = sum(c["end"] - c["start"] for c in cuts)
    budget = None
    if target.get("feed_duration") and dur and dur > target["feed_duration"]:
        budget = round(dur - target["feed_duration"], 1)
    report = {
        "total_cut": round(total_cut, 1),
        "expected_from_feed": budget,
        "excluded_pairs": excluded_pairs,
        "n_regions": len(cuts),
    }
    return cuts, report


def _standalone_blocks(tx, kept, p):
    """Ad blocks found from transcript alone: seed at segments carrying a
    strong marker, grow via the marker walk, then apply the strict test.
    Segment-seeded (not window-gridded) so block edges can't swallow
    adjacent content."""
    blocks = []
    if not tx:
        return blocks
    seeds = [(s, e) for s, e, t in tx if strong_hits(t)]
    for s0, e0 in merge_intervals(seeds, gap=30.0):
        s = _walk(tx, s0, 0.0, p, forward=False)
        e = _walk(tx, e0, tx[-1][1], p, forward=True)
        overlap = sum(min(e, r["end"]) - max(s, r["start"])
                      for r in kept if r["start"] < e and r["end"] > s)
        if overlap > 0.5 * (e - s):
            continue
        text = text_between(tx, s, e)
        if (e - s) >= p["min_standalone_seconds"] and is_strong_ad_text(text):
            blocks.append({"start": s, "end": e, "n": 0, "srcs": [],
                           "method": "transcript_only", "confidence": 0.7})
    return blocks


def _assemble(regions, tx, dur, p, evidence=None, other_shingles=None):
    """Bridge ad-text gaps, extend edges, trim weak edges, clamp, format."""
    regions = sorted(regions, key=lambda r: r["start"])

    # bridge: merge neighbors when the gap between them reads like ad copy
    merged = []
    for r in regions:
        if merged:
            prev = merged[-1]
            gap0, gap1 = prev["end"], r["start"]
            if gap1 - gap0 <= 0.5:
                _absorb(prev, r)
                continue
            if gap1 - gap0 <= p["bridge_free_gap_seconds"]:
                prev["method"] = _join_methods(prev["method"], "bridge", r["method"])
                _absorb(prev, r)
                continue
            if tx and gap1 - gap0 <= p["bridge_max_gap_seconds"]:
                gap_text = text_between(tx, gap0, gap1)
                if gap_text and is_ad_text(gap_text):
                    prev["method"] = _join_methods(prev["method"], "bridge", r["method"])
                    _absorb(prev, r)
                    continue
        merged.append(dict(r))

    # extend edges through adjacent unrepeated ads/promos
    if tx:
        for i, r in enumerate(merged):
            limit_end = merged[i + 1]["start"] if i + 1 < len(merged) else dur
            new_end = _walk(tx, r["end"], limit_end, p, forward=True)
            if new_end > r["end"] + 1.0:
                r["end"] = new_end
                r["method"] = _join_methods(r["method"], "extend")
            limit_start = merged[i - 1]["end"] if i > 0 else 0.0
            new_start = _walk(tx, r["start"], limit_start, p, forward=False)
            if new_start < r["start"] - 1.0:
                r["start"] = new_start
                r["method"] = _join_methods(r["method"], "extend")

    # post-outro tail: once the network outro has played, everything left is
    # promos/branding — bridge remaining gaps regardless of gap text and run
    # the last cut to end-of-file if only a sliver remains.
    if tx:
        outro_at = None
        for r in merged:
            text = text_between(tx, r["start"], r["end"]).lower()
            if any(m in text for m in p["outro_markers"]):
                outro_at = r["end"]
                break
        if outro_at is not None:
            tail = [r for r in merged if r["end"] >= outro_at]
            for prev, nxt in zip(tail, tail[1:]):
                if nxt["start"] - prev["end"] <= p["tail_bridge_gap_seconds"]:
                    prev["method"] = _join_methods(prev["method"], "post_outro_tail")
                    nxt["start"] = prev["end"]  # merged by the pass below
            if tail and dur - tail[-1]["end"] <= p["tail_eof_snap_seconds"]:
                tail[-1]["end"] = dur
                tail[-1]["method"] = _join_methods(tail[-1]["method"], "post_outro_tail")

        # trailing jingle/branding after the last cut (shows whose outro
        # phrasing we don't recognize still end with network branding)
        if merged:
            last = max(merged, key=lambda r: r["end"])
            tail_len = dur - last["end"]
            if 0.5 < tail_len <= p["tail_eof_snap_seconds"]:
                tail_text = text_between(tx, last["end"], dur)
                if not tail_text or strong_hits(tail_text):
                    last["end"] = dur
                    last["method"] = _join_methods(last["method"], "eof_tail")

    # re-merge anything extension brought into contact, clamp, format
    final = []
    for r in sorted(merged, key=lambda x: x["start"]):
        r["start"] = max(0.0, r["start"])
        r["end"] = min(dur, r["end"])
        if final and r["start"] <= final[-1]["end"] + 0.5:
            _absorb(final[-1], r)
        else:
            final.append(r)

    # edge trim: peel off edge segments that have neither multi-episode
    # audio coverage nor ad markers (kills content leaked into a region
    # boundary by a coincidental single-episode match).
    if tx and evidence:
        for r in final:
            _trim_edges(r, tx, evidence, dur)

    # snap edges to speech boundaries: transition stings smear chromaprint
    # matches ~1s past speech onset (music decay dominates the chroma), and
    # ad breaks sit between speech gaps. Ends snap to the nearest speech
    # onset, starts to the nearest speech end; extending an edge is only
    # allowed across non-speech.
    if tx:
        for r in final:
            _snap_to_speech(r, tx, dur)

    # talk-over preservation: the music bed dominates chromaprint, so a
    # matched sting region can swallow the host speaking over the fade.
    # Prerecorded speech has the same WORDS wherever the audio repeats;
    # live talk-over is textually unique — release unique-speech segments
    # from the cut edges inward.
    if tx and other_shingles:
        for r in final:
            _preserve_talkover(r, tx, other_shingles)

    cuts = []
    for r in final:
        if r["end"] - r["start"] < p["min_cut_seconds"]:
            continue
        excerpt = text_between(tx, r["start"], r["end"])[:300] if tx else ""
        cuts.append({
            "start": round(r["start"], 2),
            "end": round(r["end"], 2),
            "method": r["method"],
            "n_matches": r.get("n", 0),
            "confidence": r.get("confidence", 0.7),
            "excerpt": excerpt,
        })
    return cuts


def _absorb(prev, r):
    prev["end"] = max(prev["end"], r["end"])
    prev["n"] = max(prev.get("n", 0), r.get("n", 0))
    prev["srcs"] = sorted(set(prev.get("srcs", [])) | set(r.get("srcs", [])))
    prev["confidence"] = max(prev.get("confidence", 0.7), r.get("confidence", 0.7))
    prev["method"] = _join_methods(prev.get("method"), r.get("method"))


def _join_methods(*methods):
    seen = []
    for m in methods:
        if m:
            for part in m.split("+"):
                if part not in seen:
                    seen.append(part)
    return "+".join(seen)


def _trim_edges(r, tx, evidence, dur, max_trim=20.0):
    """Shrink a cut's edges past transcript segments that are fully inside
    the cut but have neither >=2-episode audio coverage nor ad markers."""
    def n_cover(s, e):
        return len({o for a, b, o, _ in evidence
                    if min(b, e) - max(a, s) > 0.5 * (e - s)})

    # a segment bisected by the cut edge whose inside-the-cut part is content
    # (no marker, <2-episode coverage): clamp the edge to the segment
    # boundary so the sentence survives whole.
    if r["end"] < dur - 0.5:
        for s, e, t in tx:
            if s < r["end"] < e and e - r["end"] > 1.0 and s > r["start"]:
                if not strong_hits(t) and n_cover(max(s, r["start"]), r["end"]) < 2:
                    r["end"] = s
                break
    if r["start"] > 0.5:
        for s, e, t in tx:
            if s < r["start"] < e and r["start"] - s > 1.0 and e < r["end"]:
                if not strong_hits(t) and n_cover(r["start"], min(e, r["end"])) < 2:
                    r["start"] = e
                break

    inside = [seg for seg in tx if seg[0] >= r["start"] - 0.5 and seg[1] <= r["end"] + 0.5]
    trimmed = 0.0
    while inside and trimmed < max_trim:
        s, e, t = inside[-1]
        if r["end"] >= dur - 0.5:  # cut runs to EOF: keep the tail intact
            break
        if e < r["end"] - 3.0 or strong_hits(t) or n_cover(s, e) >= 2:
            break
        trimmed += e - s
        r["end"] = s
        inside.pop()
    trimmed = 0.0
    while inside and trimmed < max_trim:
        s, e, t = inside[0]
        if r["start"] <= 0.5:  # cut starts at file head: keep intact
            break
        if s > r["start"] + 3.0 or strong_hits(t) or n_cover(s, e) >= 2:
            break
        trimmed += e - s
        r["start"] = e
        inside.pop(0)


def _preserve_talkover(r, tx, other_shingles, max_trim=20.0):
    """Release episode-specific speech at the cut edges.

    Walk transcript segments inward from each edge; a segment whose words
    neither repeat in other episodes nor read as ad copy is live host speech
    over the music bed — move the edge past it (releasing a live segment
    always shrinks the cut, so this can only err toward keeping audio).
    """
    import re
    from processor.ad_language import is_ad_text
    from processor.script_repeat import text_repeats_elsewhere

    def is_live(text):
        t = text.strip()
        # whisper's non-speech annotations: "(upbeat music)", "♪ lyrics ♪"
        if not t or re.fullmatch(r"[\(\[♪].*[\)\]♪]", t, re.S):
            return False
        if strong_hits(t) or is_ad_text(t):
            return False
        # break formulas are spoken live by the hosts each episode even
        # though the words repeat across episodes — always keep them.
        # (A generic "short repeated text is live" rule instead of this
        # whitelist released prerecorded ad taglines at block edges.)
        if len(t.split()) <= 20 and _BREAK_FORMULA.search(t.lower()):
            return True
        return not text_repeats_elsewhere(t, other_shingles)

    overlapping = [seg for seg in tx
                   if seg[1] > r["start"] + 0.2 and seg[0] < r["end"] - 0.2]

    trimmed = 0.0
    for s, e, t in overlapping:
        if s >= r["end"] or trimmed >= max_trim:
            break
        if e - max(s, r["start"]) > 0.3 and is_live(t):
            trimmed += min(e, r["end"]) - r["start"]
            r["start"] = min(e, r["end"])
            r["method"] = _join_methods(r["method"], "talkover_trim")
        else:
            break
    trimmed = 0.0
    for s, e, t in reversed(overlapping):
        if e <= r["start"] or trimmed >= max_trim:
            break
        if min(e, r["end"]) - s > 0.3 and is_live(t):
            trimmed += r["end"] - max(s, r["start"])
            r["end"] = max(s, r["start"])
            r["method"] = _join_methods(r["method"], "talkover_trim")
        else:
            break


import re as _re

_BREAK_FORMULA = _re.compile(
    r"\b(we('| a)?re back|we are back|welcome back|back to the (show|episode)|"
    r"and now back to|when we (come|get) back|we('|w)?ll be (right )?back|"
    r"be right back|after (the|this) break|after these messages|"
    r"(go|going|let's go)( on| to)? a break|take a (quick )?break|"
    r"return for (a few )?more|right after this|"
    # guest-introduction formulas, spoken live over the theme music
    r"joins? (me|us) (now|today|here)|(he|she|they)('s| is| are)? (here )?with (me|us))\b")


def _snap_to_speech(r, tx, dur, snap_max=2.5):
    """Align cut edges with transcript speech boundaries."""
    def speech_overlaps(t0, t1):
        return any(s < t1 and e > t0 for s, e, _ in tx)

    if r["end"] < dur - 0.5:
        onsets = [s for s, _, _ in tx if abs(s - r["end"]) <= snap_max]
        if onsets:
            best = min(onsets, key=lambda s: (abs(s - r["end"]), s))
            if best < r["end"] or not speech_overlaps(r["end"], best):
                r["end"] = best
    if r["start"] > 0.5:
        ends = [e for _, e, _ in tx if abs(e - r["start"]) <= snap_max]
        if ends:
            best = min(ends, key=lambda e: (abs(e - r["start"]), -e))
            if best > r["start"] or not speech_overlaps(best, r["start"]):
                r["start"] = best


def _walk(tx, edge, limit, p, forward=True):
    """Extend an edge while lookahead windows keep containing ad language.

    Extends in whisper-segment steps: take the lookahead window's text; if it
    scores as ad copy, move the edge to the last (first) segment boundary
    inside the window whose cumulative text still scores as ad copy.
    """
    moved = edge
    budget = p["extend_cap_seconds"]
    look = p["extend_lookahead_seconds"]
    while budget > 0:
        if forward:
            w0, w1 = moved, min(moved + look, limit)
        else:
            w0, w1 = max(moved - look, limit), moved
        if w1 - w0 < 3.0:
            break
        window_text = text_between(tx, w0, w1)
        if not window_text or not is_ad_text(window_text):
            break
        # candidate boundaries: edges of segments that THEMSELVES carry a
        # strong marker (ad/promo units end with marker tails); a cumulative
        # score would let long ad runs dilute trailing content into the cut.
        if forward:
            bounds = sorted(e for s, e, t in tx if w0 < e <= w1 and strong_hits(t))
            best = None
            for b in bounds:
                chunk = text_between(tx, moved, b)
                if chunk and is_ad_text(chunk):
                    best = b
            if best is None or best <= moved + 1.0:
                break
            budget -= best - moved
            moved = best
        else:
            bounds = sorted((s for s, e, t in tx if w0 <= s < w1 and strong_hits(t)),
                            reverse=True)
            best = None
            for b in bounds:
                chunk = text_between(tx, b, moved)
                if chunk and is_ad_text(chunk):
                    best = b
            if best is None or best >= moved - 1.0:
                break
            budget -= moved - best
            moved = best
    return min(moved, limit) if forward else max(moved, limit)


def review_cuts(cuts, tx, evidence, dur, p, config, show=None, episode=None,
                other_shingles=None):
    """LLM tier (processor/llm_judge.py). Transcript segments inside a cut
    that are covered by audio repeats in >= confirm_below_matches other
    episodes are protected (audio evidence that strong is never overruled);
    every other segment must be labeled AD by the model or it is released --
    the cut shrinks, splits, or is dropped. Model unavailable -> cut kept."""
    from processor.llm_judge import label_segments
    strong_n = p["confirm_below_matches"]
    audio = [(a, b, o) for a, b, o, ber in evidence if ber is not None]

    def protected(s, e):
        return len({o for a, b, o in audio
                    if min(b, e) - max(a, s) > 0.5 * (e - s)}) >= strong_n

    out = []
    for c in cuts:
        s0, e0 = c["start"], c["end"]
        segs = [(a, b, t) for a, b, t in tx
                if min(b, e0) - max(a, s0) > 0.5 * max(b - a, 0.01)]
        if not segs:
            out.append(c)
            continue
        prot = [protected(max(a, s0), min(b, e0)) for a, b, _ in segs]
        if all(prot):
            out.append(c)
            continue
        before = [t for a, b, t in tx if b <= s0 + 0.5 and b > s0 - 60][-8:]
        after = [t for a, b, t in tx if a >= e0 - 0.5 and a < e0 + 60][:8]
        labels = label_segments(before, [t for _, _, t in segs], after, config,
                                show, episode)
        if labels is None:
            out.append(c)
            continue
        # textual veto: words that also occur in OTHER episodes are
        # prerecorded (ads re-air; show content is unique to its episode), so
        # the model may not release them -- it misreads dramatized ad skits
        # ("Dad, Disney activities are on Lingo Kids!") as show content.
        # Break formulas ("we're back") are live despite repeating.
        # Words repeated ELSEWHERE IN THIS EPISODE (outside this cut) are an
        # inserted promo too: a brand-new first-person trailer ("My brother
        # David was diagnosed...") aired 4x in one episode, existed in no
        # other episode yet, and the model read it as the host's story.
        from processor.script_repeat import text_repeats_elsewhere, word_shingle_set
        self_shingles = word_shingle_set(
            [(a, b, t) for a, b, t in tx if b <= s0 + 0.5 or a >= e0 - 0.5])

        def releasable(t):
            if len(t.split()) <= 20 and _BREAK_FORMULA.search(t.lower()):
                return True
            return not ((other_shingles and text_repeats_elsewhere(t, other_shingles))
                        or text_repeats_elsewhere(t, self_shingles))

        # The veto is judged on ~25 words of context, not per segment:
        # whisper segments are 2-8 words, and common phrases ("get people
        # out of the streets") then match other episodes by chance -- that
        # once vetoed a 64s live discussion back into a cut. The window
        # stays inside the run of segments the model called content.
        is_ad = [pr or lab == "AD" for pr, lab in zip(prot, labels)]
        content_runs, i = [], 0
        while i < len(segs):
            if is_ad[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(segs) and not is_ad[j + 1]:
                j += 1
            content_runs.append((i, j))
            i = j + 1
        veto = []
        for i0, j0 in content_runs:
            for k in range(i0, j0 + 1):
                lo = hi = k
                words = len(segs[k][2].split())
                while words < p["llm_veto_window_words"] and (lo > i0 or hi < j0):
                    if lo > i0:
                        lo -= 1
                        words += len(segs[lo][2].split())
                    if hi < j0 and words < p["llm_veto_window_words"]:
                        hi += 1
                        words += len(segs[hi][2].split())
                if not releasable(" ".join(t for _, _, t in segs[lo:hi + 1])):
                    veto.append(k)
        for k in veto:
            is_ad[k] = True
        # mirror guard: a short CONTENT island flanked by AD on both sides is
        # a misread inside an ad break, not show content worth a splice
        i = 0
        while i < len(segs):
            if is_ad[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(segs) and not is_ad[j + 1]:
                j += 1
            if 0 < i and j < len(segs) - 1 and \
                    segs[j][1] - segs[i][0] < p["llm_min_release_seconds"]:
                for k in range(i, j + 1):
                    is_ad[k] = True
            i = j + 1
        if all(is_ad):
            out.append(dict(c, method=_join_methods(c["method"], "llm")))
            continue
        # keep runs of AD segments; edges next to released content move to
        # segment STARTS (whisper segment ends bleed across music)
        i = 0
        while i < len(segs):
            if not is_ad[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(segs) and is_ad[j + 1]:
                j += 1
            s = s0 if i == 0 else segs[i][0]
            e = e0 if j == len(segs) - 1 else segs[j + 1][0]
            # a short unprotected AD fragment carved out of a cut the model
            # otherwise calls content is a misread sentence fragment (e.g.
            # "...a network sponsored by a community group"), not an ad:
            # real ads are not 7-second slivers of a news sentence.
            if e - s < p["llm_min_fragment_seconds"] and not any(prot[i:j + 1]):
                i = j + 1
                continue
            if e - s >= p["min_cut_seconds"]:
                out.append(dict(c, start=round(s, 2), end=round(e, 2),
                                method=_join_methods(c["method"], "llm_trim"),
                                excerpt=text_between(tx, s, e)[:300]))
            i = j + 1
        logger.info(f"LLM released content in cut {s0:.0f}-{e0:.0f}s: "
                    + "".join("A" if x else "c" for x in is_ad))
    return out
