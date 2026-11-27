#!/usr/bin/env python3
"""Build and select the X2000 toolchain: the pinned nightly rustc and clang on
an LLVM with the X2000 cost model, scheduling model and MSA lowering fixes.

Use the locked Rust and LLVM Project Git repositories and the hash-checked
Rust distribution source archive. The archive supplies Rust's shipped source,
vendored crates and distribution version metadata. LLVM, including Clang, is
exported from the selected fork commit. See README.md for local inputs.
"""
import argparse
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LLVM_DIR = ROOT / 'q2/toolchain/llvm'
PIN = json.loads((LLVM_DIR / 'pin.json').read_text())
MIPS = 'src/llvm-project/llvm/lib/Target/Mips'
HOST = 'x86_64-unknown-linux-gnu'
LLVM_TOOLS = ('clang', 'llc', 'opt', 'llvm-mca', 'llvm-objdump', 'llvm-profdata')
# The feature `x2000` selects the costs and lowering. The CPU name `x2000`
# adds the X2000 scheduling model and the pre-RA machine scheduler that uses
# it. That is the default: on the Q2 (2026-11-21, 32 cases) it was faster
# than the feature alone almost everywhere (WebP 0.80 against 0.84 of the
# stock build, PNG 0.96 against 1.04). Q2_X2000_SCHED=0 gives the feature on
# CPU mips32r5 (generic scheduling).
SPEC_CPU = 'x2000'
SPEC_FEATURES = '+msa'


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def work_dir():
    if os.environ.get('Q2_X2000_WORK'):
        return Path(os.environ['Q2_X2000_WORK']).expanduser().absolute()
    return ROOT / 'build/x2000-toolchain'


def source_identity():
    return {key: PIN[key] for key in ('rust_commit', 'llvm_base', 'llvm_commit', 'source_sha256')}


def patch_identity():
    """Compatibility name used by OS and player receipt producers."""
    return source_identity()


def git_output(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def source_checkouts(rust, llvm):
    for repo, key in ((rust, 'rust_commit'), (llvm, 'llvm_commit')):
        if not (repo / '.git').exists() and not (
            (repo / 'HEAD').is_file() and (repo / 'objects').is_dir()
        ):
            raise SystemExit(f'{repo}: expected a Git checkout or bare repository')
        expected = PIN[key]
        if git_output(repo, 'rev-parse', 'HEAD') != expected:
            raise SystemExit(f'{repo}: check out the pinned {key} {expected}')
    bundled = git_output(rust, 'rev-parse', 'HEAD:src/llvm-project')
    if bundled != PIN['llvm_base']:
        raise SystemExit(f'{rust}: unexpected bundled LLVM revision {bundled}')
    if subprocess.run(['git', '-C', str(llvm), 'merge-base', '--is-ancestor',
                       bundled, PIN['llvm_commit']]).returncode:
        raise SystemExit(f'{llvm}: pinned LLVM does not contain the bundled Rust base {bundled}')


def run(args, **kw):
    print('+', ' '.join(shlex.quote(str(a)) for a in args), flush=True)
    subprocess.run([str(a) for a in args], check=True, **kw)


def fetch(work, archive):
    tarball = archive or work / 'dl' / Path(PIN['source_url']).name
    if archive is None and not tarball.exists():
        tarball.parent.mkdir(parents=True, exist_ok=True)
        part = tarball.with_suffix('.part')
        print('fetching', PIN['source_url'], flush=True)
        with urllib.request.urlopen(PIN['source_url'], timeout=60) as response, part.open('wb') as output:
            shutil.copyfileobj(response, output, 1 << 20)
        part.rename(tarball)
    if not tarball.is_file():
        raise SystemExit(f'{tarball}: Rust source archive is missing')
    if sha256(tarball) != PIN['source_sha256']:
        raise SystemExit(f'{tarball}: SHA-256 differs from q2/toolchain/llvm/pin.json')
    return tarball


def verify_rust_archive(rust, stage):
    """Compare materialized distribution files with their Rust Git blobs."""
    entries = subprocess.check_output(['git', '-C', str(rust), 'ls-tree', '-rz', 'HEAD'])
    checked = 0
    for entry in entries.split(b'\0')[:-1]:
        metadata, name = entry.split(b'\t', 1)
        mode, kind, expected = metadata.split()
        if kind != b'blob':
            continue
        path = stage / os.fsdecode(name)
        if path.is_symlink() and mode == b'120000':
            content = os.fsencode(os.readlink(path))
        elif path.is_file() and not path.is_symlink() and mode != b'120000':
            content = path.read_bytes()
        elif not path.exists() and not path.is_symlink():
            # The distribution omits development-only files.
            continue
        else:
            raise SystemExit(f'{os.fsdecode(name)}: archive file type differs from Rust Git tree')
        prefix = f'blob {len(content)}\0'.encode()
        actual = hashlib.sha1(prefix + content, usedforsecurity=False).hexdigest().encode()
        if actual != expected:
            raise SystemExit(f'{os.fsdecode(name)}: archive bytes differ from Rust Git tree')
        checked += 1
    print(f'{checked} Rust archive files matched their pinned Git blobs.', flush=True)


def prepare(work, rust, llvm, archive=None):
    """Assemble the verified Rust distribution with the committed LLVM tree."""
    source_checkouts(rust, llvm)
    tarball = fetch(work, archive)
    src = work / 'src'
    if src.exists():
        raise SystemExit(f'{src}: source already exists; choose an unused Q2_X2000_WORK')
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='prepare-', dir=work) as temp:
        stage = Path(temp) / 'src'
        stage.mkdir()
        run(['tar', '-xJf', tarball, '-C', stage, '--strip-components=1',
             '--exclude=rustc-nightly-src/src/llvm-project'])
        if (stage / 'git-commit-hash').read_text().strip() != PIN['rust_commit']:
            raise SystemExit(f'{tarball}: Rust distribution does not match the pinned Rust checkout')
        verify_rust_archive(rust, stage)
        llvm_tree = stage / 'src/llvm-project'
        llvm_tree.mkdir()
        llvm_tar = Path(temp) / 'llvm.tar'
        run(['git', '-C', llvm, 'archive', '--format=tar', '--output', llvm_tar, PIN['llvm_commit']])
        run(['tar', '-xf', llvm_tar, '-C', llvm_tree])
        stage.rename(src)
    return src


def bootstrap_toml(work, jobs):
    return f'''# Written by tools/x2000_toolchain.py.
change-id = "ignore"

[llvm]
download-ci-llvm = false
targets = "Mips;X86"
experimental-targets = ""
clang = true
ninja = true
link-jobs = {jobs}
assertions = false

[build]
build-dir = {json.dumps(str(work / 'build'))}
docs = false
extended = true
tools = ["rust-src"]
jobs = {jobs}
profiler = false
# The nightly's own version string (a tarball build otherwise appends a
# description): the same source then gets the same symbol hashes from both
# compilers, which the qualification compares.
description = ""

[install]
prefix = {json.dumps(str(work / 'install'))}
sysconfdir = "etc"

[rust]
remap-debuginfo = true
channel = "nightly"
lld = false
llvm-tools = true
'''


def receipt_path(work):
    return work / 'install/x2000-toolchain.json'


def begin(a):
    """Check a new work directory, prepare the sources and write the bootstrap configuration."""
    work = work_dir()
    if sys.platform != 'linux' or os.uname().machine != 'x86_64':
        raise SystemExit(f'The current bootstrap recipe targets {HOST}')
    if a.jobs < 1:
        raise SystemExit('--jobs must be positive')
    work.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(work).free >> 30
    if any((work / name).exists() for name in ('build', 'install', 'bootstrap.toml', 'build-01.log')):
        raise SystemExit(f'{work}: choose an unused Q2_X2000_WORK for a new build')
    if free < 25:
        raise SystemExit(f'{work}: {free} GiB free; a first build needs about 17')
    src = prepare(work, a.rust, a.llvm, a.rust_source)
    config = work / 'bootstrap.toml'
    with config.open('x') as output:
        output.write(bootstrap_toml(work, a.jobs))
    return work, src, config


def bootstrap(work, src, config, *command):
    log = work / 'build-01.log'
    print('log:', log, flush=True)
    with open(log, 'x') as f:
        p = subprocess.Popen([sys.executable, str(src / 'x.py'), *command, '--config', str(config)],
                             cwd=src, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
        for line in p.stdout:
            f.write(line)
            f.flush()
            if not line.startswith('[') or 'Linking' in line:
                sys.stdout.write(line)
                sys.stdout.flush()
        if p.wait():
            raise SystemExit(f'x.py {" ".join(command)} failed; see {log}')


def finish(work, src):
    """Add Clang, its resource headers and the LLVM tools to the install and write the receipt."""
    llvm_build = work / STAGES['llvm']
    install = work / 'install'
    tools = install / 'llvm/bin'
    tools.mkdir(parents=True, exist_ok=True)
    for name in LLVM_TOOLS:
        source = llvm_build / 'build/bin' / name
        if not source.exists():
            source = llvm_build / 'bin' / name
        target = tools / name
        shutil.copy2(os.path.realpath(source), target)
    # clang finds its builtin headers relative to its binary.
    resource = next((llvm_build / 'build/lib/clang').iterdir())
    shutil.copytree(resource, install / 'llvm/lib/clang' / resource.name, symlinks=True)
    # The tools must come from this run's LLVM: bootstrap skips the LLVM
    # step when it believes nothing changed.
    built = (llvm_build / 'build/lib/libLLVMMipsCodeGen.a').stat().st_mtime
    newest = max((src / MIPS / name).stat().st_mtime for name in os.listdir(src / MIPS))
    if built < newest:
        raise SystemExit('LLVM was not rebuilt after the MIPS sources changed')
    write_receipt(work)
    print(json.dumps(json.loads(receipt_path(work).read_text()), indent=1))


def build(a):
    work, src, config = begin(a)
    bootstrap(work, src, config, 'install')
    finish(work, src)


# The directories the workflow's jobs hand on (docs/compiler-release.md):
# LLVM's output to the Rust job, the installed compiler to the package job.
STAGES = {'llvm': Path('build') / HOST / 'llvm', 'install': Path('install')}
# Rust compiles its LLVM wrapper with the flags LLVM was built with.
HANDOFF_FLAGS = ('CFLAGS', 'CXXFLAGS')
ZSTD = 'zstd -T0 -9 --long=30'


def handoff_identity(stage, work):
    """What a handoff's consumer must share with its producer."""
    identity = {
        'schema': 1, 'stage': stage, 'producer_commit': git_output(ROOT, 'rev-parse', 'HEAD'), 'pin': PIN,
        'host': HOST, 'work': str(work),
        # The bootstrap configuration apart from its job counts.
        'recipe_sha256': hashlib.sha256(bootstrap_toml(work, 0).encode()).hexdigest(),
    }
    if stage == 'llvm':
        identity['flags'] = {name: os.environ.get(name, '') for name in HANDOFF_FLAGS}
    return identity


def tree_entries(root):
    """Every path below root: a file's mode and SHA-256, a symlink's target or a directory."""
    entries = {}
    for top, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(top) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                entry = {'symlink': os.readlink(path)}
            elif stat.S_ISDIR(info.st_mode):
                entry = {'directory': True}
            elif stat.S_ISREG(info.st_mode):
                entry = {'mode': f'{info.st_mode & 0o777:o}', 'sha256': sha256(path)}
            else:
                raise SystemExit(f'{path}: not a file, directory or symlink')
            entries[path.relative_to(root).as_posix()] = entry
    return dict(sorted(entries.items()))


def prune_llvm_build(work):
    """Remove LLVM's CMake build tree except the files `finish` reads from it."""
    build = (work / STAGES['llvm'] / 'build').resolve()
    resource = build / 'lib/clang'
    keep = {build / 'lib/libLLVMMipsCodeGen.a'}
    for name in LLVM_TOOLS:
        keep |= {build / 'bin' / name, Path(os.path.realpath(build / 'bin' / name))}
    missing = [str(path) for path in keep if not path.is_file()]
    if missing or not resource.is_dir():
        raise SystemExit(f'LLVM build tree lacks {", ".join(missing) or resource}')
    for top, dirs, files in os.walk(build, topdown=False):
        top = Path(top)
        if top == resource or resource in top.parents:
            continue
        for name in files + [name for name in dirs if (top / name).is_symlink()]:
            if top / name not in keep:
                (top / name).unlink()
        if top != build and not any(top.iterdir()):
            top.rmdir()


def write_handoff(stage, work, out):
    root = work / STAGES[stage]
    out.mkdir(parents=True, exist_ok=True)
    archive = out / f'{stage}.tar.zst'
    run(['tar', '--create', '--file', archive, '--directory', root, '--sort=name',
         '--use-compress-program', ZSTD, '.'])
    manifest = {**handoff_identity(stage, work), 'archive': archive.name,
                'archive_bytes': archive.stat().st_size, 'archive_sha256': sha256(archive),
                'entries': tree_entries(root)}
    with (out / f'{stage}.json').open('x') as output:
        output.write(json.dumps(manifest, indent=1) + '\n')
    print(f'{stage} handoff: {archive.stat().st_size >> 20} MiB, {len(manifest["entries"])} paths, '
          f'SHA-256 {manifest["archive_sha256"]}', flush=True)


def restore_handoff(stage, work, source):
    """Unpack a handoff after checking it against this checkout, its archive digest and its file list."""
    path = source / f'{stage}.json'
    manifest = json.loads(path.read_text())
    expected = {**handoff_identity(stage, work), 'archive': f'{stage}.tar.zst'}
    differ = [key for key, value in expected.items() if manifest.get(key) != value]
    if differ:
        raise SystemExit(f'{path}: the {stage} handoff differs from this checkout in {", ".join(differ)}')
    archive = source / manifest['archive']
    if sha256(archive) != manifest['archive_sha256']:
        raise SystemExit(f'{archive}: SHA-256 differs from {path.name}')
    root = work / STAGES[stage]
    if root.exists():
        raise SystemExit(f'{root}: already exists; restore into an unused Q2_X2000_WORK')
    root.mkdir(parents=True)
    run(['tar', '--extract', '--file', archive, '--directory', root, '--use-compress-program', ZSTD])
    if tree_entries(root) != manifest['entries']:
        raise SystemExit(f'{root}: unpacked files differ from {path.name}')
    print(f'{stage} handoff verified: SHA-256 {manifest["archive_sha256"]}', flush=True)
    return manifest


def build_llvm(a):
    """Build LLVM and Clang only and hand off LLVM's output directory."""
    work, src, config = begin(a)
    bootstrap(work, src, config, 'build', 'src/llvm-project/llvm')
    prune_llvm_build(work)
    write_handoff('llvm', work, a.handoff)


def build_rust(a):
    """Build and install stage-2 Rust on a handed-off LLVM and hand off the install."""
    work, src, config = begin(a)
    llvm = restore_handoff('llvm', work, getattr(a, 'from'))
    bootstrap(work, src, config, 'install')
    # Bootstrap would rebuild LLVM in place if it did not accept the handoff.
    if tree_entries(work / STAGES['llvm']) != llvm['entries']:
        raise SystemExit('Rust bootstrap changed the handed-off LLVM')
    finish(work, src)
    write_handoff('install', work, a.handoff)


def write_receipt(work):
    install = work / 'install'
    rustc = install / 'bin/rustc'
    version = subprocess.check_output([str(rustc), '-vV'], text=True)
    fields = dict(line.split(': ', 1) for line in version.splitlines()[1:])
    if fields['commit-hash'] != PIN['rust_commit'] or fields['LLVM version'] != PIN['llvm_version']:
        raise SystemExit(f'{rustc} is not the pinned compiler:\n{version}')
    cpus = subprocess.check_output([str(rustc), '--print', 'target-cpus', '--target', 'mipsel-unknown-linux-gnu'], text=True)
    if 'x2000' not in cpus.split():
        raise SystemExit(f'{rustc} has no x2000 CPU')
    files = {}
    for path in sorted([rustc, *(install / 'lib').glob('librustc_driver-*.so'), *(install / 'llvm/bin').iterdir()]):
        files[str(path.relative_to(install))] = sha256(path)
    receipt = {**source_identity(), 'pin': PIN, 'rustc_version': version.splitlines()[0],
               'host': fields['host'], 'files': files}
    with receipt_path(work).open('x') as output:
        output.write(json.dumps(receipt, indent=1) + '\n')


def profile_runtime(a):
    """Build compiler-rt's profile runtime for the Q2 into the installed compiler.

    The sources are the pinned LLVM Project commit's, from the prepared source
    tree or from `--llvm`. The X2000 C wrapper compiles them against the SDK
    sysroot; the SDK's GCC has no C++ compiler for InstrProfilingRuntime.cpp.
    The archive, its record and the receipt's file list change.
    """
    sys.path.insert(0, str(HERE))
    import q2_pgo
    work = work_dir()
    install = installed()
    sdk = a.sdk.resolve()
    ar = sdk / 'bin/mipsel-buildroot-linux-gnu-ar'
    cc = [ROOT / 'q2/toolchain/q2-x2000-cc']
    compiler = {'kind': 'x2000-clang', 'wrapper_sha256': sha256(cc[0]),
                'clang_sha256': sha256(install / 'llvm/bin/clang')}
    env = dict(os.environ, Q2_X2000_CLANG=str(install / 'llvm/bin/clang'), Q2_SDK=str(sdk),
               Q2_X2000_CC_ARCH=cc_arch())
    with tempfile.TemporaryDirectory(prefix='profile-runtime-src-') as temp:
        if a.llvm:
            source = q2_pgo.export_runtime_sources(a.llvm, PIN['llvm_commit'], Path(temp))
        else:
            source = work / 'src/src/llvm-project'
            if not (source / 'compiler-rt/lib/profile').is_dir():
                raise SystemExit(f'{source}: no prepared LLVM source; pass --llvm with the pinned repository')
        record = q2_pgo.build_runtime(source, install / q2_pgo.RUNTIME, cc, ar, env=env,
                                      llvm_commit=PIN['llvm_commit'], compiler_name=compiler)
    record.update(llvm_base=PIN['llvm_base'], sdk={'archive_sha256': a.sdk_archive_sha256, 'ar_sha256': sha256(ar)})
    record_path = install / q2_pgo.RUNTIME_RECORD
    record_path.write_text(json.dumps(record, indent=1, sort_keys=True) + '\n')
    path = receipt_path(work)
    receipt = json.loads(path.read_text())
    receipt['profile_runtime'] = record
    for item in (install / q2_pgo.RUNTIME, record_path):
        receipt['files'][str(item.relative_to(install))] = sha256(item)
    receipt['files'] = dict(sorted(receipt['files'].items()))
    part = path.with_name(path.name + '.part')
    part.write_text(json.dumps(receipt, indent=1) + '\n')
    part.replace(path)
    return record


def installed(required=True):
    """The install directory, checked against the pinned source revisions."""
    work = work_dir()
    path = receipt_path(work)
    if not path.exists():
        if required:
            raise SystemExit(f'X2000 toolchain not built ({path} missing): python3 tools/x2000_toolchain.py build')
        return None
    receipt = json.loads(path.read_text())
    identity = source_identity()
    stale = [k for k, v in identity.items() if receipt.get(k) != v]
    if stale:
        raise SystemExit(f'X2000 toolchain at {work / "install"} was built from another {", ".join(stale)}: '
                         'python3 tools/x2000_toolchain.py build')
    return work / 'install'


def enabled(environ=None):
    """The default since the Q2 A/B (docs/x2000-toolchain.md); Q2_X2000=0 is the stock nightly."""
    return (os.environ if environ is None else environ).get('Q2_X2000', '1') == '1'


def spec(base):
    """The Q2 target specification for the x2000 CPU, from the checked-in one."""
    out = dict(base)
    features = [f for f in base['features'].split(',') if f]
    extra = SPEC_FEATURES.split(',')
    if os.environ.get('Q2_X2000_SCHED', '1') == '1':
        out['cpu'] = SPEC_CPU
    else:
        out['cpu'] = 'mips32r5'
        extra.append('+x2000')
    for f in extra:
        if f not in features:
            features.append(f)
    out['features'] = ','.join(features)
    return out


def cc_arch(environ=None):
    """clang CPU flags that match the target specification `spec` writes."""
    environ = os.environ if environ is None else environ
    if environ.get('Q2_X2000_SCHED', '1') == '1':
        return '-march=x2000 -mmsa'
    return '-march=mips32r5 -mmsa -Xclang -target-feature -Xclang +x2000'


def cargo_env(environ=None, sdk=None):
    """Environment additions for a cargo build with the X2000 toolchain.

    The other crates' C code (SQLite, ring) is compiled with the X2000
    clang too; Q2_X2000_C=0 keeps the SDK's GCC for it. The player's own C
    leaves always use GCC (crates/q2nd/build.rs: the EQ leaf was 1.30
    slower from clang on the Q2). The link stays with GCC either way.
    """
    environ = os.environ if environ is None else environ
    install = installed()
    env = {'RUSTC': str(install / 'bin/rustc'), 'Q2_X2000_INSTALL': str(install),
           'Q2_X2000_CLANG': str(install / 'llvm/bin/clang')}
    if environ.get('Q2_X2000_C', '1') == '1':
        if sdk is None:
            sdk = environ.get('Q2_SDK')
        if not sdk:
            raise SystemExit('Set Q2_SDK to the location of a relocated Q2 Buildroot SDK')
        env.update({'CC_mipsel_q2_os_linux_gnu': str(ROOT / 'q2/toolchain/q2-x2000-cc'), 'Q2_SDK': str(sdk),
                    'Q2_X2000_CC_ARCH': cc_arch(environ)})
    return env


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    p = sub.add_parser('prepare')
    bl = sub.add_parser('build-llvm', help='build LLVM and Clang and write their handoff')
    br = sub.add_parser('build-rust', help='build Rust on a handed-off LLVM and write the install handoff')
    for parser in (b, p, bl, br):
        parser.add_argument('--rust', type=Path, required=True, help='pinned Rust Git repository')
        parser.add_argument('--llvm', type=Path, required=True, help='pinned LLVM Project Git repository')
        parser.add_argument('--rust-source', type=Path, help='use a local copy of the locked Rust source archive')
    for parser in (b, bl, br):
        parser.add_argument('--jobs', type=int, default=2)
    for parser in (bl, br):
        parser.add_argument('--handoff', type=Path, required=True, help='directory for the handoff archive and manifest')
    br.add_argument('--from', type=Path, required=True, help='directory holding the LLVM handoff')
    restore = sub.add_parser('restore', help='unpack and verify a handoff into Q2_X2000_WORK')
    restore.add_argument('stage', choices=['llvm', 'install'])
    restore.add_argument('--from', type=Path, required=True, help='directory holding the handoff')
    sources = sub.add_parser('check-sources')
    sources.add_argument('--rust', type=Path, required=True)
    sources.add_argument('--llvm', type=Path, required=True)
    runtime = sub.add_parser('profile-runtime', help='add the compiler-rt profile runtime to the installed compiler')
    runtime.add_argument('--sdk', type=Path, required=True, help='relocated q2-os SDK')
    runtime.add_argument('--sdk-archive-sha256', required=True, help='SHA-256 of the SDK release archive')
    runtime.add_argument('--llvm', type=Path, help='pinned LLVM Project Git repository (default: the prepared source)')
    sub.add_parser('show')
    sub.add_parser('check')
    sub.add_parser('shell-env')
    s = sub.add_parser('spec')
    s.add_argument('input', type=Path)
    s.add_argument('output', type=Path)
    a = ap.parse_args()
    if a.cmd == 'build':
        build(a)
    elif a.cmd == 'build-llvm':
        build_llvm(a)
    elif a.cmd == 'build-rust':
        build_rust(a)
    elif a.cmd == 'restore':
        restore_handoff(a.stage, work_dir(), getattr(a, 'from'))
    elif a.cmd == 'prepare':
        print(prepare(work_dir(), a.rust, a.llvm, a.rust_source))
    elif a.cmd == 'check-sources':
        source_checkouts(a.rust, a.llvm)
        print(json.dumps(source_identity(), indent=2))
    elif a.cmd == 'profile-runtime':
        print(json.dumps(profile_runtime(a), indent=1))
    elif a.cmd == 'show':
        installed()
        print(receipt_path(work_dir()).read_text(), end='')
    elif a.cmd == 'check':
        install = installed()
        receipt = json.loads(receipt_path(work_dir()).read_text())
        for name, expected in receipt['files'].items():
            path = install / name
            if not path.is_file() or sha256(path) != expected:
                raise SystemExit(f'{path}: installed file does not match the receipt')
        print(install)
    elif a.cmd == 'shell-env':
        for k, v in cargo_env().items():
            print(f'export {k}={shlex.quote(v)}')
    elif a.cmd == 'spec':
        text = json.dumps(spec(json.loads(a.input.read_text())), indent=2) + '\n'
        if not a.output.exists() or a.output.read_text() != text:
            a.output.write_text(text)


if __name__ == '__main__':
    main()
