"""IGDB opt-in: exact Switch matching, image choice, cache and safe fallback."""

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backend
import igdb_media


class IGDBMedia(unittest.TestCase):
    def test_exact_main_switch_game_or_explicit_id(self):
        rows = [
            {'id': 1, 'name': 'Super Mario Bros. Wonder', 'platforms': [130], 'game_type': 0},
            {'id': 2, 'name': 'Super Mario Bros. Wonder - Switch 2 Edition', 'platforms': [130], 'game_type': 0},
            {'id': 3, 'name': 'Super Mario Bros. Wonder', 'platforms': [167], 'game_type': 0},
        ]
        self.assertEqual((igdb_media.pick_game(rows, 'Super Mario Bros.™ Wonder', None) or {}).get('id'), 1)
        self.assertEqual((igdb_media.pick_game(rows, 'Super Mario Bros. Wonder', 2) or {}).get('id'), 2)
        self.assertIsNone(igdb_media.pick_game(rows + [rows[0]], 'Super Mario Bros. Wonder', None))
        self.assertIsNone(igdb_media.pick_game(rows, 'Another game', None))
        self.assertNotIn('"; DROP', igdb_media.game_query('Game"; DROP', None))

    def test_only_landscape_images_and_stable_selection(self):
        images = [
            {'image_id': 'portrait', 'width': 1000, 'height': 1500},
            {'image_id': 'wide', 'width': 1920, 'height': 1080},
            {'image_id': 'small', 'width': 800, 'height': 480},
            {'image_id': '../bad', 'width': 4096, 'height': 2160},
        ]
        self.assertEqual(igdb_media.pick_image(images, minimum_width=800), 'wide')
        self.assertEqual(igdb_media.pick_image(images, minimum_width=800, first=True), 'wide')
        self.assertIsNone(igdb_media.pick_image(images, minimum_width=2000))

    def test_fetch_reuses_cache_without_credentials_or_network(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            game = {'id': 42, 'name': 'Game', 'platforms': [130], 'game_type': 0,
                    'artworks': [{'image_id': 'wideart', 'width': 1920, 'height': 1080}],
                    'screenshots': [{'image_id': 'screen', 'width': 1920, 'height': 1080}]}
            calls = []
            def request(url, *, data, headers=None):
                calls.append((url, data, headers))
                return {'access_token': 'short-lived'} if url == igdb_media.TOKEN else [game]
            def download(image_id, path):
                path.write_bytes(b'picture')
                return True
            with patch.object(igdb_media, 'request_json', side_effect=request), patch.object(igdb_media, 'download', side_effect=download):
                first = igdb_media.fetch('Game', root, 'id', 'secret')
            self.assertEqual(first.game_id, 42)
            self.assertEqual(first.icon, root / 'icon.jpg')
            self.assertEqual(first.shelf, root / 'shelf.jpg')
            self.assertEqual(len(calls), 2)
            self.assertNotIn('secret', calls[0][0])
            self.assertEqual(igdb_media.fetch('Game', root, '', ''), first)
            self.assertIsNone(igdb_media.cached(root, 'Different Game', None))
            self.assertIsNone(igdb_media.cached(root, 'Game', 99))
            manifest = json.loads((root / 'selection.json').read_text())
            self.assertNotIn('secret', json.dumps(manifest))

    def test_backend_falls_back_when_igdb_is_unavailable(self):
        args = argparse.Namespace(art=None, shelf_art=None, art_scale=0, art_x=0, art_y=0,
                                  shelf_scale=0, shelf_x=0, shelf_y=0, fps=False, mode='handheld',
                                  texture_cap=512, players='', render_scale=1, no_thermal_cap=False,
                                  fps_target=0, swap_ab_xy=False, swap_ab=None, swap_xy=None,
                                  use_igdb_media=True, igdb_game_id=None)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'game'; source.mkdir()
            (source / 'Game [0100DBF01000A000][v0].nsp').touch()
            build = source / '0100DBF01000A000_build'; build.mkdir()
            (build / 'prepared-content.json').write_text(json.dumps({'package': 'v0', 'dlc': False}))
            icon = root / 'icon.jpg'; icon.write_bytes(b'icon')
            with patch.object(backend, 'DATA', root), patch.dict(backend.os.environ, {'TVPORT_IGDB_CLIENT_ID': 'id', 'TVPORT_IGDB_CLIENT_SECRET': 'secret'}), \
                 patch.object(backend, 'restore_tables'), patch.object(backend, 'write_env', return_value=root / 'env'), \
                 patch.object(backend, 'stream') as stream, patch.object(backend.igdb_media, 'fetch', side_effect=TimeoutError):
                game = backend.Game(source)
                with patch.object(type(game), 'icon', new_callable=lambda: property(lambda self: icon)):
                    backend.generate_project(game, args, 'TEST', 'test.game', sys.executable)
                env = stream.call_args.kwargs['env']
                self.assertEqual(env['DM_ICON'], str(icon))
                self.assertEqual(env['DM_TOP_SHELF_ART'], '')
                self.assertNotIn('TVPORT_IGDB_CLIENT_SECRET', env)


if __name__ == '__main__':
    unittest.main()
