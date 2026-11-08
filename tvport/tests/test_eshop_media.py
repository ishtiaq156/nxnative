"""The backend eShop option chooses the base product and falls back safely."""

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backend
import eshop_media


class EshopMedia(unittest.TestCase):
    def test_base_product_wins_over_switch_2_edition(self):
        prefix = 'https://img-eshop.cdn.nintendo.net/i/'
        products = {
            'new': {'id': '010015100B514000', 'name': 'Super Mario Bros. Wonder Nintendo Switch 2 Edition',
                    'nsuId': 2, 'bannerUrl': prefix + 'new.jpg'},
            'base': {'id': '010015100B514000', 'name': 'Super Mario Bros. Wonder',
                     'nsuId': 1, 'bannerUrl': prefix + 'base.jpg'},
            'foreign': {'id': '0100000000000000', 'name': 'Other', 'bannerUrl': prefix + 'other.jpg'},
        }
        self.assertEqual(eshop_media.pick_banner(products, '010015100b514000'), prefix + 'base.jpg')
        products['base']['bannerUrl'] = 'http://untrusted.example/base.jpg'
        self.assertEqual(eshop_media.pick_banner(products, '010015100B514000'), prefix + 'new.jpg')

    def test_backend_uses_eshop_banner_and_explicit_art_wins(self):
        args = argparse.Namespace(art=None, shelf_art=None, art_scale=0, art_x=0, art_y=0,
                                  shelf_scale=0, shelf_x=0, shelf_y=0, fps=False, mode='handheld',
                                  texture_cap=512, players='', render_scale=1, no_thermal_cap=False,
                                  fps_target=0, swap_ab_xy=False, swap_ab=None, swap_xy=None,
                                  use_eshop_artwork=True, use_igdb_media=False, igdb_game_id=None)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'game'; source.mkdir()
            (source / 'Game [0100DBF01000A000][v0].nsp').touch()
            build = source / '0100DBF01000A000_build'; build.mkdir()
            (build / 'prepared-content.json').write_text(json.dumps({'package': 'v0', 'dlc': False}))
            banner = root / 'banner.jpg'; banner.write_bytes(b'banner')
            custom = root / 'custom.jpg'; custom.write_bytes(b'custom')
            with patch.object(backend, 'DATA', root), patch.object(backend, 'restore_tables'), \
                 patch.object(backend, 'write_env', return_value=root / 'env'), \
                 patch.object(backend, 'stream') as stream, \
                 patch.object(backend.eshop_media, 'fetch', return_value=banner) as fetch:
                game = backend.Game(source)
                backend.generate_project(game, args, 'TEST', 'test.game', sys.executable)
                self.assertEqual(stream.call_args.kwargs['env']['DM_ICON'], str(banner))
                self.assertEqual(fetch.call_args.args[0], game.artwork_title_id)
                args.art = custom
                backend.generate_project(game, args, 'TEST', 'test.game', sys.executable)
                self.assertEqual(stream.call_args.kwargs['env']['DM_ICON'], str(custom))
                self.assertEqual(fetch.call_count, 1)


if __name__ == '__main__':
    unittest.main()
