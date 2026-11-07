"""NSP script with a dump folder (scripts/pack_dump.py): the folder's layout is read and packed into what the script's later steps read, and the script asks for no keys.
The run stops at the prerequisite check; no game files, keys or Xcode needed."""
import importlib.util
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import types
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'dm/tools'))
import romfs_image
spec = importlib.util.spec_from_file_location('build_ipa_from_nsp', REPO / 'scripts/build_ipa_from_nsp.py')
if spec is None or spec.loader is None: raise ImportError('scripts/build_ipa_from_nsp.py')
nsp = importlib.util.module_from_spec(spec); spec.loader.exec_module(nsp)
pack_dump = nsp.pack_dump

TID = 0x040074A01BF12000   # a game with a profile in dm/games, whose registered version is an update
VER = 458752


def npdm(tid):
    d = bytearray(0x100)
    d[:4] = b'META'
    struct.pack_into('<II', d, 0x70, 0x80, 0x20)
    d[0x80:0x84] = b'ACI0'
    struct.pack_into('<Q', d, 0x90, tid)
    return bytes(d)


def version(folder, tid=TID, main=b'code', marker='marker'):
    (folder / 'exefs').mkdir(parents=True); (folder / 'romfs/dir').mkdir(parents=True)
    (folder / 'exefs/main').write_bytes(main)
    (folder / 'exefs/main.npdm').write_bytes(npdm(tid))
    (folder / 'exefs/.listing').write_text('not a module')
    (folder / f'romfs/dir/{marker}').write_text('data')


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        self.dump = Path(td.name).resolve() / 'dump'
        version(self.dump / 'base', marker='old')
        version(self.dump / f'updates/{VER}', main=b'new code', marker='new')
        (self.dump / 'dlc/040074a01bf13001/0/romfs').mkdir(parents=True)
        (self.dump / 'dlc/040074a01bf13001/0/romfs/dlcfile').write_text('dlc')
        (self.dump / 'dlc/.listing').write_text('not a DLC')


class Scan(Base):
    def test_layout(self):
        self.assertEqual(pack_dump.scan(self.dump), (TID, {VER: self.dump / f'updates/{VER}'}, {TID + 0x1001: self.dump / 'dlc/040074a01bf13001/0/romfs'}))

    def test_base_alone(self):
        d = self.dump.parent / 'alone'; version(d / 'base')
        self.assertEqual(pack_dump.scan(d), (TID, {}, {}))

    def test_update_romfs_alone(self):   # the update's RomFS is whole, so a dump may leave the base's out; only packing the base itself then fails
        d = self.dump.parent / 'slim'; version(d / 'base'); version(d / f'updates/{VER}', main=b'new code', marker='new')
        shutil.rmtree(d / 'base/romfs')
        self.assertTrue(pack_dump.is_dump(d))
        self.assertEqual(pack_dump.scan(d), (TID, {VER: d / f'updates/{VER}'}, {}))
        with self.assertRaisesRegex(pack_dump.Error, 'no exefs/main'): pack_dump.pack_version(d / 'base', TID, d / 'out', say=lambda s: None)
        shutil.rmtree(d / f'updates/{VER}/romfs')
        self.assertFalse(pack_dump.is_dump(d))

    def test_update_alone(self):   # an update's folder is the whole game, so a dump may have no base/ (Fire Emblem Fortune's Weave)
        d = self.dump.parent / 'update'; version(d / f'updates/{VER}')
        self.assertTrue(nsp.dump_folder(d))
        self.assertEqual(pack_dump.scan(d), (TID, {VER: d / f'updates/{VER}'}, {}))
        app, fam = nsp.scan_dump(d)
        self.assertIsNone(fam['base'])
        self.assertEqual(nsp.nsp_versions(fam), {f'v{VER}': d / f'updates/{VER}'})

    def test_title_id_of_the_newest_whole_update(self):
        d = self.dump.parent / 'updates2'; version(d / f'updates/{VER}'); (d / f'updates/{VER + 1}/romfs').mkdir(parents=True)
        self.assertEqual(pack_dump.scan(d)[0], TID)

    def test_flat_folder_is_the_base_game(self):   # exefs/ and romfs/ directly in the folder, as nup extract writes them (issue #1265)
        d = self.dump.parent / 'flat'; version(d)
        self.assertTrue(nsp.dump_folder(d))
        self.assertEqual(pack_dump.scan(d), (TID, {}, {}))
        app, fam = nsp.scan_dump(d)
        self.assertEqual((app, fam['base']), (TID, d))
        self.assertEqual(nsp.nsp_versions(fam), {'v0': None})
        (d / 'base/exefs').mkdir(parents=True); (d / 'base/romfs').mkdir(); (d / 'base/exefs/main.npdm').write_bytes(npdm(TID + 0x2000))
        self.assertEqual(pack_dump.scan(d)[0], TID + 0x2000)   # base/ wins

    def test_updates_only(self):   # no base/: the game is named by the update's main.npdm, and the base is not offered
        d = self.dump.parent / 'nobase'; version(d / f'updates/{VER}', main=b'new code', marker='new')
        (d / 'dlc/040074a01bf13001/0/base/romfs').mkdir(parents=True)
        self.assertTrue(pack_dump.is_dump(d))
        self.assertFalse(pack_dump.has_base(d))
        self.assertEqual(pack_dump.scan(d), (TID, {VER: d / f'updates/{VER}'}, {TID + 0x1001: d / 'dlc/040074a01bf13001/0/base/romfs'}))
        app, fam = nsp.scan_dump(d)
        self.assertIsNone(fam['base'])
        self.assertEqual(nsp.nsp_versions(fam), {f'v{VER}': d / f'updates/{VER}'})
        with self.assertRaisesRegex(nsp.BuildError, 'no base game'): nsp.pick_nsps(fam, nsp.nsp_versions(fam), ['v0'])

    def test_switch2_edition_program(self):   # the update's code names a program of its own (Xenoblade Chronicles 2: 0400E95004038001): the game is the one its DLC belong to, or the one given
        prog = TID + 1
        d = self.dump.parent / 'ns2'; version(d / f'updates/{VER}', tid=prog)
        with self.assertRaisesRegex(pack_dump.Error, 'no DLC names the game'): pack_dump.scan(d)
        self.assertEqual(pack_dump.scan(d, TID)[0], TID)
        (d / 'dlc/040074a01bf13001/0/romfs').mkdir(parents=True)
        self.assertEqual(pack_dump.scan(d)[0], TID)
        self.assertEqual(nsp.scan_dump(d)[0], TID)
        pack_dump.pack_version(d / f'updates/{VER}', TID, d / 'out', say=lambda s: None)   # the application's own program, accepted
        self.assertTrue((d / 'out/RomFS.img').is_file())

    def test_program_folders(self):   # updates/<v>/program/<i>/: a Switch 2 Edition packs its Switch 2 program, --program another; program 0 names the title
        app, ns2 = 0x0100E95004038000, 0x0400E95004038001
        d = self.dump.parent / 'programs'
        version(d / 'base/program/0', tid=app, marker='ns1base')
        version(d / f'updates/{VER}/program/0', tid=app, marker='ns1')
        version(d / f'updates/{VER}/program/1', tid=ns2, marker='ns2')
        (d / 'dlc/0100e95004039001/196608/base/program/0/romfs').mkdir(parents=True)
        self.assertTrue(pack_dump.is_dump(d))
        tid, vers, dlc = pack_dump.scan(d)
        self.assertEqual((tid, vers), (app, {VER: d / f'updates/{VER}/program/1'}))
        self.assertEqual(dlc, {app + 0x1001: d / 'dlc/0100e95004039001/196608/base/program/0/romfs'})
        self.assertEqual(pack_dump.base_dir(d), d / 'base/program/0')   # the base has no Switch 2 program: its own is packed
        self.assertEqual(pack_dump.scan(d, program=0)[1], {VER: d / f'updates/{VER}/program/0'})
        self.assertIsNone(pack_dump.base_dir(d, 1))
        with self.assertRaisesRegex(pack_dump.Error, 'no program/2/'): pack_dump.scan(d, program=2)
        pack_dump.pack_version(vers[VER], app, d / 'out', say=lambda s: None)
        self.assertIn('dir/ns2', romfs_image.read_index(d / 'out/RomFS.img'))
        said = []
        with mock.patch.object(nsp, 'say', side_effect=said.append):
            fam = nsp.scan_dump(d)[1]
            self.assertIsNone(nsp.scan_dump(d, program=1)[1]['base'])
        self.assertIn(f'v{VER}: program 1 ({ns2:016X}, the Switch 2 Edition)', said)
        self.assertEqual(sorted(nsp.nsp_versions(fam)), ['v0', f'v{VER}'])

    def test_multi_program_packs_program_0(self):   # programs all of one platform (a launcher and its games): program 0 unless asked
        d = self.dump.parent / 'multi'
        for i in range(3): version(d / f'base/program/{i}', tid=TID + i)
        self.assertEqual(pack_dump.base_dir(d), d / 'base/program/0')
        self.assertEqual(pack_dump.base_dir(d, 2), d / 'base/program/2')
        self.assertEqual(pack_dump.scan(d)[0], TID)

    def test_dlc_update_wins(self):   # dlc/<id>/<n>/updates/<version>/romfs is whole: the newest is packed rather than the DLC's base
        d = self.dump / 'dlc/040074a01bf13002/0'
        for r in ('base/romfs', 'updates/65536/romfs', 'updates/131072/romfs'): (d / r).mkdir(parents=True)
        self.assertEqual(pack_dump.scan(self.dump)[2][TID + 0x1002], d / 'updates/131072/romfs')

    def test_refuses(self):
        (self.dump / 'dlc/0100000000000000/romfs').mkdir(parents=True)
        with self.assertRaisesRegex(pack_dump.Error, 'is not a DLC of'): pack_dump.scan(self.dump)
        d = self.dump.parent / 'patch'; version(d / 'base', tid=TID + 0x800)
        with self.assertRaisesRegex(pack_dump.Error, 'not a base title ID'): pack_dump.scan(d)
        with self.assertRaisesRegex(pack_dump.Error, 'no main.npdm'): pack_dump.scan(self.dump.parent)
        shutil.rmtree(self.dump / 'dlc/0100000000000000')
        (self.dump / 'dlc/040074a01bf13001/1/romfs').mkdir(parents=True)
        with self.assertRaisesRegex(pack_dump.Error, 'expected one'): pack_dump.scan(self.dump)

    def test_packages_win_over_the_layout(self):
        self.assertTrue(nsp.dump_folder(self.dump))
        (self.dump / 'game.nsp').write_bytes(b'x')
        self.assertFalse(nsp.dump_folder(self.dump))

    def test_appledouble_is_no_package(self):
        (self.dump / '._game.nsp').write_bytes(b'x')
        self.assertTrue(nsp.dump_folder(self.dump))


class Pack(Base):
    def out(self): return self.dump / 'out'

    def test_update_packed_on_its_own(self):
        pack_dump.pack_version(self.dump / f'updates/{VER}', TID, self.out(), say=lambda s: None)
        self.assertEqual((self.out() / 'ExeFS/main').read_bytes(), b'new code')
        self.assertFalse((self.out() / 'ExeFS/.listing').exists())
        names = romfs_image.read_index(self.out() / 'RomFS.img')
        self.assertIn('dir/new', names)
        self.assertNotIn('dir/old', names)   # an update's RomFS is the patched tree, never laid over the base

    def test_other_title_refused(self):
        with self.assertRaisesRegex(pack_dump.Error, 'the base is'): pack_dump.pack_version(self.dump / f'updates/{VER}', TID + 0x2000, self.out(), say=lambda s: None)

    def test_repeat_keeps_what_is_unchanged(self):
        src = self.dump / f'updates/{VER}'
        pack_dump.pack_version(src, TID, self.out(), say=lambda s: None)
        main, img = self.out() / 'ExeFS/main', self.out() / 'RomFS.img'
        before = main.stat().st_ino, img.stat().st_mtime_ns
        pack_dump.pack_version(src, TID, self.out(), say=lambda s: None)
        self.assertEqual((main.stat().st_ino, img.stat().st_mtime_ns), before)
        (src / 'exefs/main').write_bytes(b'patched code')
        pack_dump.pack_version(src, TID, self.out(), say=lambda s: None)
        self.assertEqual(main.read_bytes(), b'patched code')

    def test_dlc_images_follow_the_folder(self):
        aoc = self.out() / 'aoc'
        aoc.mkdir(parents=True); (aoc / '7.img').write_bytes(b'gone'); (aoc / '7.img.inputs.json').write_text('{}')
        _, _, dlc = pack_dump.scan(self.dump)
        pack_dump.pack_dlc(TID, dlc, aoc, say=lambda s: None)
        self.assertEqual(sorted(p.name for p in aoc.iterdir()), ['1.img', '1.img.inputs.json'])
        self.assertIn('dlcfile', romfs_image.read_index(aoc / '1.img'))


class Stop(Exception): pass


class Main(Base):
    def run_main(self, *opts):
        args = nsp.parse_args([str(self.dump), *opts])
        seen, said = {}, []
        def stop(env, *a): seen.update(env); raise Stop()
        with mock.patch.object(nsp, 'find_repo', return_value=REPO), mock.patch.object(nsp, 'configure_dotnet'), mock.patch.object(nsp, 'WORK_DIR', ''), mock.patch.object(nsp, 'PROD_KEYS', ''), \
             mock.patch.object(nsp, 'check_prereqs', side_effect=stop), mock.patch.object(nsp, 'say', side_effect=said.append), \
             mock.patch.object(nsp, 'ask', side_effect=AssertionError('asked for something')), mock.patch.object(nsp, 'package_reader', side_effect=AssertionError('read a package')):
            with self.assertRaises(Stop): nsp.main(args)
        return Path(seen['DM_ROOT']), said

    def test_needs_no_keys_and_builds_beside_the_dump(self):
        work, said = self.run_main()
        self.assertEqual(work, self.dump / f'{TID:016X}_build')
        self.assertTrue(any(s.startswith('a dump folder: ') for s in said), said)

    def test_updates_only(self):
        shutil.rmtree(self.dump / 'base')
        work, said = self.run_main()
        self.assertEqual(work, self.dump / f'{TID:016X}_build')
        self.assertFalse(any(s.startswith('base: ') for s in said), said)

    def test_title_id_must_match(self):
        with mock.patch.object(nsp, 'find_repo', return_value=REPO), mock.patch.object(nsp, 'configure_dotnet'), self.assertRaisesRegex(nsp.BuildError, 'which holds'):
            nsp.main(nsp.parse_args([str(self.dump), '--title-id', '0100000000010000']))

    def mixed_programs(self):
        app, edition = 0x010015100B514000, 0x040015100B514001
        d = self.dump.parent / 'mixed'
        version(d / 'base/program/0', tid=app, marker='original-base')
        version(d / f'updates/{VER}/program/0', tid=app, marker='original-update')
        version(d / f'updates/{VER}/program/1', tid=edition, marker='edition-update')
        return d, app, edition

    def test_selected_dump_profile_uses_requested_version(self):
        d, app, edition = self.mixed_programs()
        got_app, fam = nsp.scan_dump(d)
        self.assertEqual(got_app, app)   # update/DLC application identity stays original
        self.assertEqual(nsp.selected_dump_program(fam), edition)
        self.assertEqual(nsp.selected_dump_program(fam, f'v{VER}'), edition)
        self.assertEqual(nsp.selected_dump_program(fam, 'v0'), app)
        self.assertIsNone(nsp.selected_dump_program(fam, 'v999999'))

    def test_explicit_v0_uses_original_profile_not_latest_edition(self):
        d, app, edition = self.mixed_programs()
        sys.path.insert(0, str(REPO / 'dm/games')); import game
        actual = game.by_title
        edition_profile = types.SimpleNamespace(id=f'{edition:016X}', title_id=edition, builds=dict, words=lambda key: [])
        said = []
        for package, expected in (('v0', app), (f'v{VER}', edition), (None, edition)):
            said.clear()
            args = [str(d), '--platform', 'mac-app'] + (['--package', package] if package else [])
            with mock.patch.object(nsp, 'find_repo', return_value=REPO), mock.patch.object(nsp, 'configure_dotnet'), \
                 mock.patch.object(nsp, 'WORK_DIR', ''), mock.patch.object(nsp, 'PROD_KEYS', ''), \
                 mock.patch.object(game, 'by_title', side_effect=lambda tid: edition_profile if tid == edition else actual(tid)), \
                 mock.patch.object(nsp, 'nsp_versions', side_effect=Stop()), mock.patch.object(nsp, 'say', side_effect=said.append), \
                 self.assertRaises(Stop):
                nsp.main(nsp.parse_args(args))
            self.assertIn(f'game profile: {expected:016X}', said, f'--package {package}: {said}')

    def test_missing_edition_profile_surveys_edition_not_original(self):
        d, app, edition = self.mixed_programs()   # the Switch 1 profile exists, but the Edition's does not
        with mock.patch.object(nsp, 'find_repo', return_value=REPO), mock.patch.object(nsp, 'configure_dotnet'), \
             mock.patch.object(nsp, 'WORK_DIR', ''), mock.patch.object(nsp, 'PROD_KEYS', ''), \
             mock.patch.object(nsp, '_importable', return_value=True), mock.patch.object(nsp, 'check_capstone'), \
             mock.patch.object(nsp.shutil, 'which', return_value='/usr/bin/dotnet'), \
             mock.patch.object(nsp, 'same_build', side_effect=AssertionError('must not alias another program')), \
             mock.patch.object(nsp, 'survey', side_effect=Stop()) as survey, self.assertRaises(Stop):
            nsp.main(nsp.parse_args([str(d), '--platform', 'mac-app']))
        self.assertEqual(survey.call_args.args[1], edition)

    def test_provisional_edition_profile_has_its_own_title_id(self):
        d, app, edition = self.mixed_programs()
        with mock.patch.object(nsp, 'find_repo', return_value=REPO), mock.patch.object(nsp, 'configure_dotnet'), \
             mock.patch.object(nsp, 'WORK_DIR', ''), mock.patch.object(nsp, 'PROD_KEYS', ''), \
             mock.patch.object(nsp, '_importable', return_value=True), mock.patch.object(nsp, 'check_capstone'), \
             mock.patch.object(nsp.shutil, 'which', return_value='/usr/bin/dotnet'), \
             mock.patch.object(nsp, 'survey', return_value={}) as survey, \
             mock.patch.object(nsp, 'write_profile', side_effect=Stop()) as writer, self.assertRaises(Stop):
            nsp.main(nsp.parse_args([str(d), '--platform', 'mac-app', '--provisional']))
        self.assertEqual(survey.call_args.args[1], edition)
        self.assertEqual(writer.call_args.args[1], edition)
        self.assertEqual(writer.call_args.args[0].name, f'{edition:016X}')
        self.assertEqual(writer.call_args.args[2]['survey'], str(d / f'{edition:016X}_survey/survey.md'))

    def test_dump_survey_obeys_explicit_base_package(self):
        d, app, edition = self.mixed_programs()
        _, fam = nsp.scan_dump(d)
        for request, expected in (('v0', d / 'base/program/0'), (f'v{VER}', d / f'updates/{VER}/program/1')):
            with mock.patch.object(nsp, 'survey_dump', side_effect=Stop()) as survey_dump, self.assertRaises(Stop):
                nsp.survey(d, app, fam, None, REPO, sys.executable, {}, package=request)
            self.assertEqual(survey_dump.call_args.args[0], expected)

    def surveyed(self, d):   # the real survey on a dump with no profile: this NPDM is a 32-bit program's, which stops it right after the code modules and the manifest
        with mock.patch.object(nsp, 'find_repo', return_value=REPO), mock.patch.object(nsp, 'configure_dotnet'), mock.patch.object(nsp, 'WORK_DIR', ''), mock.patch.object(nsp, 'PROD_KEYS', ''), \
             mock.patch.object(nsp, 'check_capstone'), mock.patch.object(nsp.shutil, 'which', return_value='/usr/bin/dotnet'), mock.patch.object(nsp, 'say'), \
             mock.patch.object(nsp, 'build_nx_extract', side_effect=AssertionError('built nx_extract')), mock.patch.object(nsp, 'ask', side_effect=AssertionError('asked for something')), \
             self.assertRaisesRegex(nsp.BuildError, rf'{d.name} \(0400000000000000\) is a 32-bit'):
            nsp.main(nsp.parse_args([str(d)]))
        self.assertTrue((d / '0400000000000000_survey/extract/ExeFS/main').is_file())

    def test_no_profile_surveyed(self):   # it used to stop: the survey read NSPs only. The folder names the game, as a dump has no control data
        d = self.dump.parent / 'unknown'; version(d / 'base', tid=0x0400000000000000); self.surveyed(d)

    def test_update_alone_surveyed(self):
        d = self.dump.parent / 'update'; version(d / f'updates/{VER}', tid=0x0400000000000000); self.surveyed(d)

    def test_flat_folder_surveyed(self):
        d = self.dump.parent / 'flat'; version(d, tid=0x0400000000000000); self.surveyed(d)


class Survey(Base):
    def test_files_as_the_manifest_pass_leaves_them(self):
        src = self.dump.parent / 'v'; version(src, marker='a.bin')
        nro = b'\0' * 16 + b'NRO0' + b'x' * 300
        (src / 'romfs/nro').mkdir(); (src / 'romfs/nro/m.NRO').write_bytes(nro)
        out = self.dump.parent / 'out'
        self.assertEqual(nsp.survey_dump(src, out), 2)
        self.assertEqual((out / 'ExeFS/main').read_bytes(), b'code')
        self.assertFalse((out / 'ExeFS/.listing').exists())
        rows = (out / 'romfs_manifest.tsv').read_text().splitlines()
        self.assertEqual(rows[0], nsp.MANIFEST_HEAD.rstrip('\n'))
        self.assertEqual(sorted(rows[1:]), sorted([f'/dir/a.bin\t4\t{b"data".hex()}', f'/nro/m.NRO\t{len(nro)}\t{nro[:32].hex()}']))
        self.assertEqual((out / 'romfs_nro/nro/m.NRO').read_bytes(), nro)

    def test_a_version_without_code_or_romfs_stops(self):
        src = self.dump.parent / 'v'; (src / 'romfs').mkdir(parents=True)
        with self.assertRaisesRegex(nsp.BuildError, 'no exefs/main.npdm and romfs'): nsp.survey_dump(src, self.dump.parent / 'out')

    def test_same_build_reads_main_from_the_folders(self):   # a known build under another title ID: the newest update's main, then the base game's; an update with no code is passed over
        def nso(bid): return b'NSO0' + bytes(0x3c) + bytes.fromhex(bid) + bytes(0x50)
        d = self.dump.parent / 'alias'; version(d / 'base', main=nso('ab' * 16)); version(d / f'updates/{VER}', main=nso('cd' * 16))
        app, fam = nsp.scan_dump(d)
        seen, prof = [], mock.Mock(id='P', builds=lambda: {'v0': {'main': 'ab' * 16}})
        games = mock.Mock(by_build=lambda bid: seen.append(bid) or (prof if bid == 'ab' * 16 else None))
        with mock.patch.dict(sys.modules, {'game': games}), mock.patch.object(nsp, 'PROG', mock.Mock()), mock.patch.object(nsp, 'build_nx_extract', side_effect=AssertionError('built nx_extract')):
            self.assertEqual(nsp.same_build(d, app, fam, None, REPO, {}), (prof, 'v0', 'ab' * 16))
            self.assertEqual(seen, ['cd' * 16, 'ab' * 16])
            shutil.rmtree(d / f'updates/{VER}/exefs'); seen.clear()
            self.assertEqual(nsp.same_build(d, app, fam, None, REPO, {}), (prof, 'v0', 'ab' * 16))
            self.assertEqual(seen, ['ab' * 16])


if __name__ == '__main__':
    unittest.main()
