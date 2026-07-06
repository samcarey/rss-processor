"""Transcript-based ad/promo language detection.

Podcast ads and cross-promos are extremely formulaic. Strong phrases are
near-certain markers ("promo code", "listen to ... on the iheartradio app");
weak keywords only contribute density. Scores are computed over transcript
text spans and used to (a) confirm single-match audio repeats, (b) bridge
gaps between confirmed ad cuts, (c) extend cut edges across unrepeated ads,
and (d) find standalone ad blocks when no repeat evidence exists.
"""
import re

STRONG_PHRASES = [
    # sponsor reads
    "support for the show comes from", "support for this show comes from",
    "support for this podcast comes from", "support for the podcast comes from",
    "brought to you by", "sponsored by", "paid for by", "is supported by",
    "promo code", "use code", "coupon code", "discount code", "offer code",
    "percent off", "free shipping", "free trial", "money back guarantee",
    "terms apply", "terms and conditions", "restrictions apply",
    "see terms", "offer ends", "new members only", "for a limited time",
    "available on amazon", "at participating", "while supplies last",
    "member finra", "not financial advice", "results may vary",
    "always drive responsibly", "for feature availability",
    "visit capella.edu", "cancel anytime", "no purchase necessary",
    # betting / age-gated ads
    "bonus bets", "responsible gaming", "gambling problem",
    "minimum odds", "21 and over", "must be 21", "void in ontario",
    "void where prohibited", "limited time offer", "new customers",
    # podcast promo intros
    "this week on",
    # podcast cross-promos
    "on the iheartradio app", "on the iheart radio app",
    "wherever you get your podcasts", "wherever you listen to podcasts",
    "listen and subscribe", "new episodes every", "an iheart original",
    "from iheart podcasts", "this is an iheart podcast",
    # network branding / outro
    "is a production of cool zone media", "for more podcasts from cool zone media",
    "check us out on the iheartradio app",
]

WEAK_KEYWORDS = [
    "subscribe", "download the app", "app store", "google play",
    "our website", "learn more at", "go to", "visit", "sign up",
    "apple podcasts", "spotify", "iheartradio", "episodes",
    "save", "deal", "offer", "discount", "delivery", "customers",
]

_DOTCOM = re.compile(r"\b[a-z0-9]+\s?\.\s?com\b|\bdot com\b")


def strong_hits(text):
    t = text.lower()
    hits = [p for p in STRONG_PHRASES if p in t]
    hits += _DOTCOM.findall(t)
    return hits


def ad_score(text):
    """Marker density per 100 words plus strong-hit count. Empirically, real
    ad copy scores >= 2 strong hits or density >= 3; content rarely exceeds
    one incidental hit (a host mentioning a website)."""
    t = text.lower()
    words = max(len(t.split()), 1)
    strong = strong_hits(text)
    weak = sum(1 for k in WEAK_KEYWORDS if k in t)
    density = (3.0 * len(strong) + weak) / words * 100.0
    return {"strong": len(strong), "weak": weak, "density": density,
            "hits": strong[:6]}


def is_ad_text(text, min_strong=1, min_density=1.5):
    """True if a span of transcript text reads like ad/promo copy."""
    s = ad_score(text)
    return s["strong"] >= min_strong and s["density"] >= min_density


def is_strong_ad_text(text):
    """Stricter test for cutting with no audio-repeat evidence at all."""
    s = ad_score(text)
    return s["strong"] >= 2 and s["density"] >= 2.0
