#!/usr/bin/env python3
"""Package, qualify and verify an exact X2000 compiler installation."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

import x2000_toolchain as toolchain

ROOT = Path(__file__).resolve().parents[1]
NIGHTLY = toolchain.PIN['rust_toolchain']
MANIFEST = 'compiler-bundle.json'
RECEIPT = 'install/x2000-toolchain.json'
CHECKS = ['install', 'c_msa', 'rust_std']
REQUIRED_NOTICES = (
    'licenses/q2/LICENSE', 'licenses/q2/NOTICE.md', 'licenses/q2/q2-librespot-MIT.txt',
    'licenses/rust/LICENSE-APACHE', 'licenses/rust/LICENSE-MIT', 'licenses/rust/COPYRIGHT',
    'licenses/llvm-project/LICENSE.TXT', 'licenses/llvm/LICENSE.TXT', 'licenses/clang/LICENSE.TXT',
)


def git(*args):
    return subprocess.check_output(['git', '-C', ROOT, *args], text=True).strip()


def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def digest(path):
    return toolchain.sha256(path)


def hex_value(value, length):
    if not isinstance(value, str) or not re.fullmatch(rf'[0-9a-f]{{{length}}}', value):
        raise ValueError(f'expected a lowercase {length}-digit hexadecimal identity')
    return value


def verify_producer(commit):
    hex_value(commit, 40)
    if git('rev-parse', f'{commit}^{{commit}}') != commit:
        raise ValueError('producer must identify a Git commit')
    if json.loads(git('show', f'{commit}:q2/toolchain/llvm/pin.json')) != toolchain.PIN:
        raise ValueError('producer source pins differ from the current lock')
    for name in ('tools/x2000_toolchain.py', 'q2/toolchain/mipsel-q2-linux-gnu.json'):
        if git('show', f'{commit}:{name}') != (ROOT / name).read_text().rstrip():
            raise ValueError(f'producer input differs from the current checkout: {name}')


def pack(bundle, work, producer):
    verify_producer(producer)
    run(sys.executable, ROOT / 'tools/x2000_toolchain.py', 'check',
        env=dict(os.environ, Q2_X2000_WORK=str(work)))
    # The release record identifies source and its receipt by digest. Audit the
    # directory for absolute checkout paths before distribution.
    sensitive_paths = {str(path).encode() for path in (work, ROOT, Path.home())
                       if len(path.parts) >= 3}
    # Rust's installer writes host-absolute paths in its uninstall records.
    # A relocatable tar distribution is removed by deleting its directory.
    def installer_state(relative):
        return (relative.parent == Path('install/lib/rustlib')
                and (relative.name.startswith('manifest-') or relative.name in {
                    'uninstall.sh', 'install.log', 'components', 'rust-installer-version'}))

    for path in (work / 'install').rglob('*'):
        if installer_state(path.relative_to(work)):
            continue
        if path.is_symlink():
            if Path(path.readlink()).is_absolute():
                raise ValueError(f'absolute symlink in install: {path.relative_to(work)}')
            path.resolve(strict=True).relative_to((work / 'install').resolve())
        elif path.is_file():
            data = path.read_bytes()
            if any(value + b'/' in data for value in sensitive_paths):
                raise ValueError(f'producer path in install: {path.relative_to(work)}')
    def release_metadata(member):
        if installer_state(Path(member.name)):
            return None
        member.uid = member.gid = 0
        member.uname = member.gname = ''
        return member

    llvm = work / 'src/src/llvm-project'
    notices = dict(zip(REQUIRED_NOTICES, (
        ROOT / 'LICENSE', ROOT / 'NOTICE.md', ROOT / 'LICENSES/q2-librespot-MIT.txt',
        work / 'src/LICENSE-APACHE', work / 'src/LICENSE-MIT', work / 'src/COPYRIGHT',
        llvm / 'LICENSE.TXT', llvm / 'llvm/LICENSE.TXT', llvm / 'clang/LICENSE.TXT',
    ), strict=True))
    for name, path in notices.items():
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f'missing notice: {name}')

    with tempfile.TemporaryDirectory() as directory:
        manifest = Path(directory) / MANIFEST
        manifest.write_text(json.dumps({
            'schema': 1, 'producer_commit': producer, 'host': toolchain.HOST,
            'pin': toolchain.PIN, 'install_receipt_sha256': digest(work / RECEIPT),
        }, indent=2) + '\n')
        with tarfile.open(bundle, 'x:gz') as archive:
            archive.add(manifest, arcname=MANIFEST, filter=release_metadata)
            archive.add(work / 'install', arcname='install', filter=release_metadata)
            archive.add(work / 'src/LICENSES', arcname='licenses/rust/texts', filter=release_metadata)
            for name, path in notices.items():
                archive.add(path, arcname=name, filter=release_metadata)
    print(digest(bundle))


def manifest_from_archive(archive):
    names = set()
    for member in archive.getmembers():
        name = Path(member.name)
        if member.name in names or name.is_absolute() or '..' in name.parts or not name.parts:
            raise ValueError('archive member paths must be unique and relative')
        names.add(member.name)
        if name.parts[0] not in (MANIFEST, 'install', 'licenses'):
            raise ValueError('unexpected bundle content')
        tarfile.data_filter(member, '/compiler-bundle')
        if member.uid or member.gid or member.uname or member.gname:
            raise ValueError('producer account identity in bundle metadata')
    manifest, receipt = archive.getmember(MANIFEST), archive.getmember(RECEIPT)
    if any(not member.isfile() or member.size > 1024 * 1024 for member in (manifest, receipt)):
        raise ValueError('invalid or oversized bundle metadata')
    return json.load(archive.extractfile(manifest)), archive.extractfile(receipt).read()


def inspect(bundle, producer, fixture=False):
    verify_producer(producer)
    with tarfile.open(bundle, 'r:gz') as archive:
        manifest, receipt = manifest_from_archive(archive)
        import hashlib
        if manifest != {
            'schema': 1, 'producer_commit': producer, 'host': toolchain.HOST,
            'pin': toolchain.PIN, 'install_receipt_sha256': hashlib.sha256(receipt).hexdigest(),
            **({'fixture': True} if fixture else {}),
        }:
            raise ValueError('bundle provenance mismatch')
        receipt_data = json.loads(receipt)
        if not fixture:
            if receipt_data.get('host') != toolchain.HOST or receipt_data.get('pin') != toolchain.PIN:
                raise ValueError('installation receipt provenance mismatch')
            for name in REQUIRED_NOTICES:
                notice = archive.getmember(name)
                if not notice.isfile() or notice.size == 0:
                    raise ValueError(f'missing notice: {name}')
        return manifest


def verify(bundle, report, producer, expected, fixture=False):
    hex_value(expected, 64)
    if digest(bundle) != expected:
        raise ValueError('bundle SHA-256 mismatch')
    manifest = inspect(bundle, producer, fixture)
    record = json.loads(report.read_text())
    if set(record) != {'schema', 'passed', 'bundle_sha256', 'producer_commit', 'checks',
                       'sdk_archive_sha256', 'qemu_commit', 'qemu_user_sha256', 'host_abi', 'fixture'}:
        raise ValueError('qualification record fields differ from expected schema')
    if (record['schema'] != 1 or record['passed'] is not True or record['fixture'] is not fixture
            or record['bundle_sha256'] != expected or record['producer_commit'] != producer
            or record['checks'] != CHECKS or not re.fullmatch(r'Linux x86_64; glibc [0-9.]+', record['host_abi'])):
        raise ValueError('qualification does not match the exact compiler bundle')
    for field, length in (('sdk_archive_sha256', 64), ('qemu_commit', 40), ('qemu_user_sha256', 64)):
        hex_value(record[field], length)
    return manifest, record


def qualify(a):
    manifest = inspect(a.bundle, a.producer)
    bundle_hash = digest(a.bundle)
    sdk = json.loads(a.sdk_receipt.read_text())
    qemu = json.loads(a.qemu_receipt.read_text())
    if digest(a.qemu_user) != hex_value(qemu['qemu_user_sha256'], 64):
        raise ValueError('QEMU user binary differs from its receipt')
    hex_value(qemu['source_commit'], 40)
    hex_value(sdk['archive_sha256'], 64)
    if a.sdk != Path(sdk['sdk']).resolve():
        raise ValueError('SDK path differs from its relocation receipt')
    gcc = a.sdk / 'bin/mipsel-buildroot-linux-gnu-gcc'
    sysroot = Path(subprocess.check_output([gcc, '-print-sysroot'], text=True).strip()).resolve()
    sysroot.relative_to(a.sdk)
    with tempfile.TemporaryDirectory(prefix='compiler-qualification-') as directory:
        work = Path(directory)
        with tarfile.open(a.bundle, 'r:gz') as archive:
            archive.extractall(work, filter='data')
        if digest(work / RECEIPT) != manifest['install_receipt_sha256']:
            raise ValueError('extracted receipt mismatch')
        env = dict(os.environ, Q2_X2000_WORK=str(work))
        run(sys.executable, ROOT / 'tools/x2000_toolchain.py', 'check', env=env)
        run(sys.executable, ROOT / 'tests/perf/x2000_cost/check.py', '--sdk', a.sdk,
            '--qemu-user', a.qemu_user, '--reps', '1', '--out', work / 'c-msa', env=env)
        (work / 'src').mkdir()
        (work / 'Cargo.toml').write_text('[package]\nname="q2-smoke"\nversion="0.1.0"\nedition="2024"\n')
        (work / 'src/main.rs').write_text('fn main() { println!("q2-rust-ok {}", std::hint::black_box(72)); }\n')
        target = work / 'mipsel-q2-os-linux-gnu.json'
        run(sys.executable, ROOT / 'tools/x2000_toolchain.py', 'spec',
            ROOT / 'q2/toolchain/mipsel-q2-linux-gnu.json', target, env=env)
        run('cargo', f'+{NIGHTLY}', 'generate-lockfile', '--offline', cwd=work)
        env.update(RUSTC=str(work / 'install/bin/rustc'), Q2_OS_CC=str(gcc),
                   CARGO_BUILD_JOBS='2', CARGO_TARGET_DIR=str(work / 'target'),
                   CARGO_TARGET_MIPSEL_Q2_OS_LINUX_GNU_LINKER=str(a.rust_link))
        run('cargo', f'+{NIGHTLY}', 'build', '--offline', '--locked', '--release',
            '-Zbuild-std=std,panic_abort',
            '-Zbuild-std-features=panic-unwind,backtrace,backtrace-trace-only',
            '-Zjson-target-spec', '--target', target, cwd=work, env=env)
        output = subprocess.check_output([
            a.qemu_user, '-cpu', 'P5600', '-L', sysroot,
            work / 'target/mipsel-q2-os-linux-gnu/release/q2-smoke',
        ], text=True)
        if output.strip() != 'q2-rust-ok 72':
            raise ValueError('Rust runtime fixture returned unexpected output')
    if digest(a.bundle) != bundle_hash:
        raise ValueError('bundle changed during qualification')
    abi = subprocess.check_output(['getconf', 'GNU_LIBC_VERSION'], text=True).strip()
    with a.report.open('x') as stream:
        json.dump(dict(schema=1, passed=True, fixture=False, checks=CHECKS,
                       producer_commit=a.producer, bundle_sha256=bundle_hash,
                       sdk_archive_sha256=sdk['archive_sha256'], qemu_commit=qemu['source_commit'],
                       qemu_user_sha256=qemu['qemu_user_sha256'],
                       host_abi=f'{os.uname().sysname} {os.uname().machine}; {abi}'), stream, indent=2)


def fixture_test(producer):
    import io
    import hashlib
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        bundle, report = work / 'compiler.tar.gz', work / 'qualification.json'
        receipt = b'{}'
        manifest = dict(schema=1, producer_commit=producer, host=toolchain.HOST,
                        pin=toolchain.PIN, install_receipt_sha256=hashlib.sha256(receipt).hexdigest(), fixture=True)
        with tarfile.open(bundle, 'x:gz') as archive:
            for name, data in ((MANIFEST, json.dumps(manifest).encode()), (RECEIPT, receipt)):
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        expected = digest(bundle)
        record = dict(schema=1, passed=True, fixture=True, checks=CHECKS,
                      bundle_sha256=expected, producer_commit=producer, sdk_archive_sha256='1' * 64,
                      qemu_commit='2' * 40, qemu_user_sha256='3' * 64, host_abi='Linux x86_64; glibc 2.39')
        report.write_text(json.dumps(record))
        verify(bundle, report, producer, expected, fixture=True)
        for identity, fixture in ((expected, False), ('4' * 64, True)):
            try:
                verify(bundle, report, producer, identity, fixture=fixture)
            except ValueError:
                continue
            raise AssertionError('accepted a fixture as a release or accepted the wrong digest')
    print('Release validation accepted the synthetic fixture and rejected production use and digest tampering.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    for action in ('pack', 'qualify', 'verify', 'fixture-test'):
        command = commands.add_parser(action)
        command.add_argument('--producer', required=True)
        if action != 'fixture-test':
            command.add_argument('--bundle', type=lambda p: Path(p).resolve(), required=True)
        if action in ('qualify', 'verify'):
            command.add_argument('--report', type=lambda p: Path(p).resolve(), required=True)
    commands.choices['pack'].add_argument('--work', type=lambda p: Path(p).resolve(), required=True)
    commands.choices['verify'].add_argument('--sha256', required=True)
    for field in ('sdk', 'sdk-receipt', 'qemu-user', 'qemu-receipt', 'rust-link'):
        commands.choices['qualify'].add_argument(f'--{field}', type=lambda p: Path(p).resolve(), required=True)
    args = parser.parse_args()
    if args.action == 'pack':
        pack(args.bundle, args.work, args.producer)
    elif args.action == 'qualify':
        qualify(args)
    elif args.action == 'verify':
        manifest, record = verify(args.bundle, args.report, args.producer, args.sha256)
        print(json.dumps(dict(bundle=manifest, qualification=record), indent=2))
    else:
        fixture_test(args.producer)


if __name__ == '__main__':
    main()
