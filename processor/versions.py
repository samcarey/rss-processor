"""Per-input-feed processor versions, stored in tracked feed_versions.yaml.

Lives in git (not the DB) so every bump is committed alongside the processing
change that caused it. Feeds absent from the file are v0.
"""
import os
from datetime import date

import yaml

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    'feed_versions.yaml')

HEADER = """\
# Processor version per input feed (keyed by the original feed URL).
# Served feeds are titled "(vX) <title>" and their artwork carries a blue
# badge with the version number. Optional per-feed `exclude_titles`: regexes
# (case-insensitive); matching episodes are never downloaded or served.
# Bump with scripts/bump_version.py whenever the way a feed is
# processed changes (detector/cutting changes that alter its output), then
# reprocess it. The current versions are stamped into every git commit by
# .githooks/prepare-commit-msg.
"""


def load():
    if not os.path.exists(PATH):
        return {}
    with open(PATH) as f:
        return yaml.safe_load(f) or {}


def save(data):
    with open(PATH, 'w') as f:
        f.write(HEADER)
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, width=200)


def excluded(feed_url, title, data=None):
    """True if the episode title matches one of the feed's exclude_titles
    regexes (case-insensitive). Excluded episodes are never inserted,
    downloaded, processed, or served."""
    import re
    pats = (data if data is not None else load()).get(feed_url, {}).get('exclude_titles') or []
    return any(re.search(p, title or '', re.I) for p in pats)


def get_version(feed_url):
    return int(load().get(feed_url, {}).get('version', 0))


def register(feed_url, name):
    """Record a newly subscribed feed at v0 (no-op if already present)."""
    data = load()
    if feed_url not in data:
        data[feed_url] = {'name': name, 'version': 0,
                          'history': [f"v0 {date.today()}: subscribed"]}
        save(data)


def bump(feed_url, name, note):
    data = load()
    entry = data.setdefault(feed_url, {'name': name, 'version': 0, 'history': []})
    entry['version'] = int(entry.get('version', 0)) + 1
    entry.setdefault('history', []).append(f"v{entry['version']} {date.today()}: {note}")
    save(data)
    return entry['version']


def summary(data):
    """'Name v1, Other v0' -- used for the Feed-Versions commit trailer."""
    return ", ".join(f"{v.get('name', k)} v{v.get('version', 0)}" for k, v in data.items())


if __name__ == '__main__':
    # Used by .githooks/prepare-commit-msg: reads feed_versions.yaml on stdin.
    import sys
    print(summary(yaml.safe_load(sys.stdin) or {}))
