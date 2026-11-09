"""Show artwork with a blue processor-version-number badge, cached on disk."""
import io
import os

import requests
from PIL import Image, ImageDraw, ImageFont

from processor.rss_generator import resolve_storage_path

CACHE_DIR = resolve_storage_path('./data/images')
FONT = '/System/Library/Fonts/Supplemental/Arial Bold.ttf'
BLUE = (0, 122, 255)
# Bump when the badge's look changes: it's in the artwork URL (-s<N>.jpg) and cache
# filename, so podcast apps refetch without a processor-version bump.
BADGE_STYLE = 3


def _font(size):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default(size=size)


def stamp(img, label):
    img = img.convert('RGB')
    w, h = img.size
    side = min(w, h)
    font = _font(max(12, int(side * 0.13)))
    draw = ImageDraw.Draw(img)
    l, t, r, b = draw.textbbox((0, 0), label, font=font)
    tw, th = r - l, b - t
    pad_x, pad_y = int(th * 0.6), int(th * 0.45)
    margin = int(side * 0.04)
    bw, bh = tw + 2 * pad_x, th + 2 * pad_y
    x0, y0 = margin, h - margin - bh            # bottom-left corner
    draw.rounded_rectangle((x0, y0, x0 + bw, y0 + bh), radius=bh // 2, fill=BLUE,
                           outline=(255, 255, 255), width=max(2, side // 200))
    draw.text((x0 + pad_x - l, y0 + pad_y - t), label, font=font, fill=(255, 255, 255))
    return img


def badged_artwork_path(podcast, version):
    """Path to a JPEG of the podcast's artwork stamped with v<version>."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    out = os.path.join(CACHE_DIR, f"{podcast.id}-v{version}-s{BADGE_STYLE}.jpg")
    if os.path.exists(out):
        return out
    orig = os.path.join(CACHE_DIR, f"{podcast.id}-orig")
    if not os.path.exists(orig):
        r = requests.get(podcast.image_url, timeout=30,
                         headers={'User-Agent': 'rss-processor'})
        r.raise_for_status()
        with open(orig, 'wb') as f:
            f.write(r.content)
    with open(orig, 'rb') as f:
        img = Image.open(io.BytesIO(f.read()))
        img.load()
    tmp = out + '.tmp'
    stamp(img, str(version)).save(tmp, 'JPEG', quality=90)
    os.replace(tmp, out)
    return out
