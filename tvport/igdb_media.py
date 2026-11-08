"""Optional IGDB artwork for a local TVPort build; credentials and tokens stay in memory."""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API = 'https://api.igdb.com/v4/games'
TOKEN = 'https://id.twitch.tv/oauth2/token'
IMAGES = 'https://images.igdb.com/igdb/image/upload/t_1080p'
SWITCH = 130
MAX_IMAGE = 20 << 20
IMAGE_ID = re.compile(r'^[A-Za-z0-9_-]{1,80}$')


@dataclass(frozen=True)
class Media:
    icon: Path | None = None
    shelf: Path | None = None
    game_id: int | None = None


def normalized(name: str) -> str:
    text = unicodedata.normalize('NFKD', name.replace('™', '').replace('®', '')).casefold()
    return ''.join(ch for ch in text if ch.isalnum())


def request_json(url: str, *, data: bytes, headers: dict[str, str] | None = None) -> object:
    request = urllib.request.Request(url, data=data, headers=headers or {}, method='POST')
    with urllib.request.urlopen(request, timeout=12) as response:
        if response.status != 200:
            raise ValueError('IGDB request failed')
        return json.loads(response.read(1 << 20))


def token(client_id: str, client_secret: str) -> str:
    body = urllib.parse.urlencode({
        'client_id': client_id, 'client_secret': client_secret,
        'grant_type': 'client_credentials',
    }).encode()
    result = request_json(TOKEN, data=body,
                          headers={'Content-Type': 'application/x-www-form-urlencoded'})
    if not isinstance(result, dict) or not isinstance(result.get('access_token'), str):
        raise ValueError('IGDB authentication failed')
    return result['access_token']


def game_query(name: str, game_id: int | None) -> str:
    fields = 'fields id,name,game_type,platforms,artworks.image_id,artworks.width,artworks.height,screenshots.image_id,screenshots.width,screenshots.height;'
    if game_id is not None:
        return f'{fields} where id = {game_id}; limit 1;'
    # NACP names are untrusted query text. Search only their words and verify
    # the returned name/platform locally before choosing any image.
    words = ' '.join(re.findall(r'[A-Za-z0-9]+', name.replace('™', ' ')))[:120]
    if not words:
        raise ValueError('no searchable title')
    return f'{fields} search "{words}"; limit 20;'


def pick_game(rows: object, name: str, game_id: int | None) -> dict | None:
    if not isinstance(rows, list):
        return None
    if game_id is not None:
        return next((row for row in rows if isinstance(row, dict) and row.get('id') == game_id), None)
    wanted = normalized(name)
    matches = [row for row in rows if isinstance(row, dict)
               and normalized(str(row.get('name') or '')) == wanted
               and SWITCH in (row.get('platforms') or [])
               and row.get('game_type', 0) == 0]
    return matches[0] if len(matches) == 1 else None


def pick_image(rows: object, *, minimum_width: int, first: bool = False) -> str | None:
    if not isinstance(rows, list):
        return None
    eligible = [row for row in rows if isinstance(row, dict)
                and isinstance(row.get('width'), int) and isinstance(row.get('height'), int)
                and row['width'] >= minimum_width and row['height'] > 0
                and row['width'] / row['height'] >= 1.4
                and IMAGE_ID.fullmatch(str(row.get('image_id') or ''))]
    if not eligible:
        return None
    if not first:
        eligible.sort(key=lambda row: (row['width'] * row['height'],
                                       -abs(row['width'] / row['height'] - 16 / 9)), reverse=True)
    return str(eligible[0]['image_id'])


def valid_image(data: bytes, path: Path) -> bool:
    if not data.startswith((b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n')):
        return False
    path.write_bytes(data)
    result = subprocess.run(['sips', '-g', 'pixelWidth', '-g', 'pixelHeight', str(path)],
                            capture_output=True, text=True)
    if result.returncode or 'pixelWidth:' not in result.stdout or 'pixelHeight:' not in result.stdout:
        path.unlink(missing_ok=True)
        return False
    return True


def download(image_id: str, path: Path) -> bool:
    if not IMAGE_ID.fullmatch(image_id):
        return False
    request = urllib.request.Request(f'{IMAGES}/{image_id}.jpg')
    with urllib.request.urlopen(request, timeout=15) as response:
        final = urllib.parse.urlparse(response.url)
        if response.status != 200 or final.scheme != 'https' or final.hostname != 'images.igdb.com':
            return False
        data = response.read(MAX_IMAGE + 1)
    if len(data) > MAX_IMAGE:
        return False
    temporary = path.with_suffix('.part')
    if not valid_image(data, temporary):
        return False
    temporary.replace(path)
    return True


def cached(directory: Path, title: str, game_id: int | None) -> Media | None:
    try:
        saved = json.loads((directory / 'selection.json').read_text())
        if saved.get('title') != title or game_id is not None and saved.get('game_id') != game_id:
            return None
        icon = directory / 'icon.jpg' if saved.get('icon') else None
        shelf = directory / 'shelf.jpg' if saved.get('shelf') else None
        if icon and not icon.is_file() or shelf and not shelf.is_file():
            return None
        return Media(icon, shelf, saved.get('game_id'))
    except (OSError, ValueError, AttributeError):
        return None


def fetch(title: str, directory: Path, client_id: str, client_secret: str,
          game_id: int | None = None) -> Media:
    """Return cached or downloaded media; no match is an empty Media.

    Callers catch network/authentication errors and keep their existing art.
    """
    if saved := cached(directory, title, game_id):
        return saved
    if not client_id or not client_secret:
        return Media()
    bearer = token(client_id, client_secret)
    rows = request_json(API, data=game_query(title, game_id).encode(), headers={
        'Client-ID': client_id, 'Authorization': f'Bearer {bearer}',
        'Accept': 'application/json',
    })
    game = pick_game(rows, title, game_id)
    if not game:
        return Media()
    icon_id = pick_image(game.get('artworks'), minimum_width=800)
    shelf_id = pick_image(game.get('screenshots'), minimum_width=1280, first=True)
    directory.mkdir(parents=True, exist_ok=True)
    def get(image_id: str | None, filename: str) -> Path | None:
        if not image_id:
            return None
        path = directory / filename
        try:
            return path if download(image_id, path) else None
        except (OSError, ValueError):
            return None

    icon = get(icon_id, 'icon.jpg')
    shelf = get(shelf_id, 'shelf.jpg')
    result = Media(icon, shelf, game['id'])
    if icon or shelf:
        manifest = {'title': title, 'game_id': game['id'], 'icon': icon_id if icon else None,
                    'shelf': shelf_id if shelf else None}
        part = directory / 'selection.json.part'
        part.write_text(json.dumps(manifest, sort_keys=True))
        part.replace(directory / 'selection.json')
    return result
