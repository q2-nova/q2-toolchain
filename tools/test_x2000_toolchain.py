#!/usr/bin/env python3
"""Host controls for the target specification and compiler input admission."""
import json
import os
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


if __name__ == '__main__':
    unittest.main()
