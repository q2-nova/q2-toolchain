#!/usr/bin/env python3
"""Profile-guided optimization driver shared by the Q2 programs this toolchain builds.

The profile runtime, `llvm-profdata` and the instrumentation flags must match
the LLVM inside rustc, so they live with the compiler. A consumer's
`tools/pgo.py` owns its training set and calls these pieces:

  build_runtime     compiler-rt's profile runtime for the Q2 target; the
                    compiler ships it at RUNTIME (x2000_toolchain.py
                    profile-runtime)
  instrument_flags  rustflags for an instrumented build
  Training          runs training commands under qemu-mipsel or
                    qemu-system-mipsel with LLVM_PROFILE_FILE set
  merge             llvm-profdata merge of the raw profiles, with weights
  coverage          functions and regions with counts per crate and module,
                    and named hot-path checks
  make_receipt      what made a profile; check_receipt compares one with a build

  python3 tools/q2_pgo.py flags --profiles DIR --runtime ARCHIVE
  python3 tools/q2_pgo.py coverage --profdata TOOL PROFILE [--hot NAME=REGEX ...]
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

SCHEMA = 1
# Relative to the compiler's install directory.
RUNTIME = 'llvm/lib/mipsel-q2-os-linux-gnu/libclang_rt.profile.a'
RUNTIME_RECORD = 'llvm/lib/mipsel-q2-os-linux-gnu/profile-runtime.json'
PROFDATA = 'llvm/bin/llvm-profdata'
# compiler-rt/lib/profile sources for a Linux target without value-profile
# extensions beyond the defaults.
PROFILE_SOURCES = (
    'GCDAProfiling.c', 'InstrProfiling.c', 'InstrProfilingBuffer.c', 'InstrProfilingFile.c',
    'InstrProfilingInternal.c', 'InstrProfilingMerge.c', 'InstrProfilingMergeFile.c',
    'InstrProfilingNameVar.c', 'InstrProfilingPlatformLinux.c', 'InstrProfilingPlatformOther.c',
    'InstrProfilingRuntime.cpp', 'InstrProfilingUtil.c', 'InstrProfilingValue.c',
    'InstrProfilingVersionVar.c', 'InstrProfilingWriter.c',
)
RUNTIME_SOURCE_PATHS = ('compiler-rt/lib/profile', 'compiler-rt/include')
RUNTIME_CFLAGS = (
    '-O2', '-ffunction-sections', '-fdata-sections', '-fPIC', '-fno-builtin', '-fvisibility=hidden',
    '-DVISIBILITY_HIDDEN', '-DCOMPILER_RT_HAS_UNAME=1', '-DCOMPILER_RT_HAS_FCNTL_LCK=1',
    '-DCOMPILER_RT_HAS_ATOMICS=1',
)
QEMU_CPU = 'P5600'
# Raw-profile names: one file per process, or (continuous mode) one file per
# binary that every process of it, and every exec of it, counts into.
PER_PROCESS = '%p-%m.profraw'
CONTINUOUS = '%m%c.profraw'


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


# Profile runtime

def export_runtime_sources(llvm_repo, commit, dest):
    """Write the runtime's sources from a Git repository at `commit` into `dest`."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(['git', '-C', str(llvm_repo), 'archive', '--format=tar', commit,
                              *RUNTIME_SOURCE_PATHS], check=True, capture_output=True).stdout
    subprocess.run(['tar', '-xf', '-', '-C', str(dest)], input=archive, check=True)
    return dest


def build_runtime(llvm_source, out, cc, ar, *, env=None, llvm_commit, compiler_name):
    """Build compiler-rt's profile runtime from `llvm_source` (an llvm-project tree).

    `cc` is the C compiler command for the Q2 (a list); `compiler_name` says
    which one it is in the record. Writes `out` and returns the record.
    """
    llvm_source = Path(llvm_source).resolve()
    rt = llvm_source / 'compiler-rt'
    missing = [n for n in PROFILE_SOURCES if not (rt / 'lib/profile' / n).is_file()]
    if missing:
        raise SystemExit(f'{rt}: missing profile runtime sources {missing}')
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='profile-runtime-') as temp:
        objects = []
        for name in PROFILE_SOURCES:
            obj = Path(temp) / (name + '.o')
            subprocess.run([*map(str, cc), *RUNTIME_CFLAGS, f'-ffile-prefix-map={llvm_source}=/llvm-project',
                            '-I', str(rt / 'include'), '-c', str(rt / 'lib/profile' / name), '-o', str(obj)],
                           env=env, check=True)
            objects.append(obj)
        part = out.with_name(out.name + '.part')
        part.unlink(missing_ok=True)
        # D: zero timestamps and owners, so the archive depends only on its objects.
        subprocess.run([str(ar), 'crsD', str(part), *map(str, objects)], check=True)
        part.replace(out)
    return {
        'schema': SCHEMA, 'archive': out.name, 'archive_sha256': sha256(out), 'llvm_commit': llvm_commit,
        'compiler': compiler_name, 'cflags': list(RUNTIME_CFLAGS),
        'sources': {name: sha256(rt / 'lib/profile' / name) for name in PROFILE_SOURCES},
    }


def runtime(install):
    """The shipped runtime archive and its record, checked against each other."""
    install = Path(install)
    archive, record_path = install / RUNTIME, install / RUNTIME_RECORD
    if not archive.is_file() or not record_path.is_file():
        raise SystemExit(f'{install}: compiler has no profile runtime ({RUNTIME}); '
                         'run tools/x2000_toolchain.py profile-runtime')
    record = json.loads(record_path.read_text())
    if record.get('archive_sha256') != sha256(archive):
        raise SystemExit(f'{archive}: differs from {RUNTIME_RECORD}')
    return archive, record


# Builds

def instrument_flags(profiles, runtime_archive, continuous=False):
    """rustflags of an instrumented build.

    build-std cannot add profiler_builtins for a JSON target, so the runtime is
    linked whole; libc follows it for glibc's nonshared atexit. `continuous`
    adds runtime counter relocation, which continuous mode (%c, CONTINUOUS)
    needs: the counters then live in the mapped profile file, so a daemon that
    is killed or replaces itself with exec keeps its counts. Counter layout and
    function hashes are the same, so the profile fits the release build.
    Continuous mode writes no value-profile data, so it also turns off value
    profiling (indirect-call targets); otherwise the records name value sites
    that the raw profile lacks and llvm-profdata refuses it as truncated.
    """
    return ['-Cprofile-generate=' + str(profiles), '-Zno-profiler-runtime',
            *(['-Cllvm-args=-runtime-counter-relocation', '-Cllvm-args=-disable-vp'] if continuous else []),
            '-Clink-arg=-Wl,--whole-archive', '-Clink-arg=' + str(runtime_archive),
            '-Clink-arg=-Wl,--no-whole-archive', '-Clink-arg=-lc']


def use_flags(profile):
    return ['-Cprofile-use=' + str(profile)]


def feature_set_sha256(features):
    """The digest build records use for a {package: [features]} map."""
    return hashlib.sha256(json.dumps(features, sort_keys=True).encode()).hexdigest()


def compiler_identity(install, runtime_archive=None, profdata=None):
    """What identifies the compiler a profile was made with and must be used with."""
    install = Path(install)
    receipt = install / 'x2000-toolchain.json'
    record = json.loads(receipt.read_text())
    runtime_archive = Path(runtime_archive) if runtime_archive else install / RUNTIME
    profdata = Path(profdata) if profdata else install / PROFDATA
    return {
        'compiler_receipt_sha256': sha256(receipt), 'rustc_sha256': sha256(install / 'bin/rustc'),
        'rustc_version': record['rustc_version'], 'llvm_commit': record['llvm_commit'],
        'llvm_base': record['llvm_base'], 'profile_runtime_sha256': sha256(runtime_archive),
        'llvm_profdata_sha256': sha256(profdata),
    }


# Training

def slug(name):
    return re.sub(r'[^A-Za-z0-9.-]+', '-', name).strip('-') or 'run'


class Training:
    """Training runs, each with its own raw-profile directory and weight.

    LLVM_PROFILE_FILE uses %p (one file per process) and %m (per binary), so a
    run's daemons and children each leave their counts.
    """

    def __init__(self, profiles, qemu_user=None, sysroot=None, cpu=QEMU_CPU, env=None):
        self.profiles = Path(profiles)
        self.profiles.mkdir(parents=True, exist_ok=True)
        self.qemu_user, self.sysroot, self.cpu = qemu_user, sysroot, cpu
        self.env = dict(os.environ if env is None else env)
        self.runs = []

    def directory(self, name):
        directory = self.profiles / f'{len(self.runs):03d}-{slug(name)}'
        if directory.exists():
            raise SystemExit(f'{directory}: training run directory exists')
        directory.mkdir()
        return directory

    def user_prefix(self):
        if self.qemu_user is None or self.sysroot is None:
            raise SystemExit('qemu-user training needs qemu_user and sysroot')
        return [str(self.qemu_user), '-cpu', self.cpu, '-L', str(self.sysroot)]

    def environment(self, directory, env=None, continuous=False):
        out = dict(self.env)
        out.update(env or {})
        out['LLVM_PROFILE_FILE'] = str(directory / (CONTINUOUS if continuous else PER_PROCESS))
        return out

    def _record(self, name, directory, weight, start, returncode, mode, extra=None):
        run = {'case': name, 'mode': mode, 'weight': weight, 'exit': returncode,
               'seconds': round(time.monotonic() - start, 1), 'directory': directory.name}
        run.update(extra or {})
        self.runs.append(run)
        return run

    def user(self, name, argv, *, env=None, weight=1, timeout=900, check=True, continuous=False, **kwargs):
        """Run `argv` (the target program first) under qemu-mipsel; returns the process."""
        directory = self.directory(name)
        start = time.monotonic()
        process = subprocess.run([*self.user_prefix(), *map(str, argv)],
                                 env=self.environment(directory, env, continuous), timeout=timeout, **kwargs)
        self._record(name, directory, weight, start, process.returncode, 'qemu-user')
        if check and process.returncode:
            raise SystemExit(f'training run {name} exited {process.returncode}')
        return process

    def user_background(self, name, argv, *, env=None, weight=1, continuous=False, **kwargs):
        """Start `argv` under qemu-mipsel; finish() or stop() records it.

        A program that does not exit by itself needs `continuous` (and a build
        with continuous instrumentation flags) for its counts to survive stop().
        """
        directory = self.directory(name)
        process = subprocess.Popen([*self.user_prefix(), *map(str, argv)],
                                   env=self.environment(directory, env, continuous), **kwargs)
        return Background(self, name, directory, weight, process, continuous)

    def system(self, name, argv, *, collect, guest_pattern, env=None, weight=1, timeout=1800, check=True, **kwargs):
        """Run a qemu-system-mipsel command whose guest writes profiles to `guest_pattern`.

        The image tooling passes Q2_PGO_LLVM_PROFILE_FILE into the guest's
        environment for the instrumented program. After the guest stops,
        `collect(directory)` copies the guest's raw profiles into `directory`.
        """
        directory = self.directory(name)
        environment = dict(self.env)
        environment.update(env or {})
        environment['Q2_PGO_LLVM_PROFILE_FILE'] = guest_pattern
        start = time.monotonic()
        process = subprocess.run(list(map(str, argv)), env=environment, timeout=timeout, **kwargs)
        collect(directory)
        raws = len(list(directory.glob('*.profraw')))
        self._record(name, directory, weight, start, process.returncode, 'qemu-system', {'raw_profiles': raws})
        if check and (process.returncode or not raws):
            raise SystemExit(f'training run {name} exited {process.returncode} with {raws} raw profiles')
        return process

    def seconds(self):
        return round(sum(run['seconds'] for run in self.runs), 1)


class Background:
    def __init__(self, training, name, directory, weight, process, continuous=False):
        self.training, self.name, self.directory, self.weight = training, name, directory, weight
        self.process, self.start, self.continuous = process, time.monotonic(), continuous

    def stop(self, timeout=30):
        """Terminate a continuous-mode run (its counts are already in the file) and record it."""
        if not self.continuous:
            raise SystemExit(f'training run {self.name}: stop() loses the counts of a non-continuous run')
        if self.process.poll() is None:
            self.process.terminate()
        try:
            code = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            code = self.process.wait()
        self.training._record(self.name, self.directory, self.weight, self.start, code, 'qemu-user',
                              {'stopped': True})
        return code

    def finish(self, timeout=60, expect=(0,)):
        """Wait for the process; it must exit by itself (atexit writes the profile)."""
        try:
            code = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            raise SystemExit(f'training run {self.name} did not exit within {timeout} s')
        self.training._record(self.name, self.directory, self.weight, self.start, code, 'qemu-user')
        if expect is not None and code not in expect:
            raise SystemExit(f'training run {self.name} exited {code}')
        return code


def raw_profiles(profiles):
    return sorted(Path(profiles).rglob('*.profraw'))


def merge(profdata, training, out):
    """Merge every run's raw profiles with its weight. Returns (profile, raw count)."""
    weights = {run['directory']: run['weight'] for run in training.runs}
    inputs = []
    for raw in raw_profiles(training.profiles):
        weight = weights.get(raw.parent.name, 1)
        inputs.append(f'--weighted-input={weight},{raw}' if weight != 1 else str(raw))
    if not inputs:
        raise SystemExit('training wrote no raw profiles')
    out = Path(out)
    subprocess.run([str(profdata), 'merge', '-o', str(out), *inputs], check=True)
    return out, len(inputs)


# Coverage

_BASIC_TYPES = set('abcdefhijlmnopstuvxyz')


class _Demangle:
    """Enough of the Rust v0 grammar to find the path that defines a symbol."""

    def __init__(self, text):
        self.text, self.pos, self.depth = text, 0, 0

    def peek(self):
        return self.text[self.pos] if self.pos < len(self.text) else ''

    def take(self):
        if self.pos >= len(self.text):
            raise ValueError('truncated symbol')
        char = self.text[self.pos]
        self.pos += 1
        return char

    def base62(self):
        if self.peek() == '_':
            self.pos += 1
            return 0
        value = 0
        while self.peek() != '_':
            char = self.take()
            digit = ('0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ').find(char)
            if digit < 0:
                raise ValueError('bad base-62 number')
            value = value * 62 + digit
        self.pos += 1
        return value + 1

    def decimal(self):
        start = self.pos
        while self.peek().isdigit():
            self.pos += 1
        if start == self.pos:
            raise ValueError('expected a number')
        return int(self.text[start:self.pos])

    def disambiguator(self):
        if self.peek() == 's':
            self.pos += 1
            self.base62()

    def identifier(self):
        self.disambiguator()
        punycode = self.peek() == 'u'
        if punycode:
            self.pos += 1
        length = self.decimal()
        if self.peek() == '_':
            self.pos += 1
        name = self.text[self.pos:self.pos + length]
        if len(name) != length:
            raise ValueError('truncated identifier')
        self.pos += length
        return name

    def backref(self, parse):
        target = self.base62() + 2  # offsets count from after "_R"
        saved = self.pos
        self.pos = target
        self.depth += 1
        if self.depth > 64:
            raise ValueError('backreference loop')
        try:
            return parse()
        finally:
            self.depth -= 1
            self.pos = saved

    def path(self):
        """Returns the defining path as [(namespace, name), ...], crate first."""
        tag = self.take()
        if tag == 'C':
            return [('C', self.identifier())]
        if tag == 'N':
            namespace = self.take()
            parent = self.path()
            name = self.identifier()
            return parent + [(namespace, name or '{' + namespace + '}')]
        if tag in 'MX':
            # An impl: its module, then the implementing type's name.
            self.disambiguator()
            parent = self.path()
            implementing = self.type()
            if tag == 'X':
                self.path()
            return parent + ([('t', implementing[-1][1])] if implementing else [])
        if tag == 'Y':
            self.type()
            return self.path()
        if tag == 'I':
            parent = self.path()
            while self.peek() != 'E':
                self.generic_arg()
            self.pos += 1
            return parent
        if tag == 'B':
            return self.backref(self.path)
        raise ValueError(f'unexpected path tag {tag!r}')

    def generic_arg(self):
        if self.peek() == 'L':
            self.pos += 1
            self.base62()
        elif self.peek() == 'K':
            self.pos += 1
            self.const()
        else:
            self.type()

    def lifetime(self):
        if self.peek() == 'L':
            self.pos += 1
            self.base62()

    def binder(self):
        if self.peek() == 'G':
            self.pos += 1
            self.base62()

    def type(self):
        """Skips a type; returns its path if it is a path type."""
        tag = self.peek()
        if tag in _BASIC_TYPES:
            self.pos += 1
        elif tag in 'CNMXYI':
            return self.path()
        elif tag == 'B':
            self.pos += 1
            return self.backref(self.type)
        else:
            self.pos += 1
            if tag == 'A':
                self.type()
                self.const()
            elif tag in 'SPO':
                self.type()
            elif tag in 'RQ':
                self.lifetime()
                self.type()
            elif tag == 'T':
                while self.peek() != 'E':
                    self.type()
                self.pos += 1
            elif tag == 'F':
                self.binder()
                if self.peek() == 'U':
                    self.pos += 1
                if self.peek() == 'K':
                    self.pos += 1
                    if self.peek() == 'C':
                        self.pos += 1
                    else:
                        self.identifier()
                while self.peek() != 'E':
                    self.type()
                self.pos += 1
                self.type()
            elif tag == 'D':
                self.binder()
                while self.peek() != 'E':
                    self.path()
                    while self.peek() == 'p':
                        self.pos += 1
                        self.identifier()
                        self.type()
                self.pos += 1
                self.lifetime()
            elif tag == 'W':  # pattern type
                self.type()
                self.pattern()
            else:
                raise ValueError(f'unexpected type tag {tag!r}')

    def pattern(self):
        if self.take() == 'R':
            self.const()
            self.const()

    def const(self):
        tag = self.peek()
        if tag == 'p':
            self.pos += 1
        elif tag == 'B':
            self.pos += 1
            self.backref(self.const)
        elif tag in 'RQ':
            self.pos += 1
            self.const()
        elif tag in 'AT':
            self.pos += 1
            while self.peek() != 'E':
                self.const()
            self.pos += 1
        elif tag == 'V':
            self.pos += 1
            self.path()
            kind = self.take()
            if kind in 'TS':
                while self.peek() != 'E':
                    if kind == 'S':
                        self.identifier()
                    self.const()
                self.pos += 1
        else:
            self.type()
            if self.peek() == 'n':
                self.pos += 1
            while self.peek() != '_':
                self.take()
            self.pos += 1


def symbol_path(name):
    """The defining path of a profiled function name, as a list of names.

    Accepts v0 (`_R`) and legacy (`_ZN`) Rust symbols with or without the
    `file;` prefix of local symbols. Other names are their own one-element path.
    """
    name = name.rsplit(';', 1)[-1]
    if name.startswith('_R'):
        try:
            parser = _Demangle(name)
            parser.pos = 2
            if parser.peek().isdigit():
                parser.decimal()
            return [part for _, part in parser.path()]
        except (ValueError, IndexError):
            match = re.search(r'C(?:s[0-9A-Za-z]*_)?(\d+)', name)
            if match:
                length, start = int(match.group(1)), match.end()
                return [name[start:start + length]]
            return [name]
    if name.startswith('_ZN'):
        parts, pos = [], 3
        while pos < len(name) and name[pos].isdigit():
            match = re.match(r'\d+', name[pos:])
            length = int(match.group(0))
            pos += len(match.group(0))
            parts.append(name[pos:pos + length])
            pos += length
        if parts and re.fullmatch(r'h[0-9a-f]{16}', parts[-1]):
            parts.pop()
        return parts or [name]
    return [name]


def parse_show(text):
    """Functions from `llvm-profdata show --all-functions --counts`: [(name, counts)]."""
    functions, name, counts = [], None, None
    for line in text.splitlines():
        if line.startswith('  ') and not line.startswith('   ') and line.endswith(':'):
            if name is not None:
                functions.append((name, counts or []))
            name, counts = line[2:-1], []
            continue
        if name is None:
            continue
        stripped = line.strip()
        for key in ('Function count:', 'Block counts:'):
            if stripped.startswith(key):
                values = stripped[len(key):].strip().strip('[]')
                counts.extend(int(v) for v in values.split(',') if v.strip())
        if not line.startswith('  '):
            functions.append((name, counts or []))
            name = None
    if name is not None:
        functions.append((name, counts or []))
    return functions


def show(profdata, profile):
    return subprocess.check_output([str(profdata), 'show', '--all-functions', '--counts', str(profile)],
                                   text=True, errors='replace')


def coverage_report(functions, hot_paths=None, crates=None):
    """Functions and regions (profile counters) with nonzero counts.

    `crates` limits the per-module breakdown to those crates (all others are
    still counted per crate). `hot_paths` maps a name to a regular expression
    over the `::`-joined defining path; each must match a function with counts.
    """
    def empty():
        return {'functions': 0, 'functions_executed': 0, 'regions': 0, 'regions_executed': 0}

    def add(entry, counts):
        entry['functions'] += 1
        entry['functions_executed'] += any(counts)
        entry['regions'] += len(counts)
        entry['regions_executed'] += sum(1 for c in counts if c)

    total, by_crate, by_module = empty(), {}, {}
    hot = {name: {'pattern': pattern, **empty()} for name, pattern in (hot_paths or {}).items()}
    compiled = {name: re.compile(pattern) for name, pattern in (hot_paths or {}).items()}
    for name, counts in functions:
        path = symbol_path(name)
        crate = path[0]
        add(total, counts)
        add(by_crate.setdefault(crate, empty()), counts)
        if crates is None or crate in crates:
            module = crate + ('::' + path[1] if len(path) > 2 else '')
            add(by_module.setdefault(module, empty()), counts)
        joined = '::'.join(path)
        for hot_name, pattern in compiled.items():
            if pattern.search(joined):
                add(hot[hot_name], counts)
    for entry in hot.values():
        entry['ok'] = entry['functions_executed'] > 0
    return {'total': total, 'crates': dict(sorted(by_crate.items())),
            'modules': dict(sorted(by_module.items())), 'hot_paths': hot,
            'hot_paths_missing': sorted(name for name, entry in hot.items() if not entry['ok'])}


def coverage(profdata, profile, hot_paths=None, crates=None):
    return coverage_report(parse_show(show(profdata, profile)), hot_paths, crates)


def summary(report):
    """The part of a coverage report a release record carries."""
    return {'total': report['total'], 'crates': report['crates'], 'hot_paths': {
        name: {k: entry[k] for k in ('functions', 'functions_executed', 'ok')}
        for name, entry in report['hot_paths'].items()}, 'hot_paths_missing': report['hot_paths_missing']}


# Receipts

def make_receipt(*, program, profile, training_version, compiler, features, coverage_summary, training,
                 started, source_commit, raw_profiles, **extra):
    """A profile's receipt. `started` is the time.monotonic() of the profile step's start."""
    receipt = {
        'schema': SCHEMA, 'program': program, 'profile': Path(profile).name, 'profile_sha256': sha256(profile),
        'training_version': training_version, 'source_commit': source_commit, 'compiler': compiler,
        'features': features, 'feature_set_sha256': feature_set_sha256(features),
        'coverage': coverage_summary, 'raw_profiles': raw_profiles, 'training': training.runs,
        'minutes': {'training': round(training.seconds() / 60, 1),
                    'total': round((time.monotonic() - started) / 60, 1)},
    }
    receipt.update(extra)
    return receipt


def check_receipt(receipt, profile, *, compiler=None, feature_set=None, source_commit=None):
    """Raise ValueError unless the receipt describes `profile` and matches the given build."""
    problems = []
    if receipt.get('schema') != SCHEMA:
        problems.append('receipt schema')
    if receipt.get('profile_sha256') != sha256(profile):
        problems.append('profile SHA-256')
    if feature_set is not None and receipt.get('feature_set_sha256') != feature_set:
        problems.append('feature set')
    if compiler is not None:
        recorded = receipt.get('compiler') or {}
        for key, value in compiler.items():
            if recorded.get(key) != value:
                problems.append('compiler ' + key)
    if source_commit is not None and receipt.get('source_commit') != source_commit:
        problems.append('source commit')
    if problems:
        raise ValueError('profile receipt differs from this build: ' + ', '.join(problems))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='action', required=True)
    flags = sub.add_parser('flags', help='print instrumented-build rustflags, one per line')
    flags.add_argument('--profiles', type=Path, required=True)
    flags.add_argument('--runtime', type=Path, required=True)
    cov = sub.add_parser('coverage', help='coverage report of a merged profile')
    cov.add_argument('--profdata', type=Path, required=True)
    cov.add_argument('profile', type=Path)
    cov.add_argument('--hot', action='append', default=[], metavar='NAME=REGEX')
    cov.add_argument('--crate', action='append', help='list modules of this crate (default: all)')
    args = parser.parse_args()
    if args.action == 'flags':
        print('\n'.join(instrument_flags(args.profiles.resolve(), args.runtime.resolve())))
    else:
        hot = dict(item.split('=', 1) for item in args.hot)
        print(json.dumps(coverage(args.profdata, args.profile, hot, args.crate), indent=1))


if __name__ == '__main__':
    main()
