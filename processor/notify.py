"""Tell podcast apps a served feed changed, so new/re-cut episodes show up
without waiting for the app's own (slow, popularity-based) feed polling.

Overcast: GET https://overcast.fm/ping?urlprefix=<prefix> recrawls every
feed it knows whose URL starts with the prefix (documented at
overcast.fm/podcasterinfo). The prefix contains the Funnel secret, which is
fine -- Overcast already holds the full feed URLs. Best-effort, never raises.
"""
import logging

import requests

logger = logging.getLogger(__name__)


def ping_feed_change(podcast_id=None):
    from processor.rss_generator import load_config
    base = (load_config().get("web") or {}).get("base_url", "").rstrip("/")
    if not base.startswith("https://"):
        return  # tailnet/local URL: no app server can reach it anyway
    prefix = f"{base}/feed/" + (f"{podcast_id}.xml" if podcast_id else "")
    try:
        r = requests.get("https://overcast.fm/ping", params={"urlprefix": prefix}, timeout=15)
        logger.info(f"Pinged Overcast for {'feed ' + str(podcast_id) if podcast_id else 'all feeds'}: HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"Overcast ping failed (non-fatal): {e}")
