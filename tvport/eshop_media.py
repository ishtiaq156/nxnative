"""Optional eShop banner lookup for backend builds, keyed by Switch title ID."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from igdb_media import MAX_IMAGE, valid_image

TITLE_LIST = 'https://raw.githubusercontent.com/blawar/titledb/master/US.en.json'
IMAGE_HOST = 'img-eshop.cdn.nintendo.net'
MARKS = [r'switch\W*2\b', 'edition', r'\+', 'membership', r'\bpass\b', 'bundle',
         r'\bpacks?\b', 'collection', 'ultimate', 'deluxe', r'\bplus\b',
         r'\blite\b', r'\bset\b', 'retail', 'upgrade', r'\bdemo\b', r'\btrial\b']


def allowed(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == 'https' and parsed.hostname == IMAGE_HOST


def pick_banner(entries: object, title_id: str) -> str | None:
    if not isinstance(entries, dict):
        return None
    products = [p for p in entries.values() if isinstance(p, dict)
                and str(p.get('id') or '').upper() == title_id.upper()
                and isinstance(p.get('bannerUrl'), str) and allowed(p['bannerUrl'])]
    if not products:
        return None

    def rank(product: dict) -> tuple[int, int, str]:
        name = str(product.get('name') or '')
        score = sum(bool(re.search(mark, name, re.I)) for mark in MARKS)
        score += 100 if product.get('isDemo') is True else 0
        try:
            nsu_id = int(product.get('nsuId'))
        except (TypeError, ValueError):
            nsu_id = 2**63 - 1
        return score, nsu_id, product['bannerUrl']

    return min(products, key=rank)['bannerUrl']


def fetch(title_id: str, directory: Path) -> Path | None:
    if not re.fullmatch(r'[0-9A-Fa-f]{16}', title_id):
        return None
    title_id = title_id.upper()
    path = directory / f'{title_id}.jpg'
    if path.is_file():
        return path
    # Reuse the Mac app's compact TitleDB index when present.
    index = directory / 'index.json'
    try:
        saved = json.loads(index.read_text()) if index.is_file() else {}
        url = saved.get('banners', {}).get(title_id) if saved.get('version') == 2 else None
    except (OSError, ValueError, TypeError):
        url = None
    if not url or not allowed(url):
        with urllib.request.urlopen(TITLE_LIST, timeout=90) as response:
            if response.status != 200:
                return None
            entries = json.loads(response.read(120 << 20))
        url = pick_banner(entries, title_id)
    if not url:
        return None
    with urllib.request.urlopen(url, timeout=30) as response:
        final = response.geturl()
        if response.status != 200 or not allowed(final):
            return None
        data = response.read(MAX_IMAGE + 1)
    if len(data) > MAX_IMAGE:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.part')
    if not valid_image(data, temporary):
        return None
    temporary.replace(path)
    return path
