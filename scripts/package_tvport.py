#!/usr/bin/env python3
"""Pack TVPort.app, the Mac app in tvport/app, to run on any Mac without this checkout: it carries the engine and sets up the rest itself (tvport/README.md, Sharing TVPort).

    python3 scripts/package_tvport.py [--out FOLDER] [--uv PATH]

It writes FOLDER/TVPort.app and FOLDER/TVPort-<version>.zip (FOLDER: tvport/dist). The app carries:
  Contents/Resources/engine.tar.xz   the tracked files of dm/ and scripts/ and LICENSE, as they are in this checkout (uncommitted edits too; engine.json says so)
  Contents/Resources/engine.json     its ID (a digest of those files), commit and shader chain
  Contents/Resources/backend.py, igdb_media.py, requirements.txt   tvport/'s, which unpack the engine and make its Python on the first run
  Contents/Helpers/uv                which makes that Python
  Contents/Resources/Licenses/       nxnative's MIT notice, the engine's third-party licenses and uv's
Only tracked files go in, so nothing a build leaves in the checkout does: a profile's shader tables, extracted game files, keys, built apps. A tracked file that looks like one of
those stops the script. Needs Xcode, xcodegen, git and uv. Exit code: 0 done, 1 failed."""
import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = ('dm', 'scripts', 'LICENSE')
LEFT_OUT = ('dm/tools/fuzz/',)   # the fuzzers' inputs: made-up game files, which no build reads
GAME_SUFFIXES = {'.nsp', '.xci', '.nsz', '.xcz', '.nca', '.ncz', '.tik', '.keys', '.nso', '.nro', '.img', '.ipa', '.ips'}
GAME_NAMES = {'prod.keys', 'title.keys', 'dev.keys', 'control.nacp', 'main.npdm', 'c1_full.tsv', 'cb_extent.tsv', 'pso_user.tsv', 'tvport-shaders.tsv', 'tvport.json', 'tvport-env.txt'}
GAME_FOLDERS = re.compile(r'^([0-9A-Fa-f]{16}_build|.*-work|.*\.app|.*\.xcarchive|save-backups|save)$')


class Stop(Exception):
    pass


def run(argv, **kwargs) -> str:
    result = subprocess.run([str(a) for a in argv], text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise Stop(f'{Path(str(argv[0])).name} failed:\n' + (result.stderr.strip() or result.stdout.strip())[-2000:])
    return result.stdout


def unsafe(paths) -> list:
    """Paths that look like game files, keys, a game's build outputs or a built app: none belongs in the engine."""
    return [p for p in paths if Path(p).suffix.lower() in GAME_SUFFIXES or Path(p).name in GAME_NAMES or any(GAME_FOLDERS.match(part) for part in Path(p).parts[:-1])]


def engine_files() -> list:
    listed = run(['git', '-C', REPO, 'ls-files', '-z', '--', *ENGINE]).split('\0')
    files = sorted(p for p in listed if p and not p.startswith(LEFT_OUT) and (REPO / p).is_file())
    ignored = [p for p in run(['git', '-C', REPO, 'ls-files', '-z', '-ci', '--exclude-standard', '--', *ENGINE]).split('\0') if p]   # tracked although .gitignore says a build makes them
    bad = unsafe(files) + ignored
    if bad:
        raise Stop('These tracked files look like game files, keys or build outputs, so they stay out of the app. Remove them from git first:\n  ' + '\n  '.join(bad[:20]))
    return files


def digest(files: list) -> str:
    h = hashlib.sha256()
    for name in files:
        path = REPO / name
        h.update(f'{name}\0{os.access(path, os.X_OK)}\0'.encode())
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()[:12]


def uv_files(uv_arg) -> tuple:
    """uv and its two license texts, which Homebrew keeps two folders up from the binary."""
    uv = Path(uv_arg or shutil.which('uv') or '/opt/homebrew/bin/uv').resolve()
    if not uv.is_file():
        raise Stop('uv not found: brew install uv, or pass --uv')
    licenses = [uv.parent.parent / name for name in ('LICENSE-MIT', 'LICENSE-APACHE')]
    if not all(p.is_file() for p in licenses):
        raise Stop(f'uv\'s LICENSE-MIT and LICENSE-APACHE are not beside {uv}: the app must carry them with it')
    return uv, run([uv, '--version']).split()[1], licenses


def build_app(work: Path) -> Path:
    """The app built from tvport/app/project.yml in a project of its own, so the checkout's TVPort.xcodeproj and build folder stay as they are."""
    run(['xcodegen', 'generate', '-q', '--spec', REPO / 'tvport/app/project.yml', '--project', work])
    run(['xcodebuild', '-quiet', '-project', work / 'TVPort.xcodeproj', '-scheme', 'TVPort', '-configuration', 'Release', '-derivedDataPath', work / 'derived',
         f'INFOPLIST_FILE={REPO / "tvport/app/Info.plist"}',   # xcodegen writes it beside the spec, and the project looks beside itself
         'DEPLOYMENT_POSTPROCESSING=YES', 'build'])   # stripped: no paths of this Mac in the binary
    return work / 'derived/Build/Products/Release/TVPort.app'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=Path, default=REPO / 'tvport/dist')
    parser.add_argument('--uv', help='the uv binary to carry (default: the one on PATH)')
    args = parser.parse_args()
    try:
        files = engine_files()
        engine_id = digest(files)
        uv, uv_version, uv_licenses = uv_files(args.uv)
        commit = run(['git', '-C', REPO, 'rev-parse', '--short=12', 'HEAD']).strip()
        dirty = bool(run(['git', '-C', REPO, 'status', '--porcelain', '--untracked-files=no', '--', *ENGINE, 'tvport']).strip())
        chain_match = re.search(r'#define DMSH_CHAIN "(.*)"', (REPO / 'dm/gfx/nvn/dmsh_chain.h').read_text())
        if chain_match is None:
            raise RuntimeError('The engine shader chain header has no DMSH_CHAIN definition')
        chain = chain_match.group(1)
        print(f'engine {engine_id}: {len(files)} files from {commit}{" with uncommitted changes" if dirty else ""}, shader chain {chain}')
        args.out.mkdir(parents=True, exist_ok=True)
        app = args.out.resolve() / 'TVPort.app'
        with tempfile.TemporaryDirectory(prefix='tvport_') as work:
            work = Path(work)
            print('building the app')
            built = build_app(work)
            shutil.rmtree(app, ignore_errors=True)
            run(['ditto', built, app])
            resources, helpers = app / 'Contents/Resources', app / 'Contents/Helpers'
            resources.mkdir(exist_ok=True)
            print('packing the engine (about a minute)')
            listing = work / 'files'
            listing.write_text('\0'.join(files))
            run(['/usr/bin/tar', '-cJf', resources / 'engine.tar.xz', '--no-mac-metadata', '--uid', '0', '--gid', '0', '--uname', 'root', '--gname', 'wheel', '--null', '-T', listing],
                cwd=REPO, env=dict(os.environ, COPYFILE_DISABLE='1'))
        info = {'id': engine_id, 'commit': commit, 'dirty': dirty, 'chain': chain, 'files': len(files), 'uv': uv_version, 'made': datetime.now(timezone.utc).isoformat(timespec='seconds')}
        (resources / 'engine.json').write_text(json.dumps(info, indent=1) + '\n')
        for name in ('backend.py', 'igdb_media.py', 'save_import.py', 'requirements.txt'):
            shutil.copy2(REPO / 'tvport' / name, resources / name)
        licenses = resources / 'Licenses'
        licenses.mkdir()
        shutil.copy2(REPO / 'LICENSE', licenses / 'nxnative-LICENSE.txt')
        for name in files:
            if re.match(r'(LICENSE|PATENTS)', Path(name).name) and name.startswith(('dm/third_party/', 'dm/gfx/tools/spirv_cross_bin/')):
                (licenses / Path(name).parent).mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO / name, licenses / name)
        for path in uv_licenses:
            shutil.copy2(path, licenses / f'uv-{path.name}.txt')
        helpers.mkdir()
        shutil.copy2(uv, helpers / 'uv')
        (helpers / 'uv').chmod(0o755)
        plist = app / 'Contents/Info.plist'
        values = plistlib.loads(plist.read_bytes())
        values.pop('TVPortRoot', None)   # the checkout the app was built from: this one carries its engine instead
        plist.write_bytes(plistlib.dumps(values))
        run(['codesign', '--force', '--sign', '-', helpers / 'uv'])
        run(['codesign', '--force', '--sign', '-', app])
        run(['codesign', '--verify', '--strict', '--deep', app])
        leaks = [str(p.relative_to(app)) for p in (plist, app / 'Contents/MacOS/TVPort', resources / 'engine.json') if str(REPO).encode() in p.read_bytes()]
        if leaks:
            raise Stop(f'{", ".join(leaks)} still name this checkout ({REPO}): the app would not run elsewhere')
        archive = args.out.resolve() / f'TVPort-{values.get("CFBundleShortVersionString", "1.0")}.zip'
        archive.unlink(missing_ok=True)
        run(['ditto', '-c', '-k', '--keepParent', app, archive])
        size = lambda p: sum(f.stat().st_size for f in p.rglob('*') if f.is_file()) if p.is_dir() else p.stat().st_size
        print(f'{app} ({size(app) / 2**20:.0f} MB), {archive.name} ({size(archive) / 2**20:.0f} MB)')
    except Stop as stop:
        print(f'package_tvport: {stop}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
