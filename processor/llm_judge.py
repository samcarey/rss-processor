"""LLM adjudication tier for ad cuts (local Ollama model).

The lexical markers in ad_language.py are a candidate generator, not a
judge: content that discusses websites or sponsorships ("posting on x.com",
"a network sponsored by a community group") trips them. This tier reads the
transcript around each candidate cut the way a human reviewer would and
splits it into blocks (sponsor_ad / show_promo / network_branding /
show_content), which map to per-segment AD/CONTENT labels.

Policy (applied in ad_detector.review_cuts):
  * segments backed by strong audio evidence (repeated in
    >= confirm_below_matches other episodes) are never overruled;
  * other segments the model labels CONTENT are released: the cut shrinks,
    splits, or disappears;
  * if the model is unavailable or answers garbage, the cut is kept as-is
    (lexical rules already passed it) -- detection degrades, never blocks.

Verdicts are cached on disk keyed by (model, prompt), so reprocessing is
deterministic and cheap.
"""
import hashlib
import json
import logging
import os

import requests

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"

SYSTEM = """You review podcast transcripts for an ad-removal tool. Ads and \
promos were inserted into the episode audio. Your job: split a passage into \
consecutive blocks and say what each block is, so ad material can be cut \
without ever cutting the show itself.

Block kinds:
- sponsor_ad: a paid ad or sponsor read, including one read by the host \
("support for the show comes from", product pitches, offer codes, legal \
disclaimers, drug side-effect lists).
- show_promo: a trailer or promo for a DIFFERENT podcast/show. Trailers \
often open with dramatic clips of other voices, introduce a host ("I'm \
Bridget Armstrong..."), describe a season, and end with "listen to <show> on \
the iHeartRadio app / Apple Podcasts / wherever you get your podcasts".
- network_branding: station IDs and boilerplate ("This is an iHeart \
podcast", "<show> is a production of Cool Zone Media", production credits).
- show_content: the episode itself -- discussion, reporting, interviews, \
narration, quoted clips, the hosts' own intro, banter and "we're back" \
transitions. Content that merely TALKS ABOUT companies, websites, \
sponsorships, advertising, or other media is show_content.

Whisper transcription errors are common; read past them."""

KINDS = ["sponsor_ad", "show_promo", "network_branding", "show_content"]


def _cache_path(config, key):
    from processor.rss_generator import resolve_storage_path
    base = resolve_storage_path(config.get("storage", {}).get("base_path", "./data"))
    d = os.path.join(base, "llm_cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, key + ".json")


def build_prompt(before, inside, after, show=None, episode=None):
    """before/after: list of str (context); inside: list of str (numbered)."""
    lines = []
    if show:
        lines.append(f'This episode is from the podcast "{show}"'
                     + (f', episode "{episode}"' if episode else "") + ". "
                     "Anything promoting a DIFFERENT podcast or show is "
                     "show_promo, even when it sounds like interviews or "
                     "reporting.\n")
    lines.append("CONTEXT BEFORE (for reference only):")
    lines += [f"  {t}" for t in before] or ["  (start of episode)"]
    lines.append("\nPASSAGE (numbered segments):")
    lines += [f"  [{i + 1}] {t}" for i, t in enumerate(inside)]
    lines.append("\nCONTEXT AFTER (for reference only):")
    lines += [f"  {t}" for t in after] or ["  (end of episode)"]
    lines.append(
        f"\nSplit segments 1..{len(inside)} into consecutive blocks covering all "
        "of them, in order. For each block give its first and last segment "
        "number, its kind, and a few words saying what it is (e.g. 'ad for "
        "Odoo', 'trailer for podcast Curse of Miss America', 'hosts discuss "
        "Musk and X').")
    return "\n".join(lines)


def _ranges(nums):
    out, start = [], None
    for i, x in enumerate(nums):
        if start is None:
            start = x
        if i + 1 == len(nums) or nums[i + 1] != x + 1:
            out.append(str(start) if start == x else f"{start}-{x}")
            start = None
    return ", ".join(out)


def _chat(messages, model, think, schema, p):
    r = requests.post(OLLAMA_URL, timeout=float(p.get("timeout_seconds", 900)), json={
        "model": model,
        "messages": messages,
        "format": schema,
        "think": think,
        "stream": False,
        "keep_alive": "30m",
        "options": {"temperature": 0, "seed": 0,
                    "num_ctx": int(p.get("num_ctx", 16384))},
    })
    r.raise_for_status()
    return r.json()["message"]


def label_segments(before, inside, after, config, show=None, episode=None):
    """Returns list of 'AD'/'CONTENT' (len(inside)), or None if unavailable."""
    p = config.get("llm_judge", {}) or {}
    model = p.get("model", "qwen3:30b-a3b")
    think = bool(p.get("think", True))
    prompt = build_prompt(before, inside, after, show, episode)
    key = hashlib.sha256(json.dumps([model, think, SYSTEM, prompt]).encode()).hexdigest()[:32]
    cpath = _cache_path(config, key)
    if os.path.exists(cpath):
        with open(cpath) as f:
            cached = json.load(f)
        return cached["labels"]

    schema = {
        "type": "object",
        "properties": {"blocks": {"type": "array", "items": {
            "type": "object",
            "properties": {"first": {"type": "integer"}, "last": {"type": "integer"},
                           "kind": {"type": "string", "enum": KINDS},
                           "what": {"type": "string"}},
            "required": ["first", "last", "kind", "what"]}}},
        "required": ["blocks"],
    }
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt}]
    n = len(inside)
    labels = [None] * n
    blocks, thinking = [], ""
    try:
        # The model sometimes stops a segment or few short of the end. Ask it
        # to finish the uncovered segments rather than discarding the verdict.
        for attempt in range(3):
            msg = _chat(messages, model, think, schema, p)
            thinking += msg.get("thinking", "")
            out = json.loads(msg["content"])
            for blk in out["blocks"]:
                lo, hi = max(1, int(blk["first"])), min(n, int(blk["last"]))
                if lo > hi:
                    continue
                blocks.append(blk)
                lab = "CONTENT" if blk["kind"] == "show_content" else "AD"
                for i in range(lo - 1, hi):
                    if labels[i] is None:
                        labels[i] = lab
            missing = [i + 1 for i, l in enumerate(labels) if l is None]
            if not missing:
                break
            messages += [{"role": "assistant", "content": msg["content"]},
                         {"role": "user", "content":
                          f"Segments {_ranges(missing)} are not covered by any "
                          "block. Return blocks covering exactly those segments."}]
        else:
            raise ValueError(f"blocks don't cover segments {_ranges(missing)} of {n}: {blocks}")
    except Exception as e:
        logger.warning(f"LLM judge unavailable ({e}); keeping lexical verdict")
        return None

    with open(cpath, "w") as f:
        json.dump({"model": model, "prompt": prompt, "labels": labels,
                   "blocks": blocks,
                   "thinking": thinking}, f, indent=1)
    logger.info("LLM judge: " + "; ".join(
        f"{b['first']}-{b['last']} {b['kind']}: {b['what']}" for b in blocks))
    return labels
