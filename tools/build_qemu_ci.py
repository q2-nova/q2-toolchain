#!/usr/bin/env python3
"""Build and smoke-test the pinned Q2 QEMU user and system emulators on Ubuntu."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'q2/toolchain/qemu-ci.json'
BINARIES = ('qemu-mipsel', 'qemu-system-mipsel')
NAME = 'qemu-linux-x86_64.tar.gz'


def run(*args, **kwargs):
    print('+', *args, flush=True)
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def output(*args):
    return subprocess.check_output([str(arg) for arg in args], text=True).strip()


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def runtime_deps(executable):
    direct = []
    for line in output('ldd', executable).splitlines():
        if 'not found' in line:
            raise ValueError('missing QEMU runtime library')
        match = re.search(r'(/[^\s]+) \(0x[\da-f]+\)$', line)
        if not match:
            continue
        displayed = Path(match[1])
        actual = displayed.resolve()
        aliases = (actual, displayed, Path(str(actual).removeprefix('/usr')))
        for alias in dict.fromkeys(aliases):
            owners = subprocess.run(['dpkg-query', '-S', str(alias)], text=True, capture_output=True)
            if owners.returncode == 0:
                package = owners.stdout.splitlines()[0].split(': /', 1)[0]
                break
        else:
            raise ValueError(f'no runtime package documents {actual.name}')
        version = output('dpkg-query', '-W', '-f=${Version}', package)
        direct.append(dict(library=actual.name, package=package, version=version))
    if not direct:
        raise ValueError('failed to record runtime libraries')
    return sorted(direct, key=lambda item: item['library'])


def build(out, work):
    pin = json.loads(LOCK.read_text())
    src = work / 'qemu'
    obj = work / 'obj'
    staging = work / 'staging'
    pack = work / 'package'
    for path in (out, src, obj, staging, pack):
        path.mkdir(parents=True, exist_ok=False)
    run('git', 'init', '--quiet', src)
    run('git', '-C', src, 'fetch', '--quiet', '--depth=1', pin['repository'], pin['commit'])
    run('git', '-C', src, 'checkout', '--quiet', '--detach', 'FETCH_HEAD')
    if output('git', '-C', src, 'rev-parse', 'HEAD') != pin['commit']:
        raise ValueError('QEMU checkout differs from pin')
    configure = list(pin['configure']) + ['--target-list=' + ','.join(pin['targets'])]
    run(src / 'configure', *configure, cwd=obj)
    run('ninja', '-j2', *BINARIES, cwd=obj)
    run('ninja', '-j2', 'install', cwd=obj, env=dict(os.environ, DESTDIR=str(staging)))
    (pack / 'bin').mkdir()
    (pack / 'share').mkdir()
    for binary in BINARIES:
        shutil.copy2(staging / 'usr/bin' / binary, pack / 'bin' / binary)
    shutil.copytree(staging / 'usr/share/qemu', pack / 'share/qemu', symlinks=True)
    shutil.copy2(src / 'COPYING', pack / 'COPYING')
    shutil.copy2(src / 'LICENSE', pack / 'LICENSE')
    report = {
        'schema': 1, 'source_commit': pin['commit'], 'repository': pin['repository'],
        'recipe_commit': output('git', '-C', ROOT, 'rev-parse', 'HEAD'),
        'recipe_lock_sha256': sha(LOCK), 'runner': pin['runner'],
        'host_abi': output('getconf', 'GNU_LIBC_VERSION'), 'cc': output('cc', '--version').splitlines()[0],
        'packages': {package: output('dpkg-query', '-W', '-f=${Version}', package)
                     for package in pin['packages']},
        'configure': configure, 'build_jobs': 2,
        'files': {
            'bin/' + name: {
                'sha256': sha(pack / 'bin' / name),
                'version': output(pack / 'bin' / name, '--version').splitlines()[0],
                'runtime_dependencies': runtime_deps(pack / 'bin' / name),
            }
            for name in BINARIES
        },
    }
    (pack / 'qemu-artifact.json').write_text(json.dumps(report, indent=2) + '\n')
    timestamp = output('git', '-C', src, 'show', '-s', '--format=%ct', 'HEAD')
    archive = out / NAME
    run('tar', '--sort=name', '--mtime=@' + timestamp, '--owner=0', '--group=0',
        '--numeric-owner', '-C', pack, '-czf', archive, '.')
    with tempfile.TemporaryDirectory(prefix='qemu-relocate-', dir=work) as directory:
        moved = Path(directory)
        run('tar', '-xzf', archive, '-C', moved)
        for name in BINARIES:
            assert sha(moved / 'bin' / name) == report['files']['bin/' + name]['sha256']
            run(moved / 'bin' / name, '--version')
        if not re.search(r'^q2\s', output(moved / 'bin/qemu-system-mipsel', '-machine', 'help'), re.M):
            raise ValueError('portable system QEMU has no Q2 machine')
        source = moved / 'fixture.c'
        source.write_text('#include <stdio.h>\nint main(void) { puts("mips-user-ok"); }\n')
        run('mipsel-linux-gnu-gcc', '-static', source, '-o', moved / 'fixture')
        if output(moved / 'bin/qemu-mipsel', moved / 'fixture') != 'mips-user-ok':
            raise ValueError('portable QEMU did not run the MIPS user fixture')
    (out / (NAME + '.sha256')).write_text(f'{sha(archive)}  {NAME}\n')
    print('Both relocated QEMU binaries passed their smoke checks.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=lambda value: Path(value).resolve(), required=True)
    parser.add_argument('--work', type=lambda value: Path(value).resolve(), required=True)
    a = parser.parse_args()
    build(a.out, a.work)


if __name__ == '__main__':
    main()
