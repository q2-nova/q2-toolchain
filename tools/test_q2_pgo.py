#!/usr/bin/env python3
"""Controls for the shared PGO driver and the profile runtime step; no compiler or emulator."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import q2_pgo  # noqa: E402
import x2000_toolchain  # noqa: E402

# Symbols as nightly-2026-11-12 writes them (v0), from a host -Cprofile-generate build.
LANG_START = '_RINvNtCsh1oe6ztIXx1_3std2rt10lang_startuECs7emhAfPAEOi_1m'
LOCAL_SHIM = ('m.543b2011600fab80-cgu.0;_RNSNvYNCINvNtCsh1oe6ztIXx1_3std2rt10lang_startuE0INtNtNtCs2F9dQBLtWXO_'
              '4core3ops8function6FnOnceuE9call_once6vtableCs7emhAfPAEOi_1m')
SHOW = '''Counters:
  _RNvNtCs1_4q2nd3gfx4fill:
    Hash: 0x0000000000000001
    Counters: 3
    Block counts: [5, 0, 2]
  _RNvNtCs1_4q2nd3gfx5blend:
    Hash: 0x0000000000000002
    Counters: 1
    Block counts: [0]
  q2nd.cgu.0;_RNvNtNtCs1_4q2nd5audio5mixer3mix:
    Hash: 0x0000000000000003
    Counters: 2
    Function count: 4
    Block counts: [0]
  _RNvNtCs2_9symphonia4read:
    Hash: 0x0000000000000004
    Counters: 1
    Block counts: [9]
Instrumentation level: IR  entry_first = 0  instrument_loop_entries = 0
Functions shown: 4
Total functions: 4
'''


def executable(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class Demangle(unittest.TestCase):
    def test_v0_paths(self):
        self.assertEqual(q2_pgo.symbol_path('_RNvNtCs1_4q2nd3gfx4fill'), ['q2nd', 'gfx', 'fill'])
        self.assertEqual(q2_pgo.symbol_path(LANG_START), ['std', 'rt', 'lang_start'])
        # A trait's default method is attributed to the trait's crate.
        self.assertEqual(q2_pgo.symbol_path(LOCAL_SHIM)[:4], ['core', 'ops', 'function', 'FnOnce'])

    def test_inherent_impl_names_the_type(self):
        # <q2nd::gfx::Canvas>::fill: M impl-path (q2nd::gfx), the type, then the method.
        name = '_RNvMNtCs1_4q2nd3gfxNtB2_6Canvas4fill'
        self.assertEqual(q2_pgo.symbol_path(name), ['q2nd', 'gfx', 'Canvas', 'fill'])

    def test_legacy_and_other_names(self):
        self.assertEqual(q2_pgo.symbol_path('_ZN4q2nd3gfx4fill17h0123456789abcdefE'), ['q2nd', 'gfx', 'fill'])
        self.assertEqual(q2_pgo.symbol_path('main'), ['main'])

    def test_unparsed_v0_falls_back_to_its_crate(self):
        self.assertEqual(q2_pgo.symbol_path('_RNvCs1_4q2nd!'), ['q2nd'])


class Coverage(unittest.TestCase):
    def test_parse_show(self):
        functions = dict(q2_pgo.parse_show(SHOW))
        self.assertEqual(functions['_RNvNtCs1_4q2nd3gfx4fill'], [5, 0, 2])
        self.assertEqual(functions['q2nd.cgu.0;_RNvNtNtCs1_4q2nd5audio5mixer3mix'], [4, 0])
        self.assertEqual(len(functions), 4)

    def test_report_by_crate_module_and_hot_path(self):
        report = q2_pgo.coverage_report(q2_pgo.parse_show(SHOW), {
            'mixer': r'^q2nd::audio::mixer', 'draw': r'^q2nd::gfx::', 'network': r'^q2nd::net::'}, crates={'q2nd'})
        self.assertEqual(report['total'], {'functions': 4, 'functions_executed': 3, 'regions': 7, 'regions_executed': 4})
        self.assertEqual(report['crates']['q2nd']['functions_executed'], 2)
        self.assertEqual(report['modules']['q2nd::gfx'], {'functions': 2, 'functions_executed': 1,
                                                          'regions': 4, 'regions_executed': 2})
        self.assertNotIn('symphonia', report['modules'])
        self.assertTrue(report['hot_paths']['mixer']['ok'])
        self.assertTrue(report['hot_paths']['draw']['ok'])
        self.assertEqual(report['hot_paths_missing'], ['network'])
        summary = q2_pgo.summary(report)
        self.assertEqual(set(summary), {'total', 'crates', 'hot_paths', 'hot_paths_missing'})

    def test_coverage_runs_show(self):
        with mock.patch.object(subprocess, 'check_output', return_value=SHOW) as show:
            report = q2_pgo.coverage(Path('/llvm-profdata'), Path('/x.profdata'))
        self.assertEqual(show.call_args.args[0], ['/llvm-profdata', 'show', '--all-functions', '--counts', '/x.profdata'])
        self.assertEqual(report['total']['functions'], 4)


class Flags(unittest.TestCase):
    def test_instrumented_build_links_the_runtime_whole_then_libc(self):
        flags = q2_pgo.instrument_flags(Path('/p'), Path('/rt.a'))
        self.assertEqual(flags[:2], ['-Cprofile-generate=/p', '-Zno-profiler-runtime'])
        self.assertEqual(flags[2:], ['-Clink-arg=-Wl,--whole-archive', '-Clink-arg=/rt.a',
                                     '-Clink-arg=-Wl,--no-whole-archive', '-Clink-arg=-lc'])
        self.assertEqual(q2_pgo.use_flags(Path('/x.profdata')), ['-Cprofile-use=/x.profdata'])
        continuous = q2_pgo.instrument_flags(Path('/p'), Path('/rt.a'), continuous=True)
        self.assertEqual(continuous[2:4], ['-Cllvm-args=-runtime-counter-relocation', '-Cllvm-args=-disable-vp'])
        self.assertEqual(continuous[4:], flags[2:])

    def test_feature_set_digest_is_the_build_record_formula(self):
        import hashlib
        features = {'q2nd': [], 'q2-portal': []}
        self.assertEqual(q2_pgo.feature_set_sha256(features),
                         hashlib.sha256(json.dumps(features, sort_keys=True).encode()).hexdigest())
        self.assertNotEqual(q2_pgo.feature_set_sha256(features),
                            q2_pgo.feature_set_sha256({'q2nd': ['stall-sampler'], 'q2-portal': []}))


class Fakes(unittest.TestCase):
    """Training, merge and receipts with stand-in emulator and llvm-profdata scripts."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        # qemu-mipsel stand-in: drop "-cpu CPU -L SYSROOT", run the program.
        self.qemu = executable(self.dir / 'qemu-mipsel', '#!/bin/sh\nshift 4\nexec "$@"\n')
        # An "instrumented program": writes a raw profile where LLVM_PROFILE_FILE says.
        self.program = executable(self.dir / 'program', '#!/bin/sh\n'
                                  'f=$(printf %s "$LLVM_PROFILE_FILE" | sed "s/%p/$$/; s/%m/1/")\n'
                                  'printf raw > "$f"\nexit "${EXIT:-0}"\n')
        self.profdata = executable(self.dir / 'llvm-profdata', '#!/bin/sh\n'
                                   'printf "%s\\n" "$@" > "$(dirname "$0")/merge-args"\n'
                                   'while [ "$1" != -o ]; do shift; done\nprintf merged > "$2"\n')
        self.training = q2_pgo.Training(self.dir / 'profiles', self.qemu, self.dir / 'sysroot')

    def test_user_runs_write_raw_profiles_per_run(self):
        self.training.user('decode/mp3', [self.program])
        self.training.user('ui', [self.program], weight=3)
        raws = q2_pgo.raw_profiles(self.training.profiles)
        self.assertEqual([raw.parent.name for raw in raws], ['000-decode-mp3', '001-ui'])
        self.assertEqual([run['weight'] for run in self.training.runs], [1, 3])
        profile, count = q2_pgo.merge(self.profdata, self.training, self.dir / 'out.profdata')
        self.assertEqual((profile.read_text(), count), ('merged', 2))
        args = (self.dir / 'merge-args').read_text().splitlines()
        self.assertIn(str(raws[0]), args)
        self.assertIn(f'--weighted-input=3,{raws[1]}', args)

    def test_failed_run_stops_training(self):
        with self.assertRaisesRegex(SystemExit, 'exited 4'):
            self.training.user('broken', [self.program], env={'EXIT': '4'})

    def test_background_run_is_recorded_when_it_exits(self):
        run = self.training.user_background('daemon', [self.program])
        self.assertEqual(run.finish(timeout=10), 0)
        self.assertEqual(self.training.runs[0]['case'], 'daemon')
        self.assertEqual(len(q2_pgo.raw_profiles(self.training.profiles)), 1)

    def test_continuous_daemon_is_stopped_and_recorded(self):
        daemon = executable(self.dir / 'daemon', '#!/bin/sh\n'
                            'printf "%s" "$LLVM_PROFILE_FILE" > "$(dirname "$0")/pattern"\nexec sleep 30\n')
        run = self.training.user_background('tsrsd', [daemon], continuous=True)
        for _ in range(100):
            if (self.dir / 'pattern').exists():
                break
            time.sleep(0.05)
        self.assertTrue((self.dir / 'pattern').read_text().endswith('/000-tsrsd/%m%c.profraw'))
        self.assertNotEqual(run.stop(timeout=10), 0)
        self.assertTrue(self.training.runs[0]['stopped'])
        plain = self.training.user_background('other', [self.program])
        with self.assertRaisesRegex(SystemExit, 'loses the counts'):
            plain.stop()
        plain.finish(timeout=10)

    def test_merge_refuses_no_profiles(self):
        with self.assertRaisesRegex(SystemExit, 'no raw profiles'):
            q2_pgo.merge(self.profdata, self.training, self.dir / 'out.profdata')

    def test_system_run_collects_guest_profiles(self):
        seen = {}

        def collect(directory):
            seen['directory'] = directory
            (directory / '1-1.profraw').write_text('raw')
        command = [sys.executable, '-c', 'import os; print(os.environ["Q2_PGO_LLVM_PROFILE_FILE"])']
        process = self.training.system('platform', command, collect=collect,
                                       guest_pattern='/state/pgo/%p-%m.profraw', capture_output=True, text=True)
        self.assertEqual(process.stdout.strip(), '/state/pgo/%p-%m.profraw')
        self.assertEqual(self.training.runs[0]['raw_profiles'], 1)
        with self.assertRaisesRegex(SystemExit, '0 raw profiles'):
            self.training.system('empty', [sys.executable, '-c', ''], collect=lambda d: None, guest_pattern='/x')

    def test_receipt_and_check(self):
        self.training.user('ui', [self.program])
        profile, count = q2_pgo.merge(self.profdata, self.training, self.dir / 'q2nd.profdata')
        compiler = {'compiler_receipt_sha256': 'a' * 64, 'rustc_sha256': 'b' * 64}
        features = {'q2nd': []}
        receipt = q2_pgo.make_receipt(program='q2nd', profile=profile, training_version=2, compiler=compiler,
                                      features=features, coverage_summary={}, training=self.training,
                                      started=time.monotonic() - 90, source_commit='c' * 40, raw_profiles=count)
        self.assertEqual(receipt['minutes']['total'], 1.5)
        self.assertEqual(receipt['feature_set_sha256'], q2_pgo.feature_set_sha256(features))
        ok = dict(compiler=compiler, feature_set=q2_pgo.feature_set_sha256(features), source_commit='c' * 40)
        self.assertIs(q2_pgo.check_receipt(receipt, profile, **ok), receipt)
        for change, word in ((dict(feature_set='0' * 64), 'feature set'),
                             (dict(compiler=dict(compiler, rustc_sha256='0' * 64)), 'compiler rustc_sha256'),
                             (dict(source_commit='d' * 40), 'source commit')):
            with self.assertRaisesRegex(ValueError, word):
                q2_pgo.check_receipt(receipt, profile, **{**ok, **change})
        profile.write_text('another profile')
        with self.assertRaisesRegex(ValueError, 'profile SHA-256'):
            q2_pgo.check_receipt(receipt, profile, **ok)


class Runtime(unittest.TestCase):
    """The profile-runtime step with stand-in clang and ar."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.work = self.dir / 'work'
        install = self.work / 'install'
        (install / 'llvm/bin').mkdir(parents=True)
        (install / 'bin').mkdir()
        # The stand-in clang writes its source's name into the object; ar concatenates.
        executable(install / 'llvm/bin/clang', '#!/bin/sh\nwhile [ "$1" != -c ]; do shift; done\n'
                   'src=$2; shift 2; printf "%s\\n" "$(basename "$src")" > "$2"\n')
        executable(install / 'bin/rustc', '#!/bin/sh\n')
        sdk = self.dir / 'sdk'
        (sdk / 'bin').mkdir(parents=True)
        executable(sdk / 'bin/mipsel-buildroot-linux-gnu-ar', '#!/bin/sh\nout=$2; shift 2; cat "$@" > "$out"\n')
        self.sdk = sdk
        source = self.dir / 'src/src/llvm-project/compiler-rt'
        (source / 'lib/profile').mkdir(parents=True)
        (source / 'include').mkdir()
        for name in q2_pgo.PROFILE_SOURCES:
            (source / 'lib/profile' / name).write_text('/* ' + name + ' */\n')
        receipt = {**x2000_toolchain.source_identity(), 'pin': x2000_toolchain.PIN,
                   'rustc_version': 'rustc test', 'files': {'bin/rustc': q2_pgo.sha256(install / 'bin/rustc')}}
        (install / 'x2000-toolchain.json').write_text(json.dumps(receipt))
        shutil.move(str(self.dir / 'src'), str(self.work / 'src'))
        self.install = install

    def build(self):
        args = mock.Mock(sdk=self.sdk, sdk_archive_sha256='e' * 64, llvm=None)
        with mock.patch.dict(os.environ, {'Q2_X2000_WORK': str(self.work)}):
            return x2000_toolchain.profile_runtime(args)

    def test_runtime_is_shipped_with_hashes_in_the_receipt(self):
        record = self.build()
        archive, shipped = q2_pgo.runtime(self.install)
        self.assertEqual(shipped, record)
        self.assertEqual(archive.read_text().split(), list(q2_pgo.PROFILE_SOURCES))
        self.assertEqual(record['llvm_commit'], x2000_toolchain.PIN['llvm_commit'])
        self.assertEqual(set(record['sources']), set(q2_pgo.PROFILE_SOURCES))
        self.assertEqual(record['compiler']['kind'], 'x2000-clang')
        receipt = json.loads((self.install / 'x2000-toolchain.json').read_text())
        self.assertEqual(receipt['profile_runtime'], record)
        self.assertEqual(receipt['files'][q2_pgo.RUNTIME], record['archive_sha256'])
        self.assertIn(q2_pgo.RUNTIME_RECORD, receipt['files'])
        identity = q2_pgo.compiler_identity(self.install, profdata=self.install / 'bin/rustc')
        self.assertEqual(identity['profile_runtime_sha256'], record['archive_sha256'])

    def test_changed_archive_is_refused(self):
        self.build()
        (self.install / q2_pgo.RUNTIME).write_text('changed')
        with self.assertRaisesRegex(SystemExit, 'differs'):
            q2_pgo.runtime(self.install)

    def test_missing_sources_are_refused(self):
        (self.work / 'src/src/llvm-project/compiler-rt/lib/profile/InstrProfilingFile.c').unlink()
        with self.assertRaisesRegex(SystemExit, 'InstrProfilingFile.c'):
            self.build()

    def test_no_runtime_in_compiler(self):
        with self.assertRaisesRegex(SystemExit, 'no profile runtime'):
            q2_pgo.runtime(self.install)


@unittest.skipUnless(os.environ.get('Q2_PGO_TEST_PROFDATA'), 'set Q2_PGO_TEST_PROFDATA to an llvm-profdata '
                     'and Q2_PGO_TEST_PROFILE to a merged profile to read a real profile')
class RealProfile(unittest.TestCase):
    def test_a_real_profile_has_rust_crates(self):
        report = q2_pgo.coverage(Path(os.environ['Q2_PGO_TEST_PROFDATA']), Path(os.environ['Q2_PGO_TEST_PROFILE']))
        self.assertGreater(report['total']['functions_executed'], 0)
        self.assertIn('core', report['crates'])


if __name__ == '__main__':
    unittest.main()
