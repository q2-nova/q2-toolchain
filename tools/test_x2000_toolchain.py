#!/usr/bin/env python3
"""Host controls for the target specification and compiler input admission."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import x2000_toolchain  # noqa: E402

class Spec(unittest.TestCase):
    base = json.loads((ROOT / 'q2/toolchain/mipsel-q2-linux-gnu.json').read_text())

    def test_cpu_and_msa_only(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('Q2_X2000_SCHED', None)
            out = x2000_toolchain.spec(self.base)
        self.assertEqual(out['cpu'], 'x2000')
        self.assertEqual(out['features'], self.base['features'] + ',+msa')
        changed = {k for k in out if out[k] != self.base.get(k)}
        self.assertEqual(changed, {'cpu', 'features'})
        # The X2000 has no 64-bit atomics; the toolchain must not widen this.
        self.assertEqual(out['max-atomic-width'], 32)

    def test_keeps_existing_features(self):
        base = dict(self.base, features=self.base['features'] + ',+nomadd4,+msa')
        with mock.patch.dict(os.environ, {'Q2_X2000_SCHED': '1'}):
            out = x2000_toolchain.spec(base)
        self.assertEqual(out['features'], base['features'])

    def test_generic_scheduling_arm(self):
        with mock.patch.dict(os.environ, {'Q2_X2000_SCHED': '0'}):
            out = x2000_toolchain.spec(self.base)
        self.assertEqual(out['cpu'], 'mips32r5')
        self.assertTrue(out['features'].endswith(',+msa,+x2000'))
        self.assertIn('+x2000', x2000_toolchain.cc_arch({'Q2_X2000_SCHED': '0'}))
        self.assertIn('-march=x2000', x2000_toolchain.cc_arch({}))

    def test_default_on_and_zero_turns_it_off(self):
        self.assertTrue(x2000_toolchain.enabled({}))
        self.assertFalse(x2000_toolchain.enabled({'Q2_X2000': '0'}))
        self.assertTrue(x2000_toolchain.enabled({'Q2_X2000': '1'}))


class Sources(unittest.TestCase):
    def test_rejects_a_checkout_at_a_different_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Q2 Test', '-c',
                            'user.email=test@example.invalid', '-c', 'core.hooksPath=/dev/null',
                            'commit', '-qm', 'fixture', '--allow-empty'], check=True)
            with self.assertRaisesRegex(SystemExit, 'pinned rust_commit'):
                x2000_toolchain.source_checkouts(repo, repo)

    def test_rejects_a_different_source_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            archive = work / 'source.tar.xz'
            archive.write_bytes(b'wrong source')
            with self.assertRaisesRegex(SystemExit, 'SHA-256 differs'):
                x2000_toolchain.fetch(work, archive)

    def test_missing_checkout_fails_before_downloading(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            with self.assertRaisesRegex(SystemExit, 'expected a Git checkout'):
                x2000_toolchain.prepare(work, work / 'missing', work / 'also-missing')
            self.assertEqual(list(work.iterdir()), [])

    def test_stock_receipt_cannot_satisfy_fork_pin(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {'Q2_X2000_WORK': temp}):
            receipt = x2000_toolchain.receipt_path(Path(temp))
            receipt.parent.mkdir()
            receipt.write_text(json.dumps({'source_sha256': x2000_toolchain.PIN['source_sha256']}))
            with self.assertRaisesRegex(SystemExit, 'llvm_commit'):
                x2000_toolchain.installed()


@unittest.skipUnless(shutil.which('zstd'), 'zstd is not installed')
class Handoff(unittest.TestCase):
    """A job's handoff to the next one in the build workflow."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work, self.out = Path(temp.name) / 'work', Path(temp.name) / 'handoff'
        install = self.work / 'install'
        (install / 'bin').mkdir(parents=True)
        (install / 'bin/rustc').write_text('#!/bin/sh\n')
        (install / 'bin/rustc').chmod(0o755)
        (install / 'bin/rust').symlink_to('rustc')
        (install / 'lib').mkdir()
        environ = mock.patch.dict(os.environ, {'CFLAGS': '-O2', 'CXXFLAGS': '-O2'})
        environ.start()
        self.addCleanup(environ.stop)
        x2000_toolchain.write_handoff('install', self.work, self.out)
        self.entries = x2000_toolchain.tree_entries(install)
        shutil.rmtree(install)

    def test_restores_files_modes_and_links(self):
        x2000_toolchain.restore_handoff('install', self.work, self.out)
        self.assertEqual(x2000_toolchain.tree_entries(self.work / 'install'), self.entries)
        self.assertEqual(self.entries['bin/rustc']['mode'], '755')
        self.assertEqual(self.entries['bin/rust'], {'symlink': 'rustc'})

    def test_rejects_a_changed_archive(self):
        with (self.out / 'install.tar.zst').open('ab') as archive:
            archive.write(b'\0')
        with self.assertRaisesRegex(SystemExit, 'SHA-256 differs'):
            x2000_toolchain.restore_handoff('install', self.work, self.out)
        self.assertFalse((self.work / 'install').exists())

    def test_rejects_other_pins_flags_or_stage(self):
        with mock.patch.dict(x2000_toolchain.PIN, llvm_commit='0' * 40):
            with self.assertRaisesRegex(SystemExit, 'differs from this checkout in pin$'):
                x2000_toolchain.restore_handoff('install', self.work, self.out)
        # The install handoff does not depend on the flags; LLVM's does.
        with mock.patch.dict(os.environ, {'CFLAGS': '-O3'}):
            x2000_toolchain.restore_handoff('install', self.work, self.out)
        llvm = self.work / x2000_toolchain.STAGES['llvm']
        (llvm / 'bin').mkdir(parents=True)
        (llvm / 'bin/llvm-config').write_text('llvm')
        x2000_toolchain.write_handoff('llvm', self.work, self.out)
        shutil.rmtree(llvm)
        with mock.patch.dict(os.environ, {'CFLAGS': '-O3'}):
            with self.assertRaisesRegex(SystemExit, 'in flags$'):
                x2000_toolchain.restore_handoff('llvm', self.work, self.out)
        (self.out / 'llvm.json').write_text((self.out / 'install.json').read_text())
        with self.assertRaisesRegex(SystemExit, 'in stage, flags, archive$'):
            x2000_toolchain.restore_handoff('llvm', self.work, self.out)

    def test_rejects_unpacked_files_that_differ_from_the_list(self):
        manifest = json.loads((self.out / 'install.json').read_text())
        del manifest['entries']['bin/rust']
        (self.out / 'install.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(SystemExit, 'unpacked files differ'):
            x2000_toolchain.restore_handoff('install', self.work, self.out)


if __name__ == '__main__':
    unittest.main()
